"""Small causal forecasting models with explicit historical interfaces.

No fitting method accepts a target day's actual future arrays. Price candidate
selection uses complete *past* days, never the current or later day's errors.
"""
from __future__ import annotations

from datetime import date

import numpy as np

PRICE_CANDIDATES = ("legacy", "calendar", "calendar_decay")


def calendar_baseline(
    values: np.ndarray,
    dates: list[date],
    target: date,
    *,
    window: int = 28,
    degree: int = 2,
    floor: float = 1.0,
) -> np.ndarray:
    """Fit log values on recent trend and day-of-week, using only h < target."""
    values = np.asarray(values, dtype=float)
    if values.ndim != 2 or len(values) != len(dates) or not len(values):
        raise ValueError("nonempty historical values and dates must align")
    if any(d >= target for d in dates) or dates != sorted(set(dates)):
        raise ValueError("history must be strictly chronological and before target")
    if not np.all(np.isfinite(values)) or np.any(values < 0):
        raise ValueError("history must be finite and nonnegative")
    if window < 14 or degree not in (1, 2) or floor <= 0:
        raise ValueError("invalid calendar model specification")
    if len(dates) < 14:
        # Same weekly fallback as the validated Q3 model.
        lag = target.toordinal() - 7
        earlier = [i for i, d in enumerate(dates) if d.toordinal() <= lag]
        return values[earlier[-1] if earlier else 0].copy()
    dates_used = dates[-window:]
    time = np.array([(d - target).days / 28.0 for d in dates_used])
    weekdays = np.array([d.weekday() for d in dates_used])
    design = np.column_stack(
        [np.ones(len(dates_used))] + [time ** j for j in range(1, degree + 1)]
        + [(weekdays == j).astype(float) for j in range(6)]
    )
    query = np.array([1.0] + [0.0] * degree
                     + [float(target.weekday() == j) for j in range(6)])
    penalty = np.eye(design.shape[1]) * 0.01
    penalty[0, 0] = 1e-8
    coefficient = np.linalg.solve(
        design.T @ design + penalty,
        design.T @ np.log(np.maximum(floor, values[-window:])),
    )
    predicted = np.exp(query @ coefficient)
    if not np.all(np.isfinite(predicted)):
        raise ValueError("nonfinite calendar prediction")
    return predicted


def price_candidates(
    legacy_base: np.ndarray,
    calendar_base: np.ndarray,
    known: np.ndarray,
    stage: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Future prices from the available prefix, plus each candidate's bias.

    Calendar prices use a fixed 28-day linear trend. Their short-term bias is
    either held constant or decays with a fixed six-hour half-life. The first
    slot is fixed to its already announced price for all candidates.
    """
    tau = 36 * stage
    known = np.asarray(known, dtype=float)
    if stage not in range(4) or known.shape != (tau + 1,):
        raise ValueError("price prefix must end at the current interval")
    if not np.all(np.isfinite(known)) or np.any(known <= 0):
        raise ValueError("known prices must be positive and finite")
    bases = (legacy_base, calendar_base, calendar_base)
    predictions, biases = [], []
    for index, base in enumerate(bases):
        if stage == 0:
            bias = float(known[0] - base[0])
        else:
            bias = float(np.median(known[tau - 36:tau] - base[tau - 36:tau]))
        decay = 2.0 ** (-np.arange(144 - tau) / 36.0) if index == 2 else 1.0
        price = np.maximum(1e-6, base[tau:] + bias * decay)
        price[0] = known[-1]
        predictions.append(price)
        biases.append(bias)
    return np.stack(predictions), np.array(biases)


def choose_price_candidate(past_errors: list[np.ndarray], window: int = 28) -> int:
    if len(past_errors) < 7:
        return 0
    # Deterministic tie order favors the established legacy predictor.
    return int(np.argmin(np.mean(np.stack(past_errors[-window:]), axis=0)))
