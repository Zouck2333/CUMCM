"""Causal calendar/trend load prediction for the third-question dispatcher.

The 28-day, quadratic specification is fixed before this run; no evaluation
costs choose its parameters. Energy inputs and predictions are kWh/10 min.
"""
from __future__ import annotations

from datetime import date

import numpy as np


def calendar_load_baseline(
    values: np.ndarray,
    dates: list[date],
    target: int,
    *,
    window: int = 28,
    degree: int = 2,
) -> np.ndarray:
    if values.ndim != 2 or len(values) != len(dates):
        raise ValueError("load values must have one row per date")
    if not 0 <= target < len(dates):
        raise ValueError("target day lies outside the data")
    if window < 14 or degree not in (1, 2):
        raise ValueError("window must be >=14 days and degree must be 1 or 2")
    if target == 0:
        # Preserve the formal model's cold start; no actual day-zero load.
        return np.zeros(values.shape[1], dtype=float)
    if target < 14:
        return values[max(0, target - 7)].copy()
    indices = np.arange(max(0, target - window), target)
    history = values[indices]
    if not np.all(np.isfinite(history)) or np.any(history < 0):
        raise ValueError("completed historical loads must be finite and nonnegative")
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
    coefficients = np.linalg.solve(
        design.T @ design + penalty,
        design.T @ np.log(np.maximum(1.0, history)),
    )
    prediction = np.exp(query @ coefficients)
    if not np.all(np.isfinite(prediction)):
        raise ValueError("calendar forecast produced non-finite values")
    return prediction
