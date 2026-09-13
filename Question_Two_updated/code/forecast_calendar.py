"""Chronological local regressions; the target day's observations are never read."""
from __future__ import annotations

from datetime import date

import numpy as np


def calendar_load_forecast(
    values: np.ndarray, dates: list[date], target: int,
    *, window: int = 28, degree: int = 2,
) -> np.ndarray:
    """Fit per-period log load to a local trend and six weekday indicators."""
    if target < 1 or target >= len(dates):
        raise ValueError("Target requires at least one completed historical day")
    if window < 14 or degree not in (1, 2):
        raise ValueError("Load window must be >= 14 days and degree 1 or 2")
    if target < 14:
        return values[max(0, target - 7)].copy()
    indices = np.arange(max(0, target - window), target)
    time = (indices - target) / 28.0
    weekdays = np.asarray([dates[k].weekday() for k in indices])
    design = np.column_stack(
        [np.ones(len(indices))] + [time ** j for j in range(1, degree + 1)]
        + [(weekdays == j).astype(float) for j in range(6)]
    )
    query = np.asarray([1.0] + [0.0] * degree
                       + [float(dates[target].weekday() == j) for j in range(6)])
    penalty = np.eye(design.shape[1]) * 0.01
    penalty[0, 0] = 1e-8
    observed = np.log(np.maximum(1.0, values[indices]))
    coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ observed)
    return np.exp(query @ coefficients)


def recent_pv_forecast(values: np.ndarray, target: int, *, window: int = 14) -> np.ndarray:
    """Fit each period's recent PV level and shrink its linear trend."""
    if target < 1 or window < 3:
        raise ValueError("PV forecast requires historical data and a >= 3 day window")
    if target < 3:
        return values[target - 1].copy()
    indices = np.arange(max(0, target - window), target)
    time = (indices - target) / float(window)
    design = np.column_stack((np.ones(len(indices)), time))
    coefficients = np.linalg.solve(
        design.T @ design + np.diag([1e-8, 0.3]), design.T @ values[indices]
    )
    return np.maximum(0.0, coefficients[0])
