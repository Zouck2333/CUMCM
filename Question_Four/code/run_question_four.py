"""Recalculate 4-2 and 4-3 from attachments 2/3/4 with resumable checkpoints."""

from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import numpy as np

from .input_data import LoadedInputs, load_inputs
from .model import (
    DayData,
    DayResult,
    FORMAL_START,
    HistoryModel,
    INITIAL_SOC,
    ModelConfig,
    N_TIME,
    run_day,
)


ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = Path(__file__).resolve().parents[1]


def _fingerprint(inputs: LoadedInputs, config: ModelConfig, strategy: str) -> str:
    digest = hashlib.sha256()
    for path in (
        MODEL_DIR / "model_Four.md",
        MODEL_DIR / "code" / "model.py",
        MODEL_DIR / "code" / "stage_solver.py",
        MODEL_DIR / "code" / "input_data.py",
        ROOT / "C题" / "附件" / "附件2.xlsx",
        ROOT / "C题" / "附件" / "附件3.xlsx",
        ROOT / "C题" / "附件" / "附件4.xlsx",
    ):
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    digest.update(repr(config).encode("ascii"))
    digest.update(strategy.encode("ascii"))
    digest.update(str(len(inputs.days)).encode("ascii"))
    return digest.hexdigest()


def _record(result: DayResult, key: str) -> dict:
    prices = np.empty(N_TIME)
    if result.strategy == "4-2":
        prices[:] = result.stages[0].forecast.price
    else:
        for k, stage in enumerate(result.stages):
            prices[k * 36 : (k + 1) * 36] = stage.forecast.price[:36]
    return {
        "run_fingerprint": key,
        "date": result.day.isoformat(),
        "strategy": result.strategy,
        "initial_purchase": result.initial_grid.tolist(),
        "final_purchase": result.grid.tolist(),
        "charge": result.charge.tolist(),
        "discharge": result.discharge.tolist(),
        "soc_path": result.soc.tolist(),
        "soc_start": float(result.soc[0]),
        "soc_end": float(result.soc[-1]),
        "emergency": result.emergency.tolist(),
        "forecast_price_at_execution": prices.tolist(),
        "plan_cost": result.plan_cost,
        "adjustment_cost": result.adjustment_cost,
        "emergency_cost": result.emergency_cost,
        "total_cost": result.total_cost,
        "stage_gaps": [stage.solution.stage_gaps for stage in result.stages],
        "stage_seconds": [stage.solution.solve_seconds for stage in result.stages],
    }


def _result_from_record(record: dict, actual: DayData, strategy: str) -> DayResult:
    if record.get("date") != actual.day.isoformat() or record.get("strategy") != strategy:
        raise ValueError("checkpoint date or strategy does not match source data")
    result = DayResult(
        actual.day, strategy,
        np.asarray(record["initial_purchase"], dtype=float),
        np.asarray(record["final_purchase"], dtype=float),
        np.asarray(record["charge"], dtype=float),
        np.asarray(record["discharge"], dtype=float),
        np.asarray(record["soc_path"], dtype=float),
        np.asarray(record["emergency"], dtype=float),
        float(record["plan_cost"]),
        float(record["adjustment_cost"]),
        float(record["emergency_cost"]),
        float(record["total_cost"]),
        (),
    )
    result.verify(actual)
    if not math.isclose(float(record["soc_start"]), float(result.soc[0]), abs_tol=1e-6):
        raise ValueError("checkpoint opening SOC does not match the path")
    if not math.isclose(float(record["soc_end"]), float(result.soc[-1]), abs_tol=1e-6):
        raise ValueError("checkpoint closing SOC does not match the path")
    price_forecast = np.asarray(record["forecast_price_at_execution"], dtype=float)
    if price_forecast.shape != (N_TIME,) or np.any(price_forecast <= 0):
        raise ValueError("checkpoint price forecast is invalid")
    return result


