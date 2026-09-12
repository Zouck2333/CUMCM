"""Independently reconcile Question Three's saved files with source workbooks.

This reads the finished workbook and CSV afresh; it does not call the runner's
in-memory verification or optimization routines.  A successful exit means the
published 334-day tables, checkpoint decisions, source actuals and all cost
aggregates agree within the declared numerical tolerances.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from input_data import load_inputs, load_workbook


FIRST = date(2025, 2, 1)
LAST = date(2025, 12, 31)
ALL_FIRST = date(2025, 1, 1)
N_DAYS = 334
N_TIME = 144
FLOAT_ATOL = 2e-4
PHYSICAL_ATOL = 3e-5
FIELDS = (
    "initial_purchase_kwh", "final_purchase_kwh", "charge_kwh",
    "discharge_kwh", "emergency_kwh", "plan_cost_yuan",
    "adjustment_cost_yuan", "emergency_cost_yuan", "total_cost_yuan",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def close(actual: object, expected: object, location: str, *, atol: float = FLOAT_ATOL) -> None:
    try:
        left, right = float(actual), float(expected)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{location}: missing/non-numeric value {actual!r}") from exc
    require(math.isfinite(left) and math.isfinite(right), f"{location}: non-finite value")
    require(math.isclose(left, right, rel_tol=1e-11, abs_tol=atol),
            f"{location}: saved={left:.10f}, expected={right:.10f}")


def normalized_date(value: object, location: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    raise AssertionError(f"{location}: invalid date {value!r}")


def clock(t: int, *, excel_terminal: bool = False) -> str:
    if t == N_TIME and excel_terminal:
        return "0:00+1"
    h, m = divmod(t * 10, 60)
    return f"{h}:{m:02d}"


def period(t: int, end: int, *, excel_terminal: bool = False) -> str:
    return f"{clock(t)}-{clock(end, excel_terminal=excel_terminal)}"


def emergency_runs(values: list[float]) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    start: int | None = None
    amount = 0.0
    for t in range(N_TIME + 1):
        active = t < N_TIME and values[t] > 1e-8
        if active:
            if start is None:
                start = t
            amount += values[t]
        elif start is not None:
            result.append((period(start, t), amount))
            start, amount = None, 0.0
    return result


def decision_fingerprint(data: Any) -> str:
    """Recreate the persisted input/model/code hash without importing the runner."""
    digest = hashlib.sha256()
    for name, values in (
        ("prices", data.prices), ("load_kwh", data.load_kwh),
        ("pv_kwh", data.pv_kwh), ("pv_forecast_kw", data.pv_forecast_kw),
    ):
        array = np.ascontiguousarray(values, dtype="<f8")
        digest.update(name.encode("utf-8"))
        digest.update(json.dumps(array.shape).encode("ascii"))
        digest.update(array.tobytes())
    digest.update(b"dates")
    digest.update("\n".join(day.isoformat() for day in data.dates).encode("ascii"))
    code_dir = Path(__file__).resolve().parent
    for path in (
        code_dir.parent / "model_Three.md", code_dir / "input_data.py",
        code_dir / "milp_stage.py", code_dir / "run_question_three.py",
    ):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def read_checkpoint(path: Path, label: str, data: Any, fingerprint: str) -> list[dict[str, Any]]:
    require(path.is_file(), f"missing {path}")
    with path.open("r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    require(len(rows) == 365, f"{label} checkpoint has {len(rows)}, expected 365 days")
    first_config = rows[0].get("config", {})
    require(first_config.get("decision_fingerprint_sha256") == fingerprint,
            f"{label}: source/model/code fingerprint does not match checkpoint")
    require(first_config.get("model_version") == "four_stage_causal_mpc_free_soc_v4",
            f"{label}: unexpected model version")
    require(
        first_config.get("terminal_mode") == "free"
        and first_config.get("objective_mode") == "lexicographic"
        and first_config.get("scenario_count") == 3
        and first_config.get("distance_weight") == 0.10
        and first_config.get("cvar_alpha") == 0.90
        and first_config.get("risk_weight") == 0.10,
        f"{label}: checkpoint is not the official three-scenario free-SOC model",
    )
    for i, row in enumerate(rows):
        wanted = ALL_FIRST + timedelta(days=i)
        require(normalized_date(row.get("date"), f"{label} day {i}") == wanted,
                f"{label}: missing or out-of-order date {wanted}")
        require(row.get("config", {}).get("strategy") == label,
                f"{label} {wanted}: wrong strategy configuration")
        require(row.get("config") == first_config,
                f"{label} {wanted}: changed configuration/fingerprint")
        require(len(row["stages"]) == (4 if label == "rolling" else 1),
                f"{label} {wanted}: unexpected number of decision stages")
        for j, stage in enumerate(row["stages"]):
            require(stage.get("stage") == j and stage.get("issued_at_hour") == 6*j,
                    f"{label} {wanted}: wrong stage order or issue time")
            require(stage.get("scenario_count") == len(stage.get("scenario_probabilities", [])),
                    f"{label} {wanted} stage {j}: scenario count mismatch")
            probabilities = stage["scenario_probabilities"]
            require(all(math.isfinite(float(p)) and float(p) > 0 for p in probabilities),
                    f"{label} {wanted} stage {j}: invalid scenario probability")
            close(sum(probabilities), 1.0,
                  f"{label} {wanted} stage {j} probabilities", atol=1e-12)
            close(stage.get("terminal_floor_kwh"), 1200.0,
                  f"{label} {wanted} stage {j} terminal floor", atol=PHYSICAL_ATOL)
            for historical in stage["history_representatives"]:
                require(normalized_date(historical, "historical representative") < wanted,
                        f"{label} {wanted} stage {j}: future history leaked")
            status = stage.get("stage_status")
            gaps = stage.get("stage_gaps")
            bounds = stage.get("stage_bounds")
            objectives = stage.get("stage_objectives")
            durations = stage.get("stage_solve_seconds")
            require(
                all(isinstance(item, dict) for item in (status, gaps, bounds, objectives, durations))
                and set(status) == set(gaps) == set(bounds) == set(objectives) == set(durations),
                    f"{label} {wanted} stage {j}: missing status/gap fields")
            expected_keys = {"cost_risk", "throughput", "peak"}
            require(expected_keys <= set(status),
                    f"{label} {wanted} stage {j}: incomplete lexicographic solve status")
            for key in status:
                require(isinstance(status[key], str) and status[key].startswith("status=0;"),
                        f"{label} {wanted} stage {j} {key}: unsuccessful solve")
                gap = gaps[key]
                require(gap is not None and math.isfinite(float(gap)) and float(gap) >= 0,
                        f"{label} {wanted} stage {j} {key}: unavailable MIP gap")
                require(float(gap) <= float(first_config["mip_gap"]) + 1e-8,
                        f"{label} {wanted} stage {j} {key}: MIP gap exceeds configured limit")
                require(bounds[key] is not None and objectives[key] is not None
                        and math.isfinite(float(bounds[key]))
                        and math.isfinite(float(objectives[key]))
                        and math.isfinite(float(durations[key]))
                        and float(durations[key]) >= 0,
                        f"{label} {wanted} stage {j} {key}: missing solver diagnostics")
            tol1, tol2 = stage.get("tolerance_1_yuan"), stage.get("tolerance_2_kwh")
            require(tol1 is not None and tol2 is not None
                    and math.isfinite(float(tol1)) and math.isfinite(float(tol2))
                    and float(tol1) >= 1e-4 - 1e-10
                    and float(tol2) >= 1e-6 - 1e-12,
                    f"{label} {wanted} stage {j}: missing/invalid lexicographic tolerances")
        for field in ("initial_purchase", "final_purchase", "charge", "discharge",
                      "emergency", "soc_path"):
            require(len(row[field]) == (N_TIME + 1 if field == "soc_path" else N_TIME),
                    f"{label} {wanted}: wrong {field} length")
        close(row["soc_start"], row["soc_path"][0],
              f"{label} {wanted} opening SOC", atol=PHYSICAL_ATOL)
        close(row["soc_end"], row["soc_path"][-1],
              f"{label} {wanted} closing SOC", atol=PHYSICAL_ATOL)
        require(1200 - 1e-6 <= float(row["soc_start"]) <= 10800 + 1e-6,
                f"{label} {wanted}: opening SOC outside bounds")
        costs = {"plan_cost": 0.0, "adjustment_cost": 0.0,
                 "emergency_cost": 0.0}
        for t in range(N_TIME):
            initial = float(row["initial_purchase"][t])
            final = float(row["final_purchase"][t])
            charge = float(row["charge"][t])
            discharge = float(row["discharge"][t])
            emergency = float(row["emergency"][t])
            before = float(row["soc_path"][t])
            after = float(row["soc_path"][t + 1])
            p = float(data.prices[t])
            require(min(initial, final, charge, discharge, emergency) >= -1e-6,
                    f"{label} {wanted} t={t}: negative energy")
            require(charge <= 5000/6 + 1e-6 and discharge <= 5000/6 + 1e-6,
                    f"{label} {wanted} t={t}: battery power limit exceeded")
            require(not (charge > 1e-6 and discharge > 1e-6),
                    f"{label} {wanted} t={t}: simultaneous charge/discharge")
            require(1200 - 1e-6 <= before <= 10800 + 1e-6,
                    f"{label} {wanted} t={t}: SOC outside bounds")
            close(after, before + 0.9*charge - discharge/0.9,
                  f"{label} {wanted} t={t} SOC", atol=PHYSICAL_ATOL)
            deficit = (float(data.load_kwh[i,t]) + charge - final
                       - float(data.pv_kwh[i,t]) - discharge)
            close(emergency, max(0.0, deficit),
                  f"{label} {wanted} t={t} emergency", atol=PHYSICAL_ATOL)
            costs["plan_cost"] += p * initial
            costs["adjustment_cost"] += p * (
                1.5*max(0.0,final-initial) + 0.5*max(0.0,initial-final))
            costs["emergency_cost"] += 5*p*emergency
        require(1200 - 1e-6 <= float(row["soc_end"]) <= 10800 + 1e-6,
                f"{label} {wanted}: ending SOC outside bounds")
        for key, value in costs.items():
            close(row[key], value, f"{label} {wanted} {key}")
        close(row["total_cost"], sum(costs.values()),
              f"{label} {wanted} total_cost")
        if i:
            close(row["soc_start"], rows[i - 1]["soc_end"],
                  f"{label} {wanted} cross-day SOC", atol=PHYSICAL_ATOL)
    close(rows[0]["soc_start"], 6000.0, f"{label} initial SOC")
    return rows


def verify_detail(path: Path, formal: list[dict[str, Any]], data: Any) -> dict[str, float]:
    require(path.is_file(), f"missing {path}")
    totals = {field: 0.0 for field in FIELDS}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "date", "t", "time_period", "initial_purchase_kwh", "final_purchase_kwh",
            "charge_kwh", "discharge_kwh", "emergency_kwh", "actual_load_kwh",
            "actual_pv_kwh", "soc_before_kwh", "soc_after_kwh", "surplus_kwh",
            "price_yuan_per_kwh", "plan_cost_yuan", "adjustment_cost_yuan",
            "emergency_cost_yuan", "total_cost_yuan",
        }
        require(required <= set(reader.fieldnames or []), "detail CSV columns incomplete")
        row_count = 0
        for i, row in enumerate(reader):
            require(i < N_DAYS * N_TIME, "detail CSV has extra rows")
            d, t = divmod(i, N_TIME)
            day = formal[d]
            day_date = FIRST + timedelta(days=d)
            require(row["date"] == day_date.isoformat(), f"CSV row {i+2}: date mismatch")
            require(int(row["t"]) == t, f"CSV row {i+2}: time index mismatch")
            require(row["time_period"] == period(t, t + 1),
                    f"CSV row {i+2}: physical time label mismatch")
            src = d + 31
            p = float(data.prices[t])
            values = {
                "initial_purchase_kwh": float(day["initial_purchase"][t]),
                "final_purchase_kwh": float(day["final_purchase"][t]),
                "charge_kwh": float(day["charge"][t]),
                "discharge_kwh": float(day["discharge"][t]),
                "emergency_kwh": float(day["emergency"][t]),
                "actual_load_kwh": float(data.load_kwh[src, t]),
                "actual_pv_kwh": float(data.pv_kwh[src, t]),
                "soc_before_kwh": float(day["soc_path"][t]),
                "soc_after_kwh": float(day["soc_path"][t + 1]),
                "price_yuan_per_kwh": p,
            }
            values["surplus_kwh"] = max(
                0.0, values["final_purchase_kwh"] + values["actual_pv_kwh"]
                + values["discharge_kwh"] - values["actual_load_kwh"]
                - values["charge_kwh"]
            )
            initial, final, emergency = (values[name] for name in
                                         ("initial_purchase_kwh", "final_purchase_kwh", "emergency_kwh"))
            values["plan_cost_yuan"] = p * initial
            values["adjustment_cost_yuan"] = p * (
                1.5 * max(0.0, final - initial) + 0.5 * max(0.0, initial - final)
            )
            values["emergency_cost_yuan"] = 5 * p * emergency
            values["total_cost_yuan"] = sum(
                values[name] for name in
                ("plan_cost_yuan", "adjustment_cost_yuan", "emergency_cost_yuan")
            )
            for field, expected in values.items():
                close(row[field], expected, f"CSV {day_date} t={t} {field}")
            close(values["soc_after_kwh"],
                  values["soc_before_kwh"] + 0.9 * values["charge_kwh"]
                  - values["discharge_kwh"] / 0.9,
                  f"CSV {day_date} t={t} SOC recurrence", atol=PHYSICAL_ATOL)
            deficit = (values["actual_load_kwh"] + values["charge_kwh"]
                       - values["final_purchase_kwh"] - values["actual_pv_kwh"]
                       - values["discharge_kwh"])
            close(emergency, max(0.0, deficit),
                  f"CSV {day_date} t={t} actual shortfall", atol=PHYSICAL_ATOL)
            require(not (values["charge_kwh"] > 1e-6 and values["discharge_kwh"] > 1e-6),
                    f"CSV {day_date} t={t}: simultaneous battery charge/discharge")
            for field in FIELDS:
                if field in values:
                    totals[field] += values[field]
            row_count += 1
        require(row_count == N_DAYS * N_TIME,
                f"detail CSV has {row_count} data rows, expected {N_DAYS*N_TIME}")
    return totals


def verify_workbook(path: Path, formal: list[dict[str, Any]]) -> dict[str, int]:
    require(path.is_file(), f"missing {path}")
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        require(len(wb.worksheets) == 4, "result3.xlsx must have four worksheets")
        for kind, sheet in (("initial_purchase", wb.worksheets[0]),
                            ("final_purchase", wb.worksheets[1])):
            rows = sheet.iter_rows(min_row=1, min_col=1, max_col=147, values_only=True)
            header = next(rows, None)
            require(header is not None, f"{sheet.title}: missing header")
            for t, value in enumerate(header[1:145]):
                require(value == period(t, t + 1, excel_terminal=True),
                        f"{sheet.title} header {t}: {value!r}")
            for i, day in enumerate(formal):
                excel_row = i + 2
                cells = next(rows, None)
                require(cells is not None, f"{sheet.title}: missing row {excel_row}")
                wanted = FIRST + timedelta(days=i)
                require(normalized_date(cells[0], f"{sheet.title} A{excel_row}") == wanted,
                        f"{sheet.title} row {excel_row}: date mismatch")
                for t, value in enumerate(day[kind]):
                    close(cells[t + 1], value, f"{sheet.title} {wanted} t={t}")
                close(cells[145], sum(day[kind]), f"{sheet.title} {wanted} EP")
                cost = day["plan_cost"] if kind == "initial_purchase" else day["total_cost"]
                close(cells[146], cost, f"{sheet.title} {wanted} EQ")
            for row in rows:
                require(all(v is None for v in row),
                        f"{sheet.title}: nonempty data beyond 2025-12-31")

        storage = wb.worksheets[2]
        storage_rows = storage.iter_rows(min_row=1, min_col=1, max_col=6, values_only=True)
        require(next(storage_rows, None) is not None, "storage: missing header")
        for i, day in enumerate(formal):
            start_row = 2 + 6 * i
            wanted = FIRST + timedelta(days=i)
            for block in range(6):
                cells = next(storage_rows, None)
                require(cells is not None, f"storage: missing row {start_row+block}")
                if block == 0:
                    require(normalized_date(cells[0], f"storage A{start_row}") == wanted,
                            f"storage {wanted}: wrong date")
                else:
                    require(cells[0] is None, f"storage {wanted} block {block}: repeated date")
                require(cells[1] == period(24*block, 24*(block+1)),
                        f"storage {wanted} block {block}: wrong period")
                close(cells[2], sum(day["charge"][24*block:24*(block+1)]),
                      f"storage {wanted} block {block} charge")
                close(cells[3], sum(day["discharge"][24*block:24*(block+1)]),
                      f"storage {wanted} block {block} discharge")
                if block < 2:
                    require(cells[4] == ("0:00" if block == 0 else "24:00"),
                            f"storage {wanted} SOC time label")
                    close(cells[5], day["soc_start" if block == 0 else "soc_end"],
                          f"storage {wanted} SOC")
                else:
                    require(cells[4] is None and cells[5] is None,
                            f"storage {wanted} block {block}: unexpected SOC fields")
        for row in storage_rows:
            require(all(v is None for v in row), "storage has sample/extra data after row 2005")

        emergency_sheet = wb.worksheets[3]
        expected_rows: list[tuple[date | None, str | None, float | None]] = []
        for day in formal:
            runs = emergency_runs(day["emergency"])
            wanted = date.fromisoformat(day["date"])
            if not runs:
                expected_rows.append((wanted, None, None))
            else:
                expected_rows.extend((wanted if j == 0 else None, label, value)
                                     for j, (label, value) in enumerate(runs))
        emergency_rows = emergency_sheet.iter_rows(min_row=1, min_col=1,
                                                   max_col=3, values_only=True)
        require(next(emergency_rows, None) is not None, "emergency: missing header")
        for i, expected in enumerate(expected_rows, start=2):
            cells = next(emergency_rows, None)
            require(cells is not None, f"emergency: missing row {i}")
            wanted_date, label, quantity = expected
            if wanted_date is None:
                require(cells[0] is None, f"emergency row {i}: repeated date")
            else:
                require(normalized_date(cells[0], f"emergency A{i}") == wanted_date,
                        f"emergency row {i}: wrong date")
            require(cells[1] == label, f"emergency row {i}: wrong period {cells[1]!r}")
            if quantity is None:
                require(cells[2] is None, f"emergency row {i}: unexpected amount")
            else:
                close(cells[2], quantity, f"emergency row {i} amount")
        for row in emergency_rows:
            require(all(v is None for v in row), "emergency sheet has sample/extra data")
        return {"purchase_days": N_DAYS, "storage_rows": 6 * N_DAYS,
                "emergency_rows": len(expected_rows)}
    finally:
        wb.close()


def verify_summaries(output: Path, rolling: list[dict[str, Any]],
                     zero_only: list[dict[str, Any]], detail_totals: dict[str, float]) -> dict[str, Any]:
    summary_path = output / "result_summary.json"
    comparison_path = output / "strategy_comparison.json"
    require(summary_path.is_file() and comparison_path.is_file(),
            "summary or strategy comparison is missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    require(summary["days"] == N_DAYS and summary["complete_evaluation_period"] is True,
            "result summary is not complete official period")
    require(summary["evaluation_start"] == FIRST.isoformat()
            and summary["evaluation_end"] == LAST.isoformat(),
            "result summary evaluation dates mismatch")
    require(len(summary["daily"]) == N_DAYS, "result summary daily count mismatch")
    require(set(summary["monthly"]) == {f"2025-{m:02d}" for m in range(2, 13)},
            "result summary monthly keys mismatch")
    for i, row in enumerate(summary["daily"]):
        day = rolling[i + 31]
        require(row["date"] == day["date"], f"summary daily row {i}: date mismatch")
        for field, original in (
            ("initial_purchase_kwh", sum(day["initial_purchase"])),
            ("final_purchase_kwh", sum(day["final_purchase"])),
            ("charge_kwh", sum(day["charge"])),
            ("discharge_kwh", sum(day["discharge"])),
            ("emergency_kwh", sum(day["emergency"])),
            ("soc_start_kwh", day["soc_start"]), ("soc_end_kwh", day["soc_end"]),
            ("plan_cost_yuan", day["plan_cost"]),
            ("adjustment_cost_yuan", day["adjustment_cost"]),
            ("emergency_cost_yuan", day["emergency_cost"]),
            ("total_cost_yuan", day["total_cost"]),
        ):
            close(row[field], original, f"summary {day['date']} {field}")
    for field, expected in detail_totals.items():
        close(summary["totals"][field], expected, f"summary total {field}")
        monthly_sum = sum(month[field] for month in summary["monthly"].values())
        close(monthly_sum, expected, f"monthly sum {field}")

    for label, records in (("rolling", rolling), ("zero_only", zero_only)):
        row = comparison[label]
        require(row["days"] == N_DAYS, f"comparison {label}: wrong days")
        for key in ("plan_cost", "adjustment_cost", "emergency_cost", "total_cost"):
            expected = sum(float(day[key]) for day in records[31:])
            close(row[key], expected, f"comparison {label} {key}")
        close(row["emergency_kwh"],
              sum(sum(day["emergency"]) for day in records[31:]),
              f"comparison {label} emergency_kwh")
    close(comparison["rolling_saving_yuan"],
          comparison["zero_only"]["total_cost"] - comparison["rolling"]["total_cost"],
          "comparison savings")
    close(comparison["rolling"]["total_cost"],
          summary["totals"]["total_cost_yuan"], "workbook vs comparison cost")

    def verify_pair(saved: dict[str, Any],
                    pair: tuple[list[dict[str, Any]], list[dict[str, Any]]],
                    location: str) -> None:
        for label, records in zip(("rolling", "zero_only"), pair, strict=True):
            part = saved[label]
            require(part["days"] == len(records), f"{location} {label}: wrong day count")
            for key in ("plan_cost", "adjustment_cost", "emergency_cost", "total_cost"):
                close(part[key], sum(float(day[key]) for day in records),
                      f"{location} {label} {key}")
            close(part["emergency_kwh"],
                  sum(sum(day["emergency"]) for day in records),
                  f"{location} {label} emergency_kwh")
        saving = saved["zero_only"]["total_cost"] - saved["rolling"]["total_cost"]
        close(saved["rolling_saving_yuan"], saving, f"{location} saving")
        if saved["zero_only"]["total_cost"]:
            close(saved["rolling_saving_fraction"],
                  saving / saved["zero_only"]["total_cost"],
                  f"{location} saving fraction", atol=1e-9)

    require(set(comparison.get("monthly", {})) == {f"2025-{m:02d}" for m in range(2, 13)},
            "strategy comparison is missing a formal month")
    for month, saved in comparison["monthly"].items():
        pair = tuple([day for day in records[31:] if day["date"].startswith(month)]
                     for records in (rolling, zero_only))
        verify_pair(saved, pair, f"monthly {month}")

    selected_dates = ("2025-03-20", "2025-06-21", "2025-09-23", "2025-12-21")
    require(set(comparison.get("specified_dates", {})) == set(selected_dates),
            "strategy comparison specified dates mismatch")
    for wanted, saved in comparison["specified_dates"].items():
        for label, records in (("rolling", rolling), ("zero_only", zero_only)):
            day = next((record for record in records if record["date"] == wanted), None)
            require(day is not None, f"specified {wanted}: missing checkpoint record")
            part = saved[label]
            for field, expected in (
                ("initial_purchase_kwh", sum(day["initial_purchase"])),
                ("final_purchase_kwh", sum(day["final_purchase"])),
                ("emergency_kwh", sum(day["emergency"])),
                ("soc_start_kwh", day["soc_start"]),
                ("soc_end_kwh", day["soc_end"]),
                ("plan_cost", day["plan_cost"]),
                ("adjustment_cost", day["adjustment_cost"]),
                ("emergency_cost", day["emergency_cost"]),
                ("total_cost", day["total_cost"]),
            ):
                close(part[field], expected, f"specified {wanted} {label} {field}")
        close(saved["rolling_saving_yuan"],
              saved["zero_only"]["total_cost"] - saved["rolling"]["total_cost"],
              f"specified {wanted} saving")
    return {"rolling_total_cost_yuan": comparison["rolling"]["total_cost"],
            "zero_only_total_cost_yuan": comparison["zero_only"]["total_cost"],
            "rolling_emergency_kwh": comparison["rolling"]["emergency_kwh"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Independently verify the full official Question Three output")
    parser.add_argument("--base-dir", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "output_free_soc")
    args = parser.parse_args()
    data = load_inputs(args.base_dir)
    output = args.output_dir
    fingerprint = decision_fingerprint(data)
    rolling = read_checkpoint(output / "checkpoint_rolling.jsonl", "rolling", data, fingerprint)
    zero_only = read_checkpoint(output / "checkpoint_zero_only.jsonl", "zero_only", data, fingerprint)
    rolling_config, zero_config = rolling[0]["config"], zero_only[0]["config"]
    require({k: v for k, v in rolling_config.items() if k != "strategy"}
            == {k: v for k, v in zero_config.items() if k != "strategy"},
            "rolling and zero-only configurations/fingerprints differ")
    formal = rolling[31:]
    require(len(formal) == N_DAYS and formal[0]["date"] == FIRST.isoformat()
            and formal[-1]["date"] == LAST.isoformat(), "incorrect formal period")
    detail = verify_detail(output / "question_three_detail.csv", formal, data)
    workbook = verify_workbook(output / "result3.xlsx", formal)
    summary = verify_summaries(output, rolling, zero_only, detail)
    report = {"status": "passed", "evaluation_start": FIRST.isoformat(),
              "evaluation_end": LAST.isoformat(), "evaluation_days": N_DAYS,
              "detail_rows": N_DAYS * N_TIME, **workbook, **summary}
    (output / "verification_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
