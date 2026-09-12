"""Causal forecasts, scenarios, and rolling execution for Question Four.

This module accepts validated in-memory energy and price arrays. It does not
read attachments, write result workbooks, or expose future actuals to a stage
solver.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math

import numpy as np

from .stage_solver import (
    CHARGE_EFFICIENCY,
    DISCHARGE_EFFICIENCY,
    ENERGY_LIMIT,
    SOC_MAX,
    SOC_MIN,
    StageResult,
    solve_stage,
)


N_TIME = 144
N_STAGE = 4
STAGE_LENGTH = 36
PRICE_FLOOR = 1e-6
INITIAL_SOC = 6000.0
FORMAL_START = date(2025, 2, 1)
LAST_DAY = date(2025, 12, 31)


def _array(name: str, value: np.ndarray, shape: tuple[int, ...], *, positive: bool = False) -> np.ndarray:
    result = np.array(value, dtype=float, copy=True)
    if result.shape != shape or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must be finite with shape {shape}")
    if np.any(result <= 0 if positive else result < 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    result.setflags(write=False)
    return result


def _is_workday(day: date) -> bool:
    return day.weekday() < 5


def _stage(stage: int) -> int:
    if stage not in range(N_STAGE):
        raise ValueError("stage must be 0, 1, 2, or 3")
    return stage * STAGE_LENGTH


def _pv_forecast(hourly_kw: np.ndarray, stage: int) -> np.ndarray:
    tau = _stage(stage)
    hourly_kw = _array("hourly PV forecast", hourly_kw, (24,))
    return np.repeat(hourly_kw, 6)[: N_TIME - tau] / 6.0


def _load_forecast(baseline: np.ndarray, prefix: np.ndarray, stage: int) -> np.ndarray:
    tau = _stage(stage)
    if prefix.shape != (tau,):
        raise ValueError(f"load prefix at stage {stage} must have {tau} intervals")
    bias = 0.0 if stage == 0 else float(
        np.mean(prefix[tau - STAGE_LENGTH : tau] - baseline[tau - STAGE_LENGTH : tau])
    )
    return np.maximum(0.0, baseline[tau:] + bias)


@dataclass(frozen=True)
class DayData:
    """One completed day; load and PV are kWh/interval, price is yuan/kWh."""

    day: date
    load_kwh: np.ndarray
    pv_kwh: np.ndarray
    price: np.ndarray
    pv_forecast_kw: np.ndarray

    def __post_init__(self) -> None:
        if not isinstance(self.day, date):
            raise TypeError("day must be a datetime.date")
        object.__setattr__(self, "load_kwh", _array("load_kwh", self.load_kwh, (N_TIME,)))
        object.__setattr__(self, "pv_kwh", _array("pv_kwh", self.pv_kwh, (N_TIME,)))
        object.__setattr__(self, "price", _array("price", self.price, (N_TIME,), positive=True))
        object.__setattr__(
            self, "pv_forecast_kw", _array("pv_forecast_kw", self.pv_forecast_kw, (N_STAGE, 24))
        )


@dataclass(frozen=True)
class ModelConfig:
    mip_gap: float = 1e-6
    time_limit: float | None = None
    cvar_alpha: float = 0.90
    risk_weight: float = 0.10
    distance_weight: float = 0.10
    reserve_quantile: float | None = None
    terminal_value_weight: float = 0.0
    objective_mode: str = "lexicographic"

    def __post_init__(self) -> None:
        if not 0 <= self.mip_gap < 1 or not math.isfinite(self.mip_gap):
            raise ValueError("mip_gap must be finite in [0, 1)")
        if self.time_limit is not None and (
            not math.isfinite(self.time_limit) or self.time_limit <= 0
        ):
            raise ValueError("time_limit must be positive")
        if not 0 < self.cvar_alpha < 1 or not math.isfinite(self.cvar_alpha):
            raise ValueError("cvar_alpha must be finite in (0, 1)")
        if not math.isfinite(self.risk_weight) or self.risk_weight < 0:
            raise ValueError("risk_weight must be finite and nonnegative")
        if not math.isfinite(self.distance_weight) or self.distance_weight < 0:
            raise ValueError("distance_weight must be finite and nonnegative")
        if self.reserve_quantile is not None and (
            not math.isfinite(self.reserve_quantile) or not 0 <= self.reserve_quantile <= 1
        ):
            raise ValueError("reserve_quantile must be in [0, 1]")
        if not math.isfinite(self.terminal_value_weight) or not 0 <= self.terminal_value_weight <= 1:
            raise ValueError("terminal_value_weight must be in [0, 1]")
        if self.objective_mode not in {"primary", "lexicographic"}:
            raise ValueError("objective_mode must be primary or lexicographic")


@dataclass(frozen=True)
class StageForecast:
    day: date
    stage: int
    price: np.ndarray
    load_hat: np.ndarray
    pv_hat: np.ndarray
    scenario_load: np.ndarray
    scenario_pv: np.ndarray
    probabilities: np.ndarray
    representative_days: tuple[date, ...]
    price_bias: float


@dataclass(frozen=True)
class StageRecord:
    forecast: StageForecast
    solution: StageResult


@dataclass(frozen=True)
class DayResult:
    day: date
    strategy: str
    initial_grid: np.ndarray
    grid: np.ndarray
    charge: np.ndarray
    discharge: np.ndarray
    soc: np.ndarray
    emergency: np.ndarray
    plan_cost: float
    adjustment_cost: float
    emergency_cost: float
    total_cost: float
    stages: tuple[StageRecord, ...]

    @property
    def up(self) -> np.ndarray:
        return np.maximum(0.0, self.grid - self.initial_grid)

    @property
    def down(self) -> np.ndarray:
        return np.maximum(0.0, self.initial_grid - self.grid)

    def verify(self, actual: DayData) -> None:
        if self.day != actual.day:
            raise ValueError("result and actual day differ")
        if any(x.shape != (N_TIME,) for x in (
            self.initial_grid, self.grid, self.charge, self.discharge, self.emergency
        )) or self.soc.shape != (N_TIME + 1,):
            raise AssertionError("result arrays have invalid shape")
        if not all(np.all(np.isfinite(x)) for x in (
            self.initial_grid, self.grid, self.charge, self.discharge, self.emergency, self.soc
        )):
            raise AssertionError("result contains non-finite values")
        if np.min(self.grid) < -1e-7 or np.min(self.emergency) < -1e-7:
            raise AssertionError("negative grid or emergency purchase")
        if np.max(self.charge) > ENERGY_LIMIT + 1e-6 or np.max(self.discharge) > ENERGY_LIMIT + 1e-6:
            raise AssertionError("battery power limit exceeded")
        if np.any((self.charge > 1e-9) & (self.discharge > 1e-9)):
            raise AssertionError("simultaneous charging and discharging")
        recurrence = self.soc[1:] - self.soc[:-1] - CHARGE_EFFICIENCY * self.charge + self.discharge / DISCHARGE_EFFICIENCY
        if np.max(np.abs(recurrence)) > 2e-5 or np.min(self.soc) < SOC_MIN - 1e-6 or np.max(self.soc) > SOC_MAX + 1e-6:
            raise AssertionError("SOC path violates recurrence or bounds")
        expected_emergency = np.maximum(
            0.0, actual.load_kwh + self.charge - self.grid - actual.pv_kwh - self.discharge
        )
        if np.max(np.abs(expected_emergency - self.emergency)) > 1e-6:
            raise AssertionError("emergency purchase does not match actual shortfall")
        plan = float(actual.price @ self.initial_grid)
        adjustment = float(actual.price @ (1.5 * self.up + 0.5 * self.down))
        emergency = float(5.0 * actual.price @ self.emergency)
        if max(
            abs(self.plan_cost - plan),
            abs(self.adjustment_cost - adjustment),
            abs(self.emergency_cost - emergency),
            abs(self.total_cost - plan - adjustment - emergency),
        ) > 1e-4:
            raise AssertionError("settlement components do not add up")


class HistoryModel:
    """Historical features updated only after a simulated day has completed."""

    def __init__(self) -> None:
        self.days: list[DayData] = []
        self.load_baselines: list[np.ndarray] = []
        self.pv_baselines: list[np.ndarray] = []
        self.load_residuals: list[list[np.ndarray]] = [[] for _ in range(N_STAGE)]
        self.pv_residuals: list[list[np.ndarray]] = [[] for _ in range(N_STAGE)]

    def _check_next_day(self, target: date) -> None:
        if self.days and target != self.days[-1].day + timedelta(days=1):
            raise ValueError("days must be appended and solved in consecutive order")

    def _indices(self, target: date, limit: int) -> list[int]:
        history = list(range(len(self.days) - 1, -1, -1))
        same = [h for h in history if _is_workday(self.days[h].day) == _is_workday(target)]
        chosen = same[:limit]
        if len(chosen) < limit:
            chosen.extend(h for h in history if h not in chosen)
        return chosen[:limit]

    def _baseline(self, target: date) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        chosen = self._indices(target, 4)
        if not chosen:
            return np.zeros(N_TIME), np.zeros(N_TIME), np.zeros(N_TIME)
        age = np.array([(target - self.days[h].day).days for h in chosen], dtype=float)
        weights = 0.9 ** age
        weights /= float(np.sum(weights))
        load = np.average(np.stack([self.days[h].load_kwh for h in chosen]), axis=0, weights=weights)
        pv = np.average(np.stack([self.days[h].pv_kwh for h in chosen]), axis=0, weights=weights)
        price = np.average(np.stack([self.days[h].price for h in chosen]), axis=0, weights=weights)
        return load, pv, price

    def append(self, completed: DayData) -> None:
        self._check_next_day(completed.day)
        load_base, pv_base, _ = self._baseline(completed.day)
        for stage in range(N_STAGE):
            tau = _stage(stage)
            load_hat = _load_forecast(load_base, completed.load_kwh[:tau], stage)
            pv_hat = _pv_forecast(completed.pv_forecast_kw[stage], stage)
            self.load_residuals[stage].append(completed.load_kwh[tau:] - load_hat)
            self.pv_residuals[stage].append(completed.pv_kwh[tau:] - pv_hat)
        self.days.append(completed)
        self.load_baselines.append(load_base)
        self.pv_baselines.append(pv_base)

    def stage_forecast(
        self,
        target: date,
        stage: int,
        load_prefix: np.ndarray,
        pv_prefix: np.ndarray,
        price_through_current: np.ndarray,
        pv_release_kw: np.ndarray,
        *,
        distance_weight: float = 0.10,
    ) -> StageForecast:
        self._check_next_day(target)
        tau = _stage(stage)
        load_prefix = _array("load_prefix", load_prefix, (tau,))
        pv_prefix = _array("pv_prefix", pv_prefix, (tau,))
        known_price = _array(
            "price_through_current", price_through_current, (tau + 1,), positive=True
        )
        if not math.isfinite(distance_weight) or distance_weight < 0:
            raise ValueError("distance_weight must be finite and nonnegative")
        load_base, pv_base, price_base = self._baseline(target)
        load_hat = _load_forecast(load_base, load_prefix, stage)
        pv_hat = _pv_forecast(pv_release_kw, stage)
        if not self.days:
            price_base = np.full(N_TIME, known_price[0])
        price_bias = (
            float(known_price[0] - price_base[0])
            if stage == 0 else
            float(np.median(known_price[tau - STAGE_LENGTH : tau] - price_base[tau - STAGE_LENGTH : tau]))
        )
        price = np.maximum(PRICE_FLOOR, price_base[tau:] + price_bias)
        price[0] = known_price[-1]

        candidates = self._indices(target, 60)
        n = len(candidates)
        if n == 0:
            return StageForecast(
                target, stage, price, load_hat, pv_hat,
                load_hat[None, :], pv_hat[None, :], np.array([1.0]), (), price_bias
            )
        if n < 3:
            representatives = candidates
            prior = np.full(n, 1.0 / n)
        else:
            ranked = sorted(
                candidates,
                key=lambda h: (float(np.sum(self.pv_residuals[stage][h])), h),
            )
            groups: list[list[int]] = [[], [], []]
            for rank, h in enumerate(ranked):
                groups[3 * rank // n].append(h)
            representatives = [group[(len(group) - 1) // 2] for group in groups]
            prior = np.array([len(group) / n for group in groups], dtype=float)
        probabilities = prior.copy()
        if stage > 0 and n >= 3:
            current_load_deviation = load_prefix - load_base[:tau]
            current_pv_deviation = pv_prefix - pv_base[:tau]
            hist_load_deviation = np.stack([
                self.days[h].load_kwh[:tau] - self.load_baselines[h][:tau] for h in candidates
            ])
            hist_pv_deviation = np.stack([
                self.days[h].pv_kwh[:tau] - self.pv_baselines[h][:tau] for h in candidates
            ])
            sigma_load = np.maximum(1.0, np.std(hist_load_deviation, axis=0, ddof=0))
            sigma_pv = np.maximum(1.0, np.std(hist_pv_deviation, axis=0, ddof=0))
            deviation_by_day = {h: i for i, h in enumerate(candidates)}
            chosen_rows = [deviation_by_day[h] for h in representatives]
            distance = np.mean(
                ((current_load_deviation - hist_load_deviation[chosen_rows]) / sigma_load) ** 2
                + ((current_pv_deviation - hist_pv_deviation[chosen_rows]) / sigma_pv) ** 2,
                axis=1,
            )
            logits = np.log(prior) - distance_weight * distance
            shifted = np.exp(logits - float(np.max(logits)))
            posterior = shifted / float(np.sum(shifted))
            probabilities = (1.0 - 1e-6) * posterior + 1e-6 * prior
        scenario_load = np.stack([
            np.maximum(0.0, load_hat + self.load_residuals[stage][h]) for h in representatives
        ])
        scenario_pv = np.stack([
            np.maximum(0.0, pv_hat + self.pv_residuals[stage][h]) for h in representatives
        ])
        return StageForecast(
            target, stage, price, load_hat, pv_hat, scenario_load, scenario_pv,
            probabilities, tuple(self.days[h].day for h in representatives), price_bias
        )

    def terminal_reserve(self, target: date, quantile: float | None) -> float:
        self._check_next_day(target)
        if quantile is None or target == LAST_DAY:
            return 0.0
        if not 0 <= quantile <= 1:
            raise ValueError("reserve quantile must be in [0, 1]")
        chosen = self._indices(target + timedelta(days=1), 60)
        if not chosen:
            return 0.0
        shortfalls = np.array([
            np.maximum(
                0.0, self.load_residuals[0][h][:STAGE_LENGTH] - self.pv_residuals[0][h][:STAGE_LENGTH]
            ).sum()
            for h in chosen
        ])
        raw = float(np.quantile(shortfalls, quantile, method="linear")) / DISCHARGE_EFFICIENCY
        return min(SOC_MAX - SOC_MIN, raw)

    def terminal_price(self, target: date, current_price_bias: float) -> float:
        self._check_next_day(target)
        if target == LAST_DAY:
            return 0.0
        chosen = self._indices(target + timedelta(days=1), 60)
        if not chosen:
            return 0.0
        age = np.array([(target - self.days[h].day).days for h in chosen], dtype=float)
        weights = 0.9 ** age
        weights /= float(np.sum(weights))
        early_prices = np.stack([self.days[h].price[:STAGE_LENGTH] for h in chosen])
        baseline = np.average(early_prices, axis=0, weights=weights)
        return float(np.mean(np.maximum(PRICE_FLOOR, baseline + current_price_bias)))


def run_day(
    history: HistoryModel,
    actual: DayData,
    initial_soc: float,
    strategy: str,
    config: ModelConfig = ModelConfig(),
) -> DayResult:
    """Execute one day and append it to history only after settlement."""
    if strategy not in {"4-2", "4-3"}:
        raise ValueError("strategy must be 4-2 or 4-3")
    history._check_next_day(actual.day)
    if not SOC_MIN <= initial_soc <= SOC_MAX:
        raise ValueError("initial SOC lies outside the battery bounds")
    terminal_floor = SOC_MIN + history.terminal_reserve(actual.day, config.reserve_quantile)
    executed_grid = np.full(N_TIME, np.nan)
    executed_charge = np.full(N_TIME, np.nan)
    executed_discharge = np.full(N_TIME, np.nan)
    soc = np.full(N_TIME + 1, np.nan)
    soc[0] = initial_soc
    stage_records: list[StageRecord] = []
    initial_grid: np.ndarray | None = None
    stages = range(N_STAGE) if strategy == "4-3" else range(1)
    for stage in stages:
        tau = _stage(stage)
        end = tau + STAGE_LENGTH if strategy == "4-3" else N_TIME
        forecast = history.stage_forecast(
            actual.day, stage, actual.load_kwh[:tau], actual.pv_kwh[:tau],
            actual.price[: tau + 1], actual.pv_forecast_kw[stage],
            distance_weight=config.distance_weight,
        )
        term_price = history.terminal_price(actual.day, forecast.price_bias)
        solution = solve_stage(
            forecast.price, forecast.load_hat, forecast.pv_hat,
            forecast.scenario_load, forecast.scenario_pv, forecast.probabilities,
            float(soc[tau]), terminal_floor,
            initial_grid=None if stage == 0 else initial_grid[tau:],
            mip_gap=config.mip_gap, time_limit=config.time_limit,
            cvar_alpha=config.cvar_alpha, risk_weight=config.risk_weight,
            terminal_credit_per_soc=config.terminal_value_weight * DISCHARGE_EFFICIENCY * term_price,
            objective_mode=config.objective_mode,
        )
        if stage == 0:
            initial_grid = solution.grid.copy()
        length = end - tau
        executed_grid[tau:end] = solution.grid[:length]
        executed_charge[tau:end] = solution.charge[:length]
        executed_discharge[tau:end] = solution.discharge[:length]
        soc[tau : end + 1] = solution.soc[: length + 1]
        stage_records.append(StageRecord(forecast, solution))
    assert initial_grid is not None
    emergency = np.maximum(
        0.0, actual.load_kwh + executed_charge - executed_grid - actual.pv_kwh - executed_discharge
    )
    up = np.maximum(0.0, executed_grid - initial_grid)
    down = np.maximum(0.0, initial_grid - executed_grid)
    plan_cost = float(actual.price @ initial_grid)
    adjustment_cost = float(actual.price @ (1.5 * up + 0.5 * down))
    emergency_cost = float(5.0 * actual.price @ emergency)
    result = DayResult(
        actual.day, strategy, initial_grid, executed_grid, executed_charge,
        executed_discharge, soc, emergency, plan_cost, adjustment_cost,
        emergency_cost, plan_cost + adjustment_cost + emergency_cost,
        tuple(stage_records),
    )
    result.verify(actual)
    history.append(actual)
    return result


def run_strategy(
    days: list[DayData],
    strategy: str,
    config: ModelConfig = ModelConfig(),
    *,
    initial_soc: float = INITIAL_SOC,
) -> list[DayResult]:
    """Run a strategy chronologically, including January SOC warm-up."""
    if days and days[0].day != date(2025, 1, 1):
        raise ValueError("annual simulation must start on 2025-01-01 for SOC warm-up")
    if any(day.day > LAST_DAY for day in days):
        raise ValueError("annual simulation cannot extend beyond 2025-12-31")
    history = HistoryModel()
    results: list[DayResult] = []
    soc = initial_soc
    for actual in days:
        result = run_day(history, actual, soc, strategy, config)
        results.append(result)
        soc = float(result.soc[-1])
    return results


def formal_results(results: list[DayResult]) -> list[DayResult]:
    return [result for result in results if result.day >= FORMAL_START]
