from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from input_data import InputData, load_inputs
from milp_stage import StageResult, solve_stage
from load_prediction import calendar_load_baseline


N_TIME = 144
DELTA_H = 1.0 / 6.0
SOC_MIN = 1200.0
SOC_MAX = 10800.0
SOC_INITIAL = 6000.0
BATTERY_LIMIT = 5000.0 / 6.0
ETA_C = ETA_D = 0.9
FORMAL_START = date(2025, 2, 1)
EPS = 1e-6


def _is_workday(day: date) -> bool:
    return day.weekday() < 5


def historical_load_baseline(
    data: InputData, day_index: int, *, forecast_mode: str = "calendar",
    forecast_window: int = 28, forecast_degree: int = 2,
) -> np.ndarray:
    if forecast_mode == "calendar":
        return calendar_load_baseline(
            data.load_kwh, data.dates, day_index,
            window=forecast_window, degree=forecast_degree,
        )
    if forecast_mode != "four_day":
        raise ValueError("forecast_mode must be calendar or four_day")
    if day_index == 0:
        return np.zeros(N_TIME, dtype=float)
    same_type = _is_workday(data.dates[day_index])
    history = list(range(day_index - 1, -1, -1))
    selected = [
        h for h in history if _is_workday(data.dates[h]) == same_type
    ][:4]
    if len(selected) < 4:
        selected.extend(
            h for h in history if h not in selected
        )
        selected = selected[:4]
    selected = selected[: min(4, day_index)]
    offsets = day_index - np.asarray(selected, dtype=float)
    weights = np.power(0.9, offsets)
    weights /= float(np.sum(weights))
    return np.average(data.load_kwh[selected], axis=0, weights=weights)


def load_bias(
    baseline: np.ndarray,
    actual_load_prefix: np.ndarray,
    stage: int,
) -> float:
    tau = 36 * stage
    if actual_load_prefix.shape != (tau,):
        raise ValueError(
            f"阶段{stage}只能看到前{tau}个负载实际值，得到{actual_load_prefix.shape}"
        )
    if stage == 0:
        return 0.0
    return float(np.mean(actual_load_prefix[tau - 36 : tau] - baseline[tau - 36 : tau]))


def load_forecast(
    baseline: np.ndarray,
    actual_load_prefix: np.ndarray,
    stage: int,
) -> np.ndarray:
    tau = 36 * stage
    return np.maximum(0.0, baseline[tau:] + load_bias(baseline, actual_load_prefix, stage))


def pv_forecast(data: InputData, day_index: int, stage: int) -> np.ndarray:
    tau = 36 * stage
    return np.repeat(data.pv_forecast_kw[day_index, stage], 6)[: N_TIME - tau] / 6.0


def append_historical_residuals(
    data: InputData,
    day_index: int,
    baseline: np.ndarray,
    load_residuals: list[list[np.ndarray]],
    pv_residuals: list[list[np.ndarray]],
) -> None:
    for stage in range(4):
        tau = 36 * stage
        forecast_l = load_forecast(baseline, data.load_kwh[day_index, :tau], stage)
        forecast_pv = pv_forecast(data, day_index, stage)
        load_residuals[stage].append(
            np.asarray(data.load_kwh[day_index, tau:] - forecast_l, dtype=float)
        )
        pv_residuals[stage].append(
            np.asarray(data.pv_kwh[day_index, tau:] - forecast_pv, dtype=float)
        )


def candidate_history(data: InputData, day_index: int, scenario_count: int) -> list[int]:
    history = list(range(day_index - 1, -1, -1))
    same = _is_workday(data.dates[day_index])
    selected = [
        h for h in history if _is_workday(data.dates[h]) == same
    ][:60]
    if len(selected) < min(scenario_count, day_index):
        selected.extend(h for h in history if h not in selected)
        selected = selected[: min(scenario_count, day_index)]
    return selected