def _load_checkpoint(path: Path, inputs: LoadedInputs, strategy: str, key: str) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    previous_soc = INITIAL_SOC
    with path.open("r", encoding="utf-8") as stream:
        for index, line in enumerate(stream):
            record = json.loads(line)
            if record.get("run_fingerprint") != key:
                raise ValueError(f"{path}: checkpoint belongs to another model or input set")
            if index >= len(inputs.days):
                raise ValueError(f"{path}: too many checkpoint days")
            result = _result_from_record(record, inputs.days[index], strategy)
            if abs(float(result.soc[0]) - previous_soc) > 1e-5:
                raise ValueError(f"{path}: SOC discontinuity on {result.day}")
            previous_soc = float(result.soc[-1])
            records.append(record)
    return records


def _clock(t: int) -> str:
    if t == N_TIME:
        return "24:00"
    minute = 10 * t
    return f"{minute // 60}:{minute % 60:02d}"


def _write_detail(path: Path, records: list[dict], days: list[DayData]) -> None:
    fields = (
        "date", "t", "interval", "initial_purchase_kwh", "final_purchase_kwh",
        "charge_kwh", "discharge_kwh", "emergency_kwh", "load_kwh", "pv_kwh",
        "soc_before_kwh", "soc_after_kwh", "actual_price_yuan_kwh",
        "forecast_price_yuan_kwh", "plan_cost_yuan", "adjustment_cost_yuan",
        "emergency_cost_yuan", "total_cost_yuan",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        for record, day in zip(records, days, strict=True):
            for t in range(N_TIME):
                p = float(day.price[t])
                g0 = record["initial_purchase"][t]
                g = record["final_purchase"][t]
                emergency = record["emergency"][t]
                plan = p * g0
                adjustment = p * (1.5 * max(0.0, g - g0) + 0.5 * max(0.0, g0 - g))
                emergency_cost = 5.0 * p * emergency
                writer.writerow((
                    record["date"], t, f"{_clock(t)}-{_clock(t+1)}",
                    g0, g, record["charge"][t], record["discharge"][t], emergency,
                    float(day.load_kwh[t]), float(day.pv_kwh[t]),
                    record["soc_path"][t], record["soc_path"][t+1],
                    p, record["forecast_price_at_execution"][t],
                    plan, adjustment, emergency_cost, plan + adjustment + emergency_cost,
                ))


def _summary(records: list[dict], days: list[DayData]) -> dict:
    if len(records) != len(days):
        raise ValueError("summary records and days differ")
    totals = {
        "plan_cost_yuan": 0.0,
        "adjustment_cost_yuan": 0.0,
        "emergency_cost_yuan": 0.0,
        "total_cost_yuan": 0.0,
        "initial_purchase_kwh": 0.0,
        "final_purchase_kwh": 0.0,
        "emergency_kwh": 0.0,
    }
    monthly: dict[str, dict[str, float]] = {}
    selected: dict[str, dict] = {}
    error_sum = 0.0
    error_count = 0
    for record, day in zip(records, days, strict=True):
        month = monthly.setdefault(day.day.strftime("%Y-%m"), {name: 0.0 for name in totals})
        values = {
            "plan_cost_yuan": record["plan_cost"],
            "adjustment_cost_yuan": record["adjustment_cost"],
            "emergency_cost_yuan": record["emergency_cost"],
            "total_cost_yuan": record["total_cost"],
            "initial_purchase_kwh": sum(record["initial_purchase"]),
            "final_purchase_kwh": sum(record["final_purchase"]),
            "emergency_kwh": sum(record["emergency"]),
        }
        for name, value in values.items():
            totals[name] += value
            month[name] += value
        known = {0} if record["strategy"] == "4-2" else {0, 36, 72, 108}
        error_sum += sum(
            abs(float(day.price[t]) - record["forecast_price_at_execution"][t])
            for t in range(N_TIME) if t not in known
        )
        error_count += N_TIME - len(known)
        if record["date"] in {"2025-03-20", "2025-06-21", "2025-09-23", "2025-12-21"}:
            selected[record["date"]] = values
    return {
        "strategy": records[0]["strategy"] if records else None,
        "evaluation_start": records[0]["date"] if records else None,
        "evaluation_end": records[-1]["date"] if records else None,
        "evaluation_days": len(records),
        "units": {"energy": "kWh", "cost": "yuan"},
        "totals": totals,
        "monthly": monthly,
        "selected_dates": selected,
        "forecast_price_mae_unknown_slots": error_sum / error_count if error_count else None,
    }


def _runtime() -> tuple[Path, Path]:
    base = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node"
    node = base / "bin" / "node.exe"
    modules = base / "node_modules"
    if not node.is_file() or not (modules / "@oai" / "artifact-tool").is_dir():
        raise RuntimeError("bundled Node.js or artifact-tool is unavailable")
    return node, modules


def _link_modules(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise
    quoted_link = str(link).replace("'", "''")
    quoted_target = str(target).replace("'", "''")
    subprocess.run(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            f"New-Item -ItemType Junction -Path '{quoted_link}' -Target '{quoted_target}' | Out-Null",
        ],
        check=True, capture_output=True, text=True,
    )


def _write_workbook(records: list[dict], template: Path, output: Path, strategy: str) -> None:
    node, modules = _runtime()
    with tempfile.TemporaryDirectory(prefix="q4_xlsx_") as directory:
        working = Path(directory)
        _link_modules(working / "node_modules", modules)
        script = working / "output_xlsx.mjs"
        shutil.copy2(Path(__file__).with_name("output_xlsx.mjs"), script)
        source = working / "days.json"
        source.write_text(json.dumps(records, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        completed = subprocess.run(
            [str(node), str(script), str(source), str(template), str(output), strategy],
            capture_output=True, text=True, timeout=900,
        )
        if completed.returncode:
            raise RuntimeError((completed.stderr or completed.stdout)[-5000:])
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("artifact-tool did not produce the expected workbook")


def _run_one(
    inputs: LoadedInputs,
    strategy: str,
    output_dir: Path,
    max_days: int,
    config: ModelConfig,
    *,
    export_workbook: bool,
) -> dict:
    key = _fingerprint(inputs, config, strategy)
    checkpoint = output_dir / f"checkpoint_{strategy}.jsonl"
    records = _load_checkpoint(checkpoint, inputs, strategy, key)
    if len(records) > max_days:
        raise ValueError("checkpoint already exceeds max_days")
    history = HistoryModel()
    for day in inputs.days[:len(records)]:
        history.append(day)
    soc = float(records[-1]["soc_end"]) if records else INITIAL_SOC
    started = time.perf_counter()
    with checkpoint.open("a", encoding="utf-8") as stream:
        for index in range(len(records), max_days):
            day = inputs.days[index]
            result = run_day(history, day, soc, strategy, config)
            soc = float(result.soc[-1])
            record = _record(result, key)
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            records.append(record)
            if index < 3 or (index + 1) % 25 == 0 or index + 1 == max_days:
                print(
                    f"{strategy} {index+1}/{max_days} {day.day} "
                    f"cost={result.total_cost:.2f} soc={soc:.3f} "
                    f"elapsed={time.perf_counter()-started:.1f}s",
                    flush=True,
                )
    formal_indices = [i for i, day in enumerate(inputs.days[:max_days]) if day.day >= FORMAL_START]
    formal_records = [records[i] for i in formal_indices]
    formal_days = [inputs.days[i] for i in formal_indices]
    if not formal_records:
        return {"strategy": strategy, "calculated_days": max_days, "formal_days": 0}
    summary = _summary(formal_records, formal_days)
    summary_path = output_dir / f"summary_{strategy}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    detail_path = output_dir / f"detail_{strategy}.csv"
    _write_detail(detail_path, formal_records, formal_days)
    if max_days == len(inputs.days) and export_workbook:
        template = inputs.template_4_2 if strategy == "4-2" else inputs.template_4_3
        _write_workbook(formal_records, template, output_dir / f"result{strategy}.xlsx", strategy)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=("4-2", "4-3", "both"), default="both")
    parser.add_argument("--max-days", type=int, default=365)
    parser.add_argument("--output-dir", type=Path, default=MODEL_DIR / "output")
    parser.add_argument("--no-workbook", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_days <= 365:
        parser.error("--max-days must be in [1, 365]")
    inputs = load_inputs(ROOT)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    config = ModelConfig()
    strategies = ("4-2", "4-3") if args.strategy == "both" else (args.strategy,)
    for strategy in strategies:
        summary = _run_one(
            inputs, strategy, output_dir, args.max_days, config,
            export_workbook=not args.no_workbook,
        )
        print(
            json.dumps(
                {"strategy": strategy, "days": summary.get("evaluation_days", 0),
                 "totals": summary.get("totals")},
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
