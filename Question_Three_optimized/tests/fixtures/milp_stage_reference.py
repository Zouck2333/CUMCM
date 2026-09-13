"""One causal, remaining-day MILP for Question Three.

All power-flow inputs are kWh per ten-minute interval.  ``solve_stage`` sees
only forecasts and historical scenarios prepared at the current release time;
actual future load and PV are deliberately absent from its interface.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix, csr_matrix, vstack


SOC_MIN = 1200.0
SOC_MAX = 10800.0
ENERGY_LIMIT = 5000.0 / 6.0
CHARGE_EFFICIENCY = 0.9
DISCHARGE_EFFICIENCY = 0.9
DELTA_H = 1.0 / 6.0
CVaR_ALPHA = 0.90
CVaR_WEIGHT = 0.10


@dataclass(frozen=True)
class StageResult:
    """Solution for the remaining intervals, including both SOC boundaries."""

    grid: np.ndarray
    charge: np.ndarray
    discharge: np.ndarray
    soc: np.ndarray
    curtailment: np.ndarray
    primary_value: float
    throughput: float
    peak_kw: float
    stage_gaps: dict[str, float | None]
    stage_bounds: dict[str, float | None]
    stage_objectives: dict[str, float | None]
    stage_solve_seconds: dict[str, float]
    solve_seconds: float
    scenario_emergency: np.ndarray
    stage_status: dict[str, str]
    objective_mode: str
    tolerance_1: float | None = None
    tolerance_2: float | None = None


class _Rows:
    """Build a sparse, two-sided LinearConstraint one row at a time."""

    def __init__(self, n_variables: int) -> None:
        self.n_variables = n_variables
        self.row: list[int] = []
        self.col: list[int] = []
        self.value: list[float] = []
        self.lower: list[float] = []
        self.upper: list[float] = []

    def add(
        self,
        terms: list[tuple[int, float]],
        *,
        lower: float = -math.inf,
        upper: float = math.inf,
    ) -> None:
        row = len(self.lower)
        for column, coefficient in terms:
            if coefficient:
                self.row.append(row)
                self.col.append(int(column))
                self.value.append(float(coefficient))
        self.lower.append(float(lower))
        self.upper.append(float(upper))

    def matrix(self) -> csr_matrix:
        return coo_matrix(
            (self.value, (self.row, self.col)),
            shape=(len(self.lower), self.n_variables),
            dtype=float,
        ).tocsr()


def _array(name: str, value: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.shape != shape:
        raise ValueError(f"{name} must have shape {shape}, got {result.shape}")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} contains a non-finite value")
    if np.any(result < -1e-7):
        raise ValueError(f"{name} contains a negative value")
    return np.maximum(result, 0.0)


def solve_stage(
    price: np.ndarray,
    load_hat: np.ndarray,
    pv_hat: np.ndarray,
    scenario_load: np.ndarray,
    scenario_pv: np.ndarray,
    probabilities: np.ndarray,
    initial_soc: float,
    terminal_floor: float,
    initial_grid: np.ndarray | None = None,
    *,
    mip_gap: float = 1e-6,
    time_limit: float | None = None,
    cvar_alpha: float = CVaR_ALPHA,
    risk_weight: float = CVaR_WEIGHT,
    objective_mode: str = "lexicographic",
) -> StageResult:
    """Solve model sections 6--10 for one forecast-release time.

    ``initial_grid=None`` means the 0:00 stage, where grid is the initial
    purchase plan.  At later stages it is the saved 0:00 plan restricted to
    the remaining intervals.  ``soc`` has length ``n + 1``: its first value is
    the observed SOC at this release time and its last is the 24:00 boundary.
    The terminal SOC is bounded below by `terminal_floor` and is otherwise
    free within the battery capacity.  A time-limited incumbent is diagnostic
    only and raises an error before the next objective.
    """

    price = np.asarray(price, dtype=float)
    if price.ndim != 1 or not 1 <= len(price) <= 144:
        raise ValueError("price must contain 1 to 144 remaining intervals")
    n = len(price)
    price = _array("price", price, (n,))
    load_hat = _array("load_hat", load_hat, (n,))
    pv_hat = _array("pv_hat", pv_hat, (n,))

    scenario_load = np.asarray(scenario_load, dtype=float)
    if scenario_load.ndim != 2 or scenario_load.shape[1] != n:
        raise ValueError("scenario_load must have shape (scenarios, intervals)")
    n_scenarios = scenario_load.shape[0]
    if n_scenarios < 1:
        raise ValueError("at least one scenario is required")
    scenario_load = _array("scenario_load", scenario_load, (n_scenarios, n))
    scenario_pv = _array("scenario_pv", scenario_pv, (n_scenarios, n))
    probabilities = _array("probabilities", probabilities, (n_scenarios,))
    if np.any(probabilities <= 0) or not math.isclose(
        float(np.sum(probabilities)), 1.0, rel_tol=0.0, abs_tol=1e-8
    ):
        raise ValueError("scenario probabilities must be positive and sum to one")

    if initial_grid is not None:
        initial_grid = _array("initial_grid", initial_grid, (n,))
    if not math.isfinite(initial_soc) or not SOC_MIN - 1e-7 <= initial_soc <= SOC_MAX + 1e-7:
        raise ValueError("initial_soc lies outside the battery SOC bounds")
    if not math.isfinite(terminal_floor) or not SOC_MIN <= terminal_floor <= SOC_MAX:
        raise ValueError("terminal_floor lies outside the battery SOC bounds")
    if not math.isfinite(mip_gap) or not 0 <= mip_gap < 1:
        raise ValueError("mip_gap must lie in [0, 1)")
    if time_limit is not None and (not math.isfinite(time_limit) or time_limit <= 0):
        raise ValueError("time_limit must be positive")
    if not math.isfinite(cvar_alpha) or not 0 < cvar_alpha < 1:
        raise ValueError("cvar_alpha must lie in (0, 1)")
    if not math.isfinite(risk_weight) or risk_weight < 0:
        raise ValueError("risk_weight must be finite and nonnegative")
    if objective_mode not in {"primary", "cost", "lexicographic"}:
        raise ValueError("objective_mode must be 'primary' or 'lexicographic'")

    cursor = 0

    def block(size: int) -> np.ndarray:
        nonlocal cursor
        indices = np.arange(cursor, cursor + size, dtype=int)
        cursor += size
        return indices

    grid_idx = block(n)
    charge_idx = block(n)
    discharge_idx = block(n)
    soc_idx = block(n + 1)
    mode_idx = block(n)
    emergency_idx = block(n_scenarios * n).reshape(n_scenarios, n)
    if initial_grid is not None:
        up_idx = block(n)
        down_idx = block(n)
        sign_idx = block(n)
    else:
        up_idx = down_idx = sign_idx = None
    var_idx = block(1)[0]
    tail_idx = block(n_scenarios)
    peak_idx = block(1)[0]
    n_variables = cursor

    lower_bounds = np.zeros(n_variables)
    upper_bounds = np.full(n_variables, math.inf)
    # Prediction balance and 0 <= curtailment <= pv_hat imply this grid bound.
    grid_upper = load_hat + ENERGY_LIMIT
    upper_bounds[grid_idx] = grid_upper
    upper_bounds[charge_idx] = ENERGY_LIMIT
    upper_bounds[discharge_idx] = ENERGY_LIMIT
    lower_bounds[soc_idx] = SOC_MIN
    upper_bounds[soc_idx] = SOC_MAX
    upper_bounds[mode_idx] = 1.0
    if initial_grid is not None:
        # max(G0, Lhat+B) bounds |G-G0| because 0 <= G <= Lhat+B.
        adjustment_bound = np.maximum(initial_grid, grid_upper)
        upper_bounds[up_idx] = adjustment_bound
        upper_bounds[down_idx] = adjustment_bound
        upper_bounds[sign_idx] = 1.0
    lower_bounds[var_idx] = -math.inf

    integrality = np.zeros(n_variables, dtype=np.uint8)
    integrality[mode_idx] = 1
    if sign_idx is not None:
        integrality[sign_idx] = 1

    rows = _Rows(n_variables)
    for t in range(n):
        # Q = G + PVhat + D - Lhat - C is eliminated exactly.
        rows.add(
            [(grid_idx[t], 1), (discharge_idx[t], 1), (charge_idx[t], -1)],
            lower=load_hat[t] - pv_hat[t],
            upper=load_hat[t],
        )

    rows.add([(soc_idx[0], 1)], lower=initial_soc, upper=initial_soc)
    for t in range(n):
        rows.add(
            [
                (soc_idx[t + 1], 1),
                (soc_idx[t], -1),
                (charge_idx[t], -CHARGE_EFFICIENCY),
                (discharge_idx[t], 1.0 / DISCHARGE_EFFICIENCY),
            ],
            lower=0,
            upper=0,
        )
        rows.add(
            [(charge_idx[t], 1), (mode_idx[t], -ENERGY_LIMIT)], upper=0
        )
        rows.add(
            [(discharge_idx[t], 1), (mode_idx[t], ENERGY_LIMIT)],
            upper=ENERGY_LIMIT,
        )
    rows.add([(soc_idx[-1], 1)], lower=terminal_floor)

    for s in range(n_scenarios):
        for t in range(n):
            rows.add(
                [
                    (grid_idx[t], 1),
                    (discharge_idx[t], 1),
                    (charge_idx[t], -1),
                    (emergency_idx[s, t], 1),
                ],
                lower=scenario_load[s, t] - scenario_pv[s, t],
            )

    if initial_grid is not None:
        for t in range(n):
            rows.add(
                [
                    (grid_idx[t], 1),
                    (up_idx[t], -1),
                    (down_idx[t], 1),
                ],
                lower=initial_grid[t],
                upper=initial_grid[t],
            )
            rows.add(
                [(up_idx[t], 1), (sign_idx[t], -adjustment_bound[t])],
                upper=0,
            )
            rows.add(
                [(down_idx[t], 1), (sign_idx[t], adjustment_bound[t])],
                upper=adjustment_bound[t],
            )

    plan_constant = float(np.dot(price, initial_grid)) if initial_grid is not None else 0.0
    scenario_cost = np.zeros((n_scenarios, n_variables))
    for s in range(n_scenarios):
        if initial_grid is None:
            scenario_cost[s, grid_idx] = price
        else:
            scenario_cost[s, up_idx] = 1.5 * price
            scenario_cost[s, down_idx] = 0.5 * price
        scenario_cost[s, emergency_idx[s]] = 5.0 * price
        nonzero = np.flatnonzero(scenario_cost[s])
        cost_terms = [(int(i), float(scenario_cost[s, i])) for i in nonzero]
        cost_terms.extend([(int(var_idx), -1), (int(tail_idx[s]), -1)])
        rows.add(cost_terms, upper=-plan_constant)

    primary_coeff = probabilities @ scenario_cost
    primary_coeff[var_idx] = risk_weight
    primary_coeff[tail_idx] = risk_weight * probabilities / (1.0 - cvar_alpha)
    for s in range(n_scenarios):
        for t in range(n):
            rows.add(
                [
                    (grid_idx[t], 1.0 / DELTA_H),
                    (emergency_idx[s, t], 1.0 / DELTA_H),
                    (peak_idx, -1),
                ],
                upper=0,
            )

    base_matrix = rows.matrix()
    row_lower = np.asarray(rows.lower)
    row_upper = np.asarray(rows.upper)
    bounds = Bounds(lower_bounds, upper_bounds)
    options: dict[str, float | bool] = {"presolve": True, "mip_rel_gap": mip_gap}
    if time_limit is not None:
        options["time_limit"] = time_limit

    stage_gaps: dict[str, float | None] = {}
    stage_bounds: dict[str, float | None] = {}
    stage_objectives: dict[str, float | None] = {}
    stage_solve_seconds: dict[str, float] = {}
    stage_status: dict[str, str] = {}

    def run(
        name: str,
        objective: np.ndarray,
        matrix: csr_matrix,
        row_lo: np.ndarray,
        row_hi: np.ndarray,
    ):
        solve_started = time.perf_counter()
        result = milp(
            c=objective,
            integrality=integrality,
            bounds=bounds,
            constraints=LinearConstraint(matrix, row_lo, row_hi),
            options=options,
        )
        elapsed = time.perf_counter() - solve_started
        gap = getattr(result, "mip_gap", None)
        bound = getattr(result, "mip_dual_bound", None)
        objective_value = getattr(result, "fun", None)
        if result.x is None or result.status != 0:
            raise RuntimeError(
                f"{name} MILP did not prove optimality: status={result.status}; "
                f"incumbent={objective_value}; best_bound={bound}; "
                f"mip_gap={gap}; seconds={elapsed:.3f}; {result.message}"
            )
        x = np.asarray(result.x)
        if not np.all(np.isfinite(x)):
            raise RuntimeError(f"{name} MILP returned non-finite variables")
        activity = matrix @ x
        violation = max(
            float(np.max(row_lo - activity, initial=0.0)),
            float(np.max(activity - row_hi, initial=0.0)),
            float(np.max(lower_bounds - x, initial=0.0)),
            float(np.max(x - upper_bounds, initial=0.0)),
            float(np.max(np.abs(x[integrality == 1] - np.rint(x[integrality == 1])), initial=0.0)),
        )
        if violation > 1e-5:
            raise RuntimeError(
                f"{name} MILP incumbent violates bounds or constraints by {violation:.6g}"
            )
        stage_gaps[name] = float(gap) if gap is not None and math.isfinite(gap) else None
        stage_bounds[name] = float(bound) if bound is not None and math.isfinite(bound) else None
        stage_objectives[name] = (
            float(objective_value)
            if objective_value is not None and math.isfinite(objective_value)
            else None
        )
        stage_solve_seconds[name] = elapsed
        stage_status[name] = f"status={result.status}; {result.message}"
        return result

    started = time.perf_counter()
    first = run("cost_risk", primary_coeff, base_matrix, row_lower, row_upper)
    first_value = float(primary_coeff @ first.x + plan_constant)
    result = first
    final_matrix = base_matrix
    final_lower = row_lower
    final_upper = row_upper
    final_objective = primary_coeff
    tolerance_1: float | None = None
    tolerance_2: float | None = None

    if objective_mode == "lexicographic":
        tolerance_1 = max(1e-4, 1e-7 * abs(first_value))
        throughput_coeff = np.zeros(n_variables)
        throughput_coeff[charge_idx] = 1
        throughput_coeff[discharge_idx] = 1
        second_matrix = vstack(
            [base_matrix, csr_matrix(primary_coeff.reshape(1, -1))], format="csr"
        )
        second_lo = np.append(row_lower, -math.inf)
        # The first-stage solver objective excludes the fixed expected plan cost.
        second_hi = np.append(row_upper, first_value - plan_constant + tolerance_1)
        second = run(
            "throughput", throughput_coeff, second_matrix, second_lo, second_hi
        )
        second_value = float(throughput_coeff @ second.x)
        tolerance_2 = max(1e-6, 1e-7 * abs(second_value))
        third_matrix = vstack(
            [second_matrix, csr_matrix(throughput_coeff.reshape(1, -1))],
            format="csr",
        )
        third_lo = np.append(second_lo, -math.inf)
        third_hi = np.append(second_hi, second_value + tolerance_2)
        peak_coeff = np.zeros(n_variables)
        peak_coeff[peak_idx] = 1
        result = run("peak", peak_coeff, third_matrix, third_lo, third_hi)
        final_matrix = third_matrix
        final_lower = third_lo
        final_upper = third_hi
        final_objective = peak_coeff

    # HiGHS accepts a binary value within its integer tolerance.  With the
    # 833 kWh big-M this can leave ~1e-4 kWh of the forbidden battery action
    # in the returned incumbent.  The tiny overlap is physically impossible,
    # and simply zeroing it can break an active terminal SOC bound.  Fix every
    # binary to its nearest integer and solve the resulting continuous model
    # again, with the same objective and lexicographic locks, when necessary.
    incumbent = np.asarray(result.x)
    overlap = np.minimum(incumbent[charge_idx], incumbent[discharge_idx])
    if np.any(overlap > 1e-10):
        polished_lower = lower_bounds.copy()
        polished_upper = upper_bounds.copy()
        modes = np.rint(incumbent[mode_idx]).astype(int)
        polished_lower[mode_idx] = modes
        polished_upper[mode_idx] = modes
        polished_upper[charge_idx[modes == 0]] = 0.0
        polished_upper[discharge_idx[modes == 1]] = 0.0
        if sign_idx is not None:
            signs = np.rint(incumbent[sign_idx]).astype(int)
            polished_lower[sign_idx] = signs
            polished_upper[sign_idx] = signs
            polished_upper[up_idx[signs == 0]] = 0.0
            polished_upper[down_idx[signs == 1]] = 0.0
        polish_started = time.perf_counter()
        polished = milp(
            c=final_objective,
            integrality=np.zeros(n_variables, dtype=np.uint8),
            bounds=Bounds(polished_lower, polished_upper),
            constraints=LinearConstraint(final_matrix, final_lower, final_upper),
            options={"presolve": True},
        )
        if polished.x is None or polished.status != 0:
            raise RuntimeError(
                "fixed-binary LP could not remove simultaneous charging and "
                f"discharging: status={polished.status}; {polished.message}"
            )
        result = polished
        stage_gaps["fixed_binary_polish"] = 0.0
        stage_bounds["fixed_binary_polish"] = float(polished.fun)
        stage_objectives["fixed_binary_polish"] = float(polished.fun)
        stage_solve_seconds["fixed_binary_polish"] = time.perf_counter() - polish_started
        stage_status["fixed_binary_polish"] = (
            f"status={polished.status}; {polished.message}"
        )

    elapsed = time.perf_counter() - started
    x = np.asarray(result.x)
    grid = x[grid_idx].copy()
    charge = x[charge_idx].copy()
    discharge = x[discharge_idx].copy()
    # Drop only floating-point dust after the fixed-binary solve.  Preserve
    # the solver's bounded SOC variables instead of re-summing hundreds of
    # rounded battery actions: a feasible recurrence residual of ~1e-7 kWh can
    # otherwise accumulate into a returned SOC just above E_max and become an
    # invalid starting point for the next release time.
    for name, values in (("grid", grid), ("charge", charge), ("discharge", discharge)):
        if float(np.min(values)) < -1e-7:
            raise RuntimeError(f"{name} exceeds the solver's nonnegative bound tolerance")
        values[values < 0] = 0.0
        values[values < 1e-10] = 0.0
    if np.any((charge > 0) & (discharge > 0)):
        raise RuntimeError("battery actions still overlap after binary polishing")
    soc = x[soc_idx].copy()
    if abs(float(soc[0]) - initial_soc) > 1e-6:
        raise RuntimeError("returned SOC does not start at the observed SOC")
    soc[0] = initial_soc
    soc[(soc < SOC_MIN) & (soc >= SOC_MIN - 1e-6)] = SOC_MIN
    soc[(soc > SOC_MAX) & (soc <= SOC_MAX + 1e-6)] = SOC_MAX
    recurrence_error = float(
        np.max(
            np.abs(
                soc[1:] - soc[:-1]
                - CHARGE_EFFICIENCY * charge
                + discharge / DISCHARGE_EFFICIENCY
            )
        )
    )
    if (
        float(np.min(soc)) < SOC_MIN - 1e-6
        or float(np.max(soc)) > SOC_MAX + 1e-6
        or soc[-1] < terminal_floor - 1e-6
        or recurrence_error > 1e-6
    ):
        raise RuntimeError(
            "bounded SOC after binary polishing violates recurrence, bounds, "
            f"or terminal floor (recurrence error {recurrence_error:.6g} kWh)"
        )
    curtailment = grid + pv_hat + discharge - load_hat - charge
    if np.min(curtailment) < -1e-5 or np.max(curtailment - pv_hat) > 1e-5:
        raise RuntimeError("reconstructed curtailment violates the PV bounds")
    curtailment = np.maximum(curtailment, 0.0)
    scenario_emergency = np.maximum(
        0.0,
        scenario_load + charge[None, :] - grid[None, :] - scenario_pv - discharge[None, :],
    )
    peak_kw = float(np.max((grid[None, :] + scenario_emergency) / DELTA_H))

    return StageResult(
        grid=grid,
        charge=charge,
        discharge=discharge,
        soc=soc,
        curtailment=curtailment,
        primary_value=float(primary_coeff @ x + plan_constant),
        throughput=float(np.sum(charge + discharge)),
        peak_kw=peak_kw,
        stage_gaps=stage_gaps,
        stage_bounds=stage_bounds,
        stage_objectives=stage_objectives,
        stage_solve_seconds=stage_solve_seconds,
        solve_seconds=elapsed,
        scenario_emergency=scenario_emergency,
        stage_status=stage_status,
        objective_mode=objective_mode,
        tolerance_1=tolerance_1,
        tolerance_2=tolerance_2,
    )