def select_scenarios(
    data: InputData,
    day_index: int,
    stage: int,
    load_hat: np.ndarray,
    pv_hat: np.ndarray,
    load_residuals: list[list[np.ndarray]],
    pv_residuals: list[list[np.ndarray]],
    historical_baselines: list[np.ndarray],
    current_baseline: np.ndarray,
    *,
    scenario_count: int = 3,
    distance_weight: float = 0.10,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[int]]:
    if scenario_count not in (1, 3, 5):
        raise ValueError("scenario_count must be 1, 3, or 5")
    if not math.isfinite(distance_weight) or distance_weight < 0:
        raise ValueError("distance_weight must be finite and nonnegative")
    if len(historical_baselines) != day_index:
        raise ValueError("historical baselines must contain exactly the past days")
    if day_index == 0:
        return load_hat[None, :].copy(), pv_hat[None, :].copy(), np.array([1.0]), []

    selected = candidate_history(data, day_index, scenario_count)
    n_history = len(selected)
    ranked = (
        sorted(selected)
        if n_history < 3 else
        sorted(selected, key=lambda h: (float(np.sum(pv_residuals[stage][h])), h))
    )
    branches = min(scenario_count, n_history)
    groups: list[list[int]] = [[] for _ in range(branches)]
    for rank, h in enumerate(ranked):
        groups[branches * rank // n_history].append(h)
    medoids = [group[(len(group) - 1) // 2] for group in groups]
    prior = np.asarray([len(group) / n_history for group in groups], dtype=float)
    probabilities = prior.copy()
    if stage > 0 and branches > 1 and distance_weight > 0:
        tau = 36 * stage
        current_prefix = data.load_kwh[day_index, :tau]
        current_bias = load_bias(current_baseline, current_prefix, stage)
        current_error = current_prefix - np.maximum(
            0.0, current_baseline[:tau] + current_bias
        )
        historical_errors = np.stack([
            data.load_kwh[h, :tau] - np.maximum(
                0.0,
                historical_baselines[h][:tau]
                + load_bias(historical_baselines[h], data.load_kwh[h, :tau], stage),
            )
            for h in selected
        ])
        sigma = np.maximum(
            1.0,
            np.sqrt(np.mean(
                (historical_errors - np.mean(historical_errors, axis=0)) ** 2,
                axis=0,
            )),
        )
        by_day = {h: index for index, h in enumerate(selected)}
        distances = np.mean(
            ((current_error - historical_errors[[by_day[h] for h in medoids]]) / sigma) ** 2,
            axis=1,
        )
        if not np.all(np.isfinite(distances)):
            raise ValueError("prefix distances contain non-finite values")
        log_weights = np.log(prior) - distance_weight * distances
        shifted = np.exp(log_weights - float(np.max(log_weights)))
        posterior = shifted / float(np.sum(shifted))
        probabilities = (1.0 - 1e-6) * posterior + 1e-6 * prior
    loads = np.stack(
        [
            np.maximum(0.0, load_hat + load_residuals[stage][h])
            for h in medoids
        ]
    )
    pvs = np.stack(
        [
            np.maximum(0.0, pv_hat + pv_residuals[stage][h])
            for h in medoids
        ]
    )
    if not np.all(np.isfinite(probabilities)) or np.any(probabilities <= 0):
        raise AssertionError("情景概率必须为正且有限")
    if not np.isclose(np.sum(probabilities), 1.0, rtol=0.0, atol=1e-12):
        raise AssertionError("情景概率之和不等于1")
    return loads, pvs, probabilities, medoids


def terminal_reserve(
    data: InputData,
    day_index: int,
    stage: int,
    current_soc: float,
    load_residuals: list[list[np.ndarray]],
    pv_residuals: list[list[np.ndarray]],
    scenario_count: int,
    quantile: float,
) -> tuple[float, float]:
    if not 0 <= quantile <= 1:
        raise ValueError("reserve quantile must lie in [0, 1]")
    selected = candidate_history(data, day_index, scenario_count)
    if len(selected) < 5 or quantile == 0:
        raw = 0.0
    else:
        needs = [
            float(np.sum(np.maximum(
                0.0, load_residuals[stage][h] - pv_residuals[stage][h]
            )))
            for h in selected
        ]
        raw = float(np.quantile(np.asarray(needs), quantile, method="linear")) / ETA_D
    remaining = N_TIME - 36 * stage
    reachable = current_soc + ETA_C * BATTERY_LIMIT * remaining - SOC_MIN
    reserve = max(0.0, min(raw, SOC_MAX - SOC_MIN, reachable))
    return raw, reserve


def verify_day(
    record: dict[str, Any], data: InputData, day_index: int
) -> dict[str, float]:
    names = ("initial_purchase", "final_purchase", "charge", "discharge", "emergency")
    arrays = {name: np.asarray(record[name], dtype=float) for name in names}
    for name, values in arrays.items():
        if values.shape != (N_TIME,) or not np.all(np.isfinite(values)):
            raise AssertionError(f"{record['date']} {name}长度或数值异常")
        if float(np.min(values)) < -EPS:
            raise AssertionError(f"{record['date']} {name}存在负值")
    charge, discharge = arrays["charge"], arrays["discharge"]
    if float(np.max(charge)) > BATTERY_LIMIT + EPS or float(np.max(discharge)) > BATTERY_LIMIT + EPS:
        raise AssertionError(f"{record['date']} 储能功率越界")
    overlap = np.flatnonzero((charge > EPS) & (discharge > EPS))
    if overlap.size:
        t = int(overlap[0])
        raise AssertionError(
            f"{record['date']} 时段{t}同时充放电: "
            f"充电{charge[t]:.9f}、放电{discharge[t]:.9f} kWh"
        )
    soc = np.asarray(record["soc_path"], dtype=float)
    if soc.shape != (N_TIME + 1,):
        raise AssertionError(f"{record['date']} SOC必须有145个边界值")
    soc_error = float(
        np.max(np.abs(soc[1:] - soc[:-1] - ETA_C * charge + discharge / ETA_D))
    )
    if soc_error > 2e-5 or float(np.min(soc)) < SOC_MIN - EPS or float(np.max(soc)) > SOC_MAX + EPS:
        raise AssertionError(f"{record['date']} SOC递推或范围异常，误差{soc_error}")
    if abs(float(soc[0]) - float(record["soc_start"])) > EPS or abs(float(soc[-1]) - float(record["soc_end"])) > EPS:
        raise AssertionError(f"{record['date']} SOC首尾不一致")
    if day_index == 0 and abs(float(soc[0]) - SOC_INITIAL) > EPS:
        raise AssertionError("2025-01-01 0:00的初始SOC必须为6000 kWh")
    actual_l = data.load_kwh[day_index]
    actual_pv = data.pv_kwh[day_index]
    deficit = actual_l + charge - arrays["final_purchase"] - actual_pv - discharge
    expected_emergency = np.maximum(0.0, deficit)
    emergency_error = float(np.max(np.abs(arrays["emergency"] - expected_emergency)))
    if emergency_error > 2e-5:
        raise AssertionError(f"{record['date']} 紧急购电不等于实际缺口")
    surplus = np.maximum(0.0, -deficit)
    balance = (
        arrays["final_purchase"] + actual_pv + discharge + arrays["emergency"]
        - actual_l - charge - surplus
    )
    balance_error = float(np.max(np.abs(balance)))
    if balance_error > 2e-5:
        raise AssertionError(f"{record['date']} 实际供电平衡误差{balance_error}")
    prices = data.prices
    expected_plan = float(prices @ arrays["initial_purchase"])
    positive = np.maximum(0.0, arrays["final_purchase"] - arrays["initial_purchase"])
    negative = np.maximum(0.0, arrays["initial_purchase"] - arrays["final_purchase"])
    expected_adjustment = float(np.sum(prices * (1.5 * positive + 0.5 * negative)))
    expected_emergency_cost = float(5.0 * prices @ arrays["emergency"])
    for key, expected in (
        ("plan_cost", expected_plan),
        ("adjustment_cost", expected_adjustment),
        ("emergency_cost", expected_emergency_cost),
        ("total_cost", expected_plan + expected_adjustment + expected_emergency_cost),
    ):
        if abs(float(record[key]) - expected) > 0.001:
            raise AssertionError(f"{record['date']} {key}费用不一致")
    return {
        "max_soc_recurrence_error_kwh": soc_error,
        "max_supply_balance_error_kwh": balance_error,
        "max_emergency_error_kwh": emergency_error,
    }


def decision_fingerprint(data: InputData) -> str:
    """Bind resumable results to the exact decisions inputs and model code."""
    digest = hashlib.sha256()
    for name, values in (
        ("prices", data.prices),
        ("load_kwh", data.load_kwh),
        ("pv_kwh", data.pv_kwh),
        ("pv_forecast_kw", data.pv_forecast_kw),
    ):
        array = np.ascontiguousarray(values, dtype="<f8")
        digest.update(name.encode("utf-8"))
        digest.update(json.dumps(array.shape).encode("ascii"))
        digest.update(array.tobytes())
    digest.update("dates".encode("ascii"))
    digest.update("\n".join(day.isoformat() for day in data.dates).encode("ascii"))
    code_dir = Path(__file__).resolve().parent
    for path in (
        code_dir.parent / "model_Three.md",
        code_dir / "input_data.py",
        code_dir / "milp_stage.py",
        code_dir / "run_question_three.py",
        code_dir / "load_prediction.py",
    ):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _record_config(
    strategy: str,
    objective_mode: str,
    mip_gap: float,
    time_limit: float | None,
    fingerprint: str,
    terminal_mode: str,
    scenario_count: int,
    distance_weight: float,
    reserve_quantile: float,
    cvar_alpha: float,
    risk_weight: float,
    forecast_mode: str = "calendar",
    forecast_window: int = 28,
    forecast_degree: int = 2,
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "objective_mode": objective_mode,
        "mip_gap": mip_gap,
        "time_limit": time_limit,
        "terminal_mode": terminal_mode,
        "scenario_count": scenario_count,
        "distance_weight": distance_weight,
        "reserve_quantile": reserve_quantile,
        "cvar_alpha": cvar_alpha,
        "risk_weight": risk_weight,
        "forecast_mode": forecast_mode,
        "forecast_window": forecast_window,
        "forecast_degree": forecast_degree,
        "model_version": "four_stage_causal_mpc_calendar_v5",
        "decision_fingerprint_sha256": fingerprint,
    }


def load_checkpoint(
    path: Path,
    data: InputData,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    for index, record in enumerate(records):
        if record.get("date") != data.dates[index].isoformat():
            raise ValueError(f"{path}中的日期序列不连续")
        if record.get("config") != config:
            raise ValueError(f"{path}与本次参数不同，请另设输出目录")
        verify_day(record, data, index)
        if index and abs(float(record["soc_start"]) - float(records[index - 1]["soc_end"])) > EPS:
            raise ValueError(f"{path}跨日SOC不连续")
    return records


def run_strategy(
    data: InputData,
    strategy: str,
    output_dir: Path,
    *,
    objective_mode: str,
    mip_gap: float,
    time_limit: float | None,
    max_days: int,
    fingerprint: str,
    terminal_mode: str,
    scenario_count: int,
    distance_weight: float,
    reserve_quantile: float,
    cvar_alpha: float,
    risk_weight: float,
    forecast_mode: str = "calendar",
    forecast_window: int = 28,
    forecast_degree: int = 2,
) -> list[dict[str, Any]]:
    if strategy not in {"rolling", "zero_only"}:
        raise ValueError(strategy)
    config = _record_config(
        strategy, objective_mode, mip_gap, time_limit, fingerprint,
        terminal_mode, scenario_count, distance_weight, reserve_quantile,
        cvar_alpha, risk_weight,
        forecast_mode, forecast_window, forecast_degree,
    )
    checkpoint = output_dir / f"checkpoint_{strategy}.jsonl"
    records = load_checkpoint(checkpoint, data, config)
    if len(records) > max_days:
        raise ValueError("已存在的断点天数超过本次 --max-days")
    load_residuals: list[list[np.ndarray]] = [[], [], [], []]
    pv_residuals: list[list[np.ndarray]] = [[], [], [], []]
    historical_baselines: list[np.ndarray] = []
    for day_index in range(len(records)):
        baseline = historical_load_baseline(
            data, day_index, forecast_mode=forecast_mode,
            forecast_window=forecast_window, forecast_degree=forecast_degree,
        )
        append_historical_residuals(
            data, day_index, baseline, load_residuals, pv_residuals
        )
        historical_baselines.append(baseline)
    current_soc = float(records[-1]["soc_end"]) if records else SOC_INITIAL
    for day_index in range(len(records), max_days):
        started = time.perf_counter()
        baseline = historical_load_baseline(
            data, day_index, forecast_mode=forecast_mode,
            forecast_window=forecast_window, forecast_degree=forecast_degree,
        )
        day_name = data.dates[day_index].isoformat()
        initial_grid = np.full(N_TIME, np.nan)
        final_grid = np.full(N_TIME, np.nan)
        charge = np.full(N_TIME, np.nan)
        discharge = np.full(N_TIME, np.nan)
        emergency = np.full(N_TIME, np.nan)
        soc_path = np.full(N_TIME + 1, np.nan)
        soc_path[0] = current_soc
        stages = []
        stage_range = range(4) if strategy == "rolling" else range(1)
        for stage in stage_range:
            tau = 36 * stage
            end = 36 * (stage + 1) if strategy == "rolling" else N_TIME
            load_hat = load_forecast(
                baseline, data.load_kwh[day_index, :tau], stage
            )
            pv_hat = pv_forecast(data, day_index, stage)
            scenario_load, scenario_pv, probs, medoids = select_scenarios(
                data, day_index, stage, load_hat, pv_hat, load_residuals, pv_residuals,
                historical_baselines, baseline,
                scenario_count=scenario_count,
                distance_weight=distance_weight,
            )
            if terminal_mode == "free":
                raw_reserve = reserve = 0.0
                terminal_floor = SOC_MIN
            else:
                raw_reserve, reserve = terminal_reserve(
                    data, day_index, stage, current_soc, load_residuals,
                    pv_residuals, scenario_count, reserve_quantile,
                )
                terminal_floor = SOC_MIN + reserve
            try:
                stage_result: StageResult = solve_stage(
                    data.prices[tau:],
                    load_hat,
                    pv_hat,
                    scenario_load,
                    scenario_pv,
                    probs,
                    current_soc,
                    terminal_floor,
                    None if stage == 0 else initial_grid[tau:].copy(),
                    mip_gap=mip_gap,
                    time_limit=time_limit,
                    cvar_alpha=cvar_alpha,
                    risk_weight=risk_weight,
                    objective_mode=objective_mode,
                )
            except RuntimeError as exc:
                raise RuntimeError(f"{day_name} {strategy} stage={stage}: {exc}") from exc
            executed = end - tau
            if stage == 0:
                initial_grid[:] = stage_result.grid
            if abs(float(stage_result.soc[0]) - current_soc) > EPS:
                raise AssertionError(f"{day_name} 阶段{stage}的计划SOC未接当前SOC")
            final_grid[tau:end] = stage_result.grid[:executed]
            charge[tau:end] = stage_result.charge[:executed]
            discharge[tau:end] = stage_result.discharge[:executed]
            soc_path[tau + 1 : end + 1] = stage_result.soc[1 : executed + 1]
            current_soc = float(soc_path[end])
            actual_net = (
                data.load_kwh[day_index, tau:end]
                + charge[tau:end]
                - final_grid[tau:end]
                - data.pv_kwh[day_index, tau:end]
                - discharge[tau:end]
            )
            emergency[tau:end] = np.maximum(0.0, actual_net)
            stages.append(
                {
                    "stage": stage,
                    "issued_at_hour": 6 * stage,
                    "scenario_count": int(len(probs)),
                    "load_forecast_kwh": load_hat.tolist(),
                    "pv_forecast_kwh": pv_hat.tolist(),
                    "history_representatives": [data.dates[h].isoformat() for h in medoids],
                    "scenario_probabilities": probs.tolist(),
                    "reserve_raw_kwh": raw_reserve,
                    "reserve_kwh": reserve,
                    "terminal_floor_kwh": terminal_floor,
                    "primary_value": stage_result.primary_value,
                    "throughput_kwh": stage_result.throughput,
                    "peak_kw": stage_result.peak_kw,
                    "stage_gaps": stage_result.stage_gaps,
                    "stage_bounds": stage_result.stage_bounds,
                    "stage_objectives": stage_result.stage_objectives,
                    "stage_solve_seconds": stage_result.stage_solve_seconds,
                    "stage_status": stage_result.stage_status,
                    "tolerance_1_yuan": stage_result.tolerance_1,
                    "tolerance_2_kwh": stage_result.tolerance_2,
                    "solve_seconds": stage_result.solve_seconds,
                }
            )
        if np.any(~np.isfinite(final_grid)) or np.any(~np.isfinite(soc_path)):
            raise AssertionError(f"{day_name}未填完144时段与145个SOC状态")
        p = data.prices
        plan_cost = float(p @ initial_grid)
        upward = np.maximum(0.0, final_grid - initial_grid)
        downward = np.maximum(0.0, initial_grid - final_grid)
        adjustment_cost = float(np.sum(p * (1.5 * upward + 0.5 * downward)))
        emergency_cost = float(5.0 * p @ emergency)
        actual_load = data.load_kwh[day_index]
        actual_pv = data.pv_kwh[day_index]
        surplus = np.maximum(
            0.0, final_grid + actual_pv + discharge - actual_load - charge
        )
        record: dict[str, Any] = {
            "config": config,
            "date": day_name,
            "initial_purchase": initial_grid.tolist(),
            "final_purchase": final_grid.tolist(),
            "charge": charge.tolist(),
            "discharge": discharge.tolist(),
            "emergency": emergency.tolist(),
            "surplus": surplus.tolist(),
            "actual_load": actual_load.tolist(),
            "actual_pv": actual_pv.tolist(),
            "price": p.tolist(),
            "soc_path": soc_path.tolist(),
            "soc_start": float(soc_path[0]),
            "soc_end": float(soc_path[-1]),
            "plan_cost": plan_cost,
            "adjustment_cost": adjustment_cost,
            "emergency_cost": emergency_cost,
            "total_cost": plan_cost + adjustment_cost + emergency_cost,
            "stages": stages,
            "solve_seconds": time.perf_counter() - started,
        }
        record["verification"] = verify_day(record, data, day_index)
        if records and abs(record["soc_start"] - records[-1]["soc_end"]) > EPS:
            raise AssertionError(f"{day_name}跨日SOC不连续")
        with checkpoint.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        records.append(record)
        append_historical_residuals(
            data, day_index, baseline, load_residuals, pv_residuals
        )
        historical_baselines.append(baseline)
        print(
            f"{strategy} {day_name}: stages={len(stages)} "
            f"cost={record['total_cost']:.2f} emergency={float(np.sum(emergency)):.2f} "
            f"soc_end={current_soc:.2f} seconds={record['solve_seconds']:.2f}",
            flush=True,
        )
    return records


def compare_strategies(
    rolling: list[dict[str, Any]],
    zero_only: list[dict[str, Any]],
) -> dict[str, Any]:
    fields = ("plan_cost", "adjustment_cost", "emergency_cost", "total_cost")

    def totals(records: list[dict[str, Any]]) -> dict[str, float]:
        return {
            **{key: float(sum(float(r[key]) for r in records)) for key in fields},
            "emergency_kwh": float(
                sum(sum(float(x) for x in r["emergency"]) for r in records)
            ),
            "days": len(records),
        }

    def comparison(
        rolling_records: list[dict[str, Any]],
        zero_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        a, b = totals(rolling_records), totals(zero_records)
        return {
            "rolling": a,
            "zero_only": b,
            "rolling_saving_yuan": b["total_cost"] - a["total_cost"],
            "rolling_saving_fraction": (
                (b["total_cost"] - a["total_cost"]) / b["total_cost"]
                if b["total_cost"] else None
            ),
        }

    rolling_formal = {
        record["date"]: record for record in rolling
        if date.fromisoformat(record["date"]) >= FORMAL_START
    }
    zero_formal = {
        record["date"]: record for record in zero_only
        if date.fromisoformat(record["date"]) >= FORMAL_START
    }
    if set(rolling_formal) != set(zero_formal):
        raise ValueError("对照策略与滚动策略的正式评价日期不一致")
    day_keys = sorted(rolling_formal)
    monthly = {}
    for month in sorted({key[:7] for key in day_keys}):
        month_keys = [key for key in day_keys if key.startswith(month)]
        monthly[month] = comparison(
            [rolling_formal[key] for key in month_keys],
            [zero_formal[key] for key in month_keys],
        )

    def daily_metrics(record: dict[str, Any]) -> dict[str, float]:
        return {
            "initial_purchase_kwh": float(sum(record["initial_purchase"])),
            "final_purchase_kwh": float(sum(record["final_purchase"])),
            "emergency_kwh": float(sum(record["emergency"])),
            "soc_start_kwh": float(record["soc_start"]),
            "soc_end_kwh": float(record["soc_end"]),
            **{key: float(record[key]) for key in fields},
        }

    specified_dates = {}
    for day in ("2025-03-20", "2025-06-21", "2025-09-23", "2025-12-21"):
        if day not in rolling_formal:
            raise ValueError(f"缺少论文指定日期 {day} 的两策略记录")
        rolling_day = daily_metrics(rolling_formal[day])
        zero_day = daily_metrics(zero_formal[day])
        specified_dates[day] = {
            "rolling": rolling_day,
            "zero_only": zero_day,
            "rolling_saving_yuan": zero_day["total_cost"] - rolling_day["total_cost"],
        }

    return {
        "period": "2025-02-01 to 2025-12-31",
        "full_year": comparison(rolling, zero_only),
        "boundary_soc": {
            label: {"initial_soc_kwh": records[0]["soc_start"],
                    "evaluation_initial_soc_kwh": records[31]["soc_start"],
                    "final_soc_kwh": records[-1]["soc_end"]}
            for label, records in (("rolling", rolling), ("zero_only", zero_only))
        },
        **comparison(
            [rolling_formal[key] for key in day_keys],
            [zero_formal[key] for key in day_keys],
        ),
        "monthly": monthly,
        "specified_dates": specified_dates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="C题第三问：四时点滚动三情景MILP与仅0:00预报对照"
    )
    default_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--base-dir", type=Path, default=default_root)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "output_optimized"
    )
    parser.add_argument(
        "--strategy", choices=("rolling", "zero_only", "both"), default="both"
    )
    parser.add_argument(
        "--objective-mode", choices=("lexicographic", "primary"), default="primary"
    )
    parser.add_argument(
        "--terminal-mode", choices=("free", "reserve"), default="free"
    )
    parser.add_argument("--scenario-count", type=int, choices=(1, 3, 5), default=3)
    parser.add_argument("--forecast-mode", choices=("calendar", "four_day"), default="calendar")
    parser.add_argument("--forecast-window", type=int, default=28)
    parser.add_argument("--forecast-degree", type=int, choices=(1, 2), default=2)
    parser.add_argument("--distance-weight", type=float, default=0.10)
    parser.add_argument("--reserve-quantile", type=float, default=0.90)
    parser.add_argument("--cvar-alpha", type=float, default=0.90)
    parser.add_argument("--risk-weight", type=float, default=0.10)
    parser.add_argument("--mip-gap", type=float, default=1e-6)
    parser.add_argument("--time-limit", type=float, default=None)
    parser.add_argument("--max-days", type=int, default=365)
    args = parser.parse_args()
    if not 1 <= args.max_days <= 365:
        parser.error("--max-days 应位于1到365")
    if args.forecast_window < 14:
        parser.error("--forecast-window 必须至少14天")
    if not math.isfinite(args.mip_gap) or not 0 <= args.mip_gap < 1:
        parser.error("--mip-gap 应为[0,1)中的有限数")
    if not math.isfinite(args.distance_weight) or args.distance_weight < 0:
        parser.error("--distance-weight 必须是非负有限数")
    if not math.isfinite(args.reserve_quantile) or not 0 <= args.reserve_quantile <= 1:
        parser.error("--reserve-quantile 应为[0,1]中的有限数")
    if not math.isfinite(args.cvar_alpha) or not 0 < args.cvar_alpha < 1:
        parser.error("--cvar-alpha 应为(0,1)中的有限数")
    if not math.isfinite(args.risk_weight) or args.risk_weight < 0:
        parser.error("--risk-weight 必须是非负有限数")
    if args.time_limit is not None and (
        not math.isfinite(args.time_limit) or args.time_limit <= 0
    ):
        parser.error("--time-limit 必须为正有限数")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    data = load_inputs(args.base_dir)
    fingerprint = decision_fingerprint(data)
    strategies = (
        ("rolling", "zero_only") if args.strategy == "both" else (args.strategy,)
    )
    completed: dict[str, list[dict[str, Any]]] = {}
    for strategy in strategies:
        completed[strategy] = run_strategy(
            data,
            strategy,
            args.output_dir,
            objective_mode=args.objective_mode,
            mip_gap=args.mip_gap,
            time_limit=args.time_limit,
            max_days=args.max_days,
            fingerprint=fingerprint,
            terminal_mode=args.terminal_mode,
            scenario_count=args.scenario_count,
            distance_weight=args.distance_weight,
            reserve_quantile=args.reserve_quantile,
            cvar_alpha=args.cvar_alpha,
            risk_weight=args.risk_weight,
            forecast_mode=args.forecast_mode,
            forecast_window=args.forecast_window,
            forecast_degree=args.forecast_degree,
        )
    if args.max_days == 365:
        rolling = completed.get("rolling")
        official_parameters = (
            args.terminal_mode == "free"
            and args.objective_mode in {"primary", "lexicographic"}
            and args.scenario_count == 3
            and args.distance_weight == 0.10
            and args.cvar_alpha == 0.90
            and args.risk_weight == 0.10
            and args.forecast_mode == "calendar"
            and args.forecast_window == 28
            and args.forecast_degree == 2
        )
        if rolling is not None and official_parameters:
            from output_data import write_outputs

            formal = [
                record for record in rolling
                if date.fromisoformat(record["date"]) >= FORMAL_START
            ]
            write_outputs(formal, data.template_path, args.output_dir)
        elif rolling is not None:
            print(
                "当前参数属于对照或灵敏度实验，仅保留断点记录，不写正式 result3.xlsx。",
                flush=True,
            )
        if "rolling" in completed and "zero_only" in completed:
            comparison = compare_strategies(
                completed["rolling"], completed["zero_only"]
            )
            (args.output_dir / "strategy_comparison.json").write_text(
                json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps({k: v for k, v in comparison.items()
                              if k not in {"monthly", "specified_dates"}},
                             ensure_ascii=False, indent=2), flush=True)
    else:
        print(
            f"仅完成前{args.max_days}天的调试计算；正式result3.xlsx只在全年完成后写出。",
            flush=True,
        )


if __name__ == "__main__":
    main()
