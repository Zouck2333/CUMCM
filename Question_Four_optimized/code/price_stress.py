"""Fixed-trajectory price-band experiment from model section 11.2.

The dispatch is already executed. Only settlement prices vary; this is not
the robust re-optimization of section 11.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math

import numpy as np

from .model import DayData, DayResult, N_TIME, PRICE_FLOOR, StageForecast


@dataclass(frozen=True)
class PriceBand:
    center: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    known: np.ndarray


@dataclass(frozen=True)
class PriceStressResult:
    gamma: float
    center_cost: float
    worst_cost: float
    worst_price: np.ndarray
    unknown_coverage: float | None


def _historical_prices(prior_days: list[DayData], target: date) -> np.ndarray:
    if any(day.day >= target for day in prior_days):
        raise ValueError("price history must precede the target day")
    if any(a.day >= b.day for a, b in zip(prior_days, prior_days[1:])):
        raise ValueError("prior days must be in chronological order")
    ordered = list(reversed(prior_days))
    same_type = target.weekday() < 5
    chosen = [day for day in ordered if (day.day.weekday() < 5) == same_type][:60]
    if len(chosen) < 60:
        chosen_dates = {day.day for day in chosen}
        chosen.extend(day for day in ordered if day.day not in chosen_dates)
    chosen = chosen[:60]
    return np.stack([day.price for day in chosen]) if chosen else np.empty((0, N_TIME))


def stage_price_band(forecast: StageForecast, prior_days: list[DayData]) -> PriceBand:
    """Price uncertainty visible at one release time, including its fixed current slot."""
    tau = forecast.stage * 36
    center = np.asarray(forecast.price, dtype=float)
    if center.shape != (N_TIME - tau,) or np.any(center <= 0):
        raise ValueError("stage forecast price has invalid shape or values")
    history = _historical_prices(prior_days, forecast.day)[:, tau:]
    if len(history):
        q10 = np.quantile(history, 0.10, axis=0, method="linear")
        q90 = np.quantile(history, 0.90, axis=0, method="linear")
        lower = np.maximum(PRICE_FLOOR, np.minimum(center, q10))
        upper = np.maximum(center, q90)
    else:
        lower = center.copy()
        upper = center.copy()
    known = np.zeros(len(center), dtype=bool)
    known[0] = True
    lower[0] = center[0]
    upper[0] = center[0]
    return PriceBand(center, lower, upper, known)


def executed_price_band(result: DayResult, prior_days: list[DayData]) -> PriceBand:
    """Use the forecast that existed when each interval's dispatch was set."""
    center = np.full(N_TIME, np.nan)
    lower = np.full(N_TIME, np.nan)
    upper = np.full(N_TIME, np.nan)
    known = np.zeros(N_TIME, dtype=bool)
    if result.strategy == "4-2":
        if len(result.stages) != 1:
            raise ValueError("4-2 result must contain one stage")
        stage = stage_price_band(result.stages[0].forecast, prior_days)
        center[:] = stage.center
        lower[:] = stage.lower
        upper[:] = stage.upper
        known[:] = stage.known
    elif result.strategy == "4-3":
        if len(result.stages) != 4:
            raise ValueError("4-3 result must contain four stages")
        for stage_index, record in enumerate(result.stages):
            tau = stage_index * 36
            stage = stage_price_band(record.forecast, prior_days)
            center[tau : tau + 36] = stage.center[:36]
            lower[tau : tau + 36] = stage.lower[:36]
            upper[tau : tau + 36] = stage.upper[:36]
            known[tau : tau + 36] = stage.known[:36]
    else:
        raise ValueError("unknown strategy")
    if not all(np.all(np.isfinite(values)) for values in (center, lower, upper)):
        raise ValueError("executed price band is incomplete")
    return PriceBand(center, lower, upper, known)


def stress_test(
    result: DayResult,
    prior_days: list[DayData],
    gamma: float,
    *,
    actual_price: np.ndarray | None = None,
) -> PriceStressResult:
    """Solve the positive-coefficient budgeted price LP by fractional knapsack."""
    if not math.isfinite(gamma) or gamma < 0:
        raise ValueError("gamma must be finite and nonnegative")
    band = executed_price_band(result, prior_days)
    if result.strategy == "4-2":
        coefficients = result.initial_grid + 5.0 * result.emergency
    else:
        coefficients = (
            result.initial_grid + 1.5 * result.up + 0.5 * result.down
            + 5.0 * result.emergency
        )
    if np.any(coefficients < -1e-8):
        raise ValueError("settlement coefficients must be nonnegative")
    gains = np.maximum(0.0, coefficients * (band.upper - band.center))
    order = np.argsort(-gains, kind="stable")
    fractions = np.zeros(N_TIME)
    remaining = gamma
    for t in order:
        if remaining <= 0 or gains[t] <= 0:
            break
        fractions[t] = min(1.0, remaining)
        remaining -= fractions[t]
    worst_price = band.center + fractions * (band.upper - band.center)
    coverage: float | None = None
    if actual_price is not None:
        actual_price = np.asarray(actual_price, dtype=float)
        if (
            actual_price.shape != (N_TIME,)
            or not np.all(np.isfinite(actual_price))
            or np.any(actual_price <= 0)
        ):
            raise ValueError("actual_price must be a positive finite 144-vector")
        unknown = ~band.known
        coverage = float(np.mean(
            (actual_price[unknown] >= band.lower[unknown] - 1e-10)
            & (actual_price[unknown] <= band.upper[unknown] + 1e-10)
        ))
    return PriceStressResult(
        gamma, float(band.center @ coefficients),
        float(worst_price @ coefficients), worst_price, coverage,
    )
