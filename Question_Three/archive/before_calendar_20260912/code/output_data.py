"""Export Question Three's realized day records to the official result workbook.

All energy arrays contain 144 physical ten-minute *kWh* intervals, beginning
with 00:00-00:10.  The Excel workbook is authored by the bundled
``@oai/artifact-tool`` runtime; this module handles validation and plain-text
audit outputs.
"""

from __future__ import annotations

import csv
from datetime import date
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


INTERVALS_PER_DAY = 144
SOC_MIN = 1200.0
SOC_MAX = 10800.0
ENERGY_FIELDS = (
    "initial_purchase",
    "final_purchase",
    "charge",
    "discharge",
    "emergency",
)
AUDIT_FIELDS = ("actual_load", "actual_pv", "soc_path", "surplus", "price")
COST_FIELDS = ("plan_cost", "adjustment_cost", "emergency_cost", "total_cost")


def _clock(t: int, *, terminal: str = "24:00") -> str:
    if t == INTERVALS_PER_DAY:
        return terminal
    hour, minute = divmod(10 * t, 60)
    return f"{hour}:{minute:02d}"


def _number(value: Any, label: str, *, nonnegative: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    if nonnegative and result < -1e-7:
        raise ValueError(f"{label} must be nonnegative")
    return max(0.0, result) if nonnegative else result


def _normalize_day(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise TypeError("Each day must be a dict")
    try:
        day = date.fromisoformat(str(record["date"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("Each day needs an ISO date (YYYY-MM-DD)") from exc
    if day.year != 2025 or not (date(2025, 2, 1) <= day <= date(2025, 12, 31)):
        raise ValueError(f"{day}: official result3.xlsx dates must be in 2025-02-01..2025-12-31")

    result: dict[str, Any] = {"date": day.isoformat()}
    for field in ENERGY_FIELDS:
        try:
            values = list(record[field])
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{day}: {field} must contain 144 kWh values") from exc
        if len(values) != INTERVALS_PER_DAY:
            raise ValueError(f"{day}: {field} has {len(values)} values; expected 144")
        result[field] = [_number(x, f"{day} {field}[{t}]", nonnegative=True) for t, x in enumerate(values)]

    for field in ("soc_start", "soc_end"):
        if field not in record:
            raise ValueError(f"{day}: missing {field}")
        result[field] = _number(record[field], f"{day} {field}", nonnegative=True)
    if any(not SOC_MIN <= result[field] <= SOC_MAX for field in ("soc_start", "soc_end")):
        raise ValueError(f"{day}: SOC must remain in [{SOC_MIN}, {SOC_MAX}] kWh")
    for field in COST_FIELDS:
        if field not in record:
            raise ValueError(f"{day}: missing {field}")
        result[field] = _number(record[field], f"{day} {field}", nonnegative=True)

    provided_audit = [field for field in AUDIT_FIELDS if field in record]
    if provided_audit and len(provided_audit) != len(AUDIT_FIELDS):
        missing = ", ".join(field for field in AUDIT_FIELDS if field not in record)
        raise ValueError(f"{day}: audit fields must be supplied together; missing {missing}")
    if provided_audit:
        for field in AUDIT_FIELDS:
            try:
                values = list(record[field])
            except TypeError as exc:
                raise ValueError(f"{day}: {field} must be an array") from exc
            expected = INTERVALS_PER_DAY + 1 if field == "soc_path" else INTERVALS_PER_DAY
            if len(values) != expected:
                raise ValueError(f"{day}: {field} has {len(values)} values; expected {expected}")
            result[field] = [
                _number(x, f"{day} {field}[{t}]", nonnegative=True)
                for t, x in enumerate(values)
            ]
        if not math.isclose(result["soc_path"][0], result["soc_start"], rel_tol=1e-8, abs_tol=1e-5):
            raise ValueError(f"{day}: soc_path[0] differs from soc_start")
        if not math.isclose(result["soc_path"][-1], result["soc_end"], rel_tol=1e-8, abs_tol=1e-5):
            raise ValueError(f"{day}: soc_path[144] differs from soc_end")
        if any(not SOC_MIN - 1e-6 <= value <= SOC_MAX + 1e-6 for value in result["soc_path"]):
            raise ValueError(f"{day}: soc_path leaves the battery SOC bounds")

        calculated = {field: 0.0 for field in COST_FIELDS}
        for t in range(INTERVALS_PER_DAY):
            plan, adjustment, emergency, total = _interval_costs(result, t)
            calculated["plan_cost"] += plan
            calculated["adjustment_cost"] += adjustment
            calculated["emergency_cost"] += emergency
            calculated["total_cost"] += total
        for field in COST_FIELDS:
            if not math.isclose(result[field], calculated[field], rel_tol=1e-8, abs_tol=1e-4):
                raise ValueError(f"{day}: {field} differs from the sum of interval costs")

    components = result["plan_cost"] + result["adjustment_cost"] + result["emergency_cost"]
    if not math.isclose(result["total_cost"], components, rel_tol=1e-8, abs_tol=1e-4):
        raise ValueError(f"{day}: total_cost differs from the three cost components")
    return result


def _interval_costs(day: dict[str, Any], t: int) -> tuple[float, float, float, float]:
    price = day["price"][t]
    initial = day["initial_purchase"][t]
    final = day["final_purchase"][t]
    plan = price * initial
    adjustment = price * (1.5 * max(0.0, final - initial) + 0.5 * max(0.0, initial - final))
    emergency = 5.0 * price * day["emergency"][t]
    return plan, adjustment, emergency, plan + adjustment + emergency


def _runtime() -> tuple[Path, Path]:
    """Return the bundled Node binary and package directory, with env overrides."""
    cache = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node"
    default_node = cache / "bin" / "node.exe"
    node = Path(os.environ.get("QUESTION_THREE_NODE", str(default_node)))
    if not node.is_file():
        found = shutil.which("node")
        if found is None:
            raise RuntimeError("Node.js is required to export result3.xlsx")
        node = Path(found)

    modules = Path(os.environ.get("QUESTION_THREE_NODE_MODULES", str(cache / "node_modules")))
    if not (modules / "@oai" / "artifact-tool").is_dir():
        raise RuntimeError(
            "The bundled @oai/artifact-tool is unavailable; set QUESTION_THREE_NODE_MODULES "
            "to the bundled node_modules path"
        )
    return node, modules


def _link_modules(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise
    # Windows without Developer Mode can still create a directory junction.
    quote = lambda p: str(p).replace("'", "''")
    command = f"New-Item -ItemType Junction -Path '{quote(link)}' -Target '{quote(target)}' | Out-Null"
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )


def _run_xlsx_writer(days: list[dict[str, Any]], template: Path, workbook: Path) -> None:
    node, modules = _runtime()
    helper = Path(__file__).with_name("output_xlsx.mjs")
    if not helper.is_file():
        raise FileNotFoundError(helper)
    with tempfile.TemporaryDirectory(prefix="q3_xlsx_") as temporary:
        working = Path(temporary)
        _link_modules(working / "node_modules", modules)
        script = working / "output_xlsx.mjs"
        shutil.copy2(helper, script)
        source = working / "days.json"
        source.write_text(json.dumps(days, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        completed = subprocess.run(
            [str(node), str(script), str(source), str(template), str(workbook)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if completed.returncode:
            raise RuntimeError(
                "The result3.xlsx export failed:\n"
                + (completed.stderr or completed.stdout)[-4000:]
            )
    if not workbook.is_file() or workbook.stat().st_size == 0:
        raise RuntimeError("The result3.xlsx exporter did not produce a workbook")


def _write_detail(days: list[dict[str, Any]], path: Path) -> None:
    fields = (
        "date", "t", "time_period", "initial_purchase_kwh", "final_purchase_kwh",
        "charge_kwh", "discharge_kwh", "emergency_kwh",
        "actual_load_kwh", "actual_pv_kwh", "soc_before_kwh", "soc_after_kwh",
        "surplus_kwh", "price_yuan_per_kwh", "plan_cost_yuan",
        "adjustment_cost_yuan", "emergency_cost_yuan", "total_cost_yuan",
    )
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for day in days:
            has_audit = all(field in day for field in AUDIT_FIELDS)
            for t in range(INTERVALS_PER_DAY):
                row = {
                    "date": day["date"],
                    "t": t,
                    "time_period": f"{_clock(t)}-{_clock(t + 1)}",
                    "initial_purchase_kwh": day["initial_purchase"][t],
                    "final_purchase_kwh": day["final_purchase"][t],
                    "charge_kwh": day["charge"][t],
                    "discharge_kwh": day["discharge"][t],
                    "emergency_kwh": day["emergency"][t],
                }
                if has_audit:
                    plan, adjustment, emergency, total = _interval_costs(day, t)
                    row.update({
                        "actual_load_kwh": day["actual_load"][t],
                        "actual_pv_kwh": day["actual_pv"][t],
                        "soc_before_kwh": day["soc_path"][t],
                        "soc_after_kwh": day["soc_path"][t + 1],
                        "surplus_kwh": day["surplus"][t],
                        "price_yuan_per_kwh": day["price"][t],
                        "plan_cost_yuan": plan,
                        "adjustment_cost_yuan": adjustment,
                        "emergency_cost_yuan": emergency,
                        "total_cost_yuan": total,
                    })
                writer.writerow(row)


def _summarize(days: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    monthly: dict[str, dict[str, float]] = {}
    totals = {
        "initial_purchase_kwh": 0.0,
        "final_purchase_kwh": 0.0,
        "charge_kwh": 0.0,
        "discharge_kwh": 0.0,
        "emergency_kwh": 0.0,
        "plan_cost_yuan": 0.0,
        "adjustment_cost_yuan": 0.0,
        "emergency_cost_yuan": 0.0,
        "total_cost_yuan": 0.0,
    }
    for day in days:
        row = {
            "date": day["date"],
            "initial_purchase_kwh": sum(day["initial_purchase"]),
            "final_purchase_kwh": sum(day["final_purchase"]),
            "charge_kwh": sum(day["charge"]),
            "discharge_kwh": sum(day["discharge"]),
            "emergency_kwh": sum(day["emergency"]),
            "soc_start_kwh": day["soc_start"],
            "soc_end_kwh": day["soc_end"],
            "plan_cost_yuan": day["plan_cost"],
            "adjustment_cost_yuan": day["adjustment_cost"],
            "emergency_cost_yuan": day["emergency_cost"],
            "total_cost_yuan": day["total_cost"],
        }
        rows.append(row)
        month = monthly.setdefault(day["date"][:7], {key: 0.0 for key in totals})
        for key in totals:
            totals[key] += row[key]
            month[key] += row[key]
    return {
        "evaluation_start": days[0]["date"],
        "evaluation_end": days[-1]["date"],
        "days": len(days),
        "complete_evaluation_period": (
            len(days) == 334 and days[0]["date"] == "2025-02-01" and days[-1]["date"] == "2025-12-31"
        ),
        "units": {"energy": "kWh", "cost": "yuan"},
        "totals": totals,
        "monthly": monthly,
        "daily": rows,
    }


def write_outputs(days: list[dict[str, Any]], template_path: Path, output_dir: Path) -> dict[str, Any]:
    """Write result3.xlsx, a ten-minute CSV, and an auditable JSON summary.

    ``days`` may be a contiguous subset for a smoke test.  The official run
    must pass all 334 evaluation dates, from 2025-02-01 through 2025-12-31.
    """
    if not days:
        raise ValueError("At least one evaluation day is required")
    normalized = [_normalize_day(day) for day in days]
    dates = [date.fromisoformat(day["date"]) for day in normalized]
    if any((later - earlier).days != 1 for earlier, later in zip(dates, dates[1:])):
        raise ValueError("Evaluation days must be unique, consecutive, and sorted")
    for earlier, later in zip(normalized, normalized[1:]):
        if not math.isclose(earlier["soc_end"], later["soc_start"], rel_tol=0.0, abs_tol=1e-6):
            raise ValueError(f"{later['date']}: opening SOC differs from the previous closing SOC")

    template_path = Path(template_path)
    output_dir = Path(output_dir)
    if not template_path.is_file():
        raise FileNotFoundError(template_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    workbook = output_dir / "result3.xlsx"
    detail = output_dir / "question_three_detail.csv"
    summary_path = output_dir / "result_summary.json"

    _run_xlsx_writer(normalized, template_path, workbook)
    _write_detail(normalized, detail)
    summary = _summarize(normalized)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return {
        "workbook": str(workbook),
        "detail_csv": str(detail),
        "summary_json": str(summary_path),
        "summary": summary,
    }
