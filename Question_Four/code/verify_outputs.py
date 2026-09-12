"""Independent cell-by-cell checks for the exported Question Four workbooks."""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path

from .input_data import load_workbook
from .model import FORMAL_START


N_TIME = 144
EPS = 2e-5


def _equal(actual: object, expected: float, label: str) -> None:
    if actual is None or abs(float(actual) - expected) > EPS:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _date(actual: object, expected: str, label: str) -> None:
    found = actual.date() if isinstance(actual, datetime) else actual
    if found != date.fromisoformat(expected):
        raise AssertionError(f"{label}: expected {expected}, got {actual!r}")


def _records(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    formal = [record for record in records if date.fromisoformat(record["date"]) >= FORMAL_START]
    if len(formal) != 334:
        raise AssertionError(f"{path}: expected 334 formal days, got {len(formal)}")
    return formal


def _check_purchase(sheet, records: list[dict], field: str, cost_field: str) -> None:
    if sheet["B1"].value != "0:00-0:10" or sheet["EO1"].value != "23:50-0:00+1":
        raise AssertionError("time headers do not cover the 144 physical intervals")
    rows = sheet.iter_rows(min_row=2, max_row=335, min_col=1, max_col=147, values_only=True)
    for row_number, (row, record) in enumerate(zip(rows, records, strict=True), start=2):
        _date(row[0], record["date"], f"purchase row {row_number} date")
        values = record[field]
        for t, expected in enumerate(values):
            _equal(row[1+t], expected, f"purchase row {row_number} t={t}")
        _equal(row[145], sum(values), f"purchase row {row_number} EP")
        _equal(row[146], record[cost_field], f"purchase row {row_number} EQ")


def _check_storage(sheet, records: list[dict]) -> None:
    rows = sheet.iter_rows(min_row=2, max_row=1 + 334 * 6, min_col=1, max_col=6, values_only=True)
    for i, (row, record) in enumerate(zip(rows, (
        record for record in records for _ in range(6)
    ), strict=True)):
        block = i % 6
        day_index = i // 6
        if block == 0:
            _date(row[0], record["date"], f"storage day {day_index} date")
            _equal(row[5], record["soc_start"], f"storage day {day_index} opening SOC")
        elif block == 1:
            _equal(row[5], record["soc_end"], f"storage day {day_index} closing SOC")
        start = 24 * block
        end = start + 24
        _equal(row[2], sum(record["charge"][start:end]), f"storage day {day_index} block {block} charge")
        _equal(row[3], sum(record["discharge"][start:end]), f"storage day {day_index} block {block} discharge")


def _emergency_periods(record: dict) -> list[tuple[int, int, float]]:
    periods: list[tuple[int, int, float]] = []
    begin = None
    amount = 0.0
    for t in range(N_TIME + 1):
        active = t < N_TIME and record["emergency"][t] > 1e-8
        if active:
            if begin is None:
                begin = t
            amount += record["emergency"][t]
        elif begin is not None:
            periods.append((begin, t, amount))
            begin = None
            amount = 0.0
    return periods


def _clock(t: int) -> str:
    if t == N_TIME:
        return "24:00"
    minute = t * 10
    return f"{minute // 60}:{minute % 60:02d}"


def _check_emergency(sheet, records: list[dict]) -> int:
    expected: list[tuple[str | None, str | None, float | None]] = []
    for record in records:
        periods = _emergency_periods(record)
        if not periods:
            expected.append((record["date"], None, None))
        else:
            for i, (start, stop, amount) in enumerate(periods):
                expected.append((
                    record["date"] if i == 0 else None,
                    f"{_clock(start)}-{_clock(stop)}",
                    amount,
                ))
    rows = sheet.iter_rows(min_row=2, max_row=1 + len(expected), min_col=1, max_col=3, values_only=True)
    for row_number, (row, wanted) in enumerate(zip(rows, expected, strict=True), start=2):
        wanted_date, wanted_period, wanted_amount = wanted
        if wanted_date is not None:
            _date(row[0], wanted_date, f"emergency row {row_number} date")
        elif row[0] is not None:
            raise AssertionError(f"emergency row {row_number}: repeated date")
        if row[1] != wanted_period:
            raise AssertionError(f"emergency row {row_number}: wrong interval")
        if wanted_amount is None:
            if row[2] is not None:
                raise AssertionError(f"emergency row {row_number}: expected blank energy")
        else:
            _equal(row[2], wanted_amount, f"emergency row {row_number} energy")
    return len(expected)


def verify(output_dir: Path) -> dict:
    report: dict[str, dict] = {}
    for strategy in ("4-2", "4-3"):
        records = _records(output_dir / f"checkpoint_{strategy}.jsonl")
        workbook = load_workbook(
            output_dir / f"result{strategy}.xlsx", read_only=True, data_only=True,
        )
        try:
            expected_sheets = 3 if strategy == "4-2" else 4
            if len(workbook.worksheets) != expected_sheets:
                raise AssertionError(f"{strategy}: wrong sheet count")
            _check_purchase(workbook.worksheets[0], records, "initial_purchase", "plan_cost")
            if strategy == "4-3":
                _check_purchase(workbook.worksheets[1], records, "final_purchase", "total_cost")
            storage = workbook.worksheets[1 if strategy == "4-2" else 2]
            emergency = workbook.worksheets[2 if strategy == "4-2" else 3]
            _check_storage(storage, records)
            emergency_rows = _check_emergency(emergency, records)
            report[strategy] = {"days": len(records), "emergency_rows": emergency_rows}
        finally:
            workbook.close()
    return report


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).resolve().parents[1] / "output"), indent=2))
