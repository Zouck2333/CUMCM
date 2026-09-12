# Fourth-question model program

This directory implements the formal model in model_Four.md. The core solver
accepts in-memory arrays; a separate adapter reads attachments 2, 3 and 4,
runs both strategies from 2025-01-01, and fills the official result templates.

## Modules

- code/model.py: causal load/PV/price forecasts, historical residual scenarios,
  scenario probabilities, rolling 4-2/4-3 execution, actual settlement, SOC
  carryover, optional terminal reserve and terminal value.
- code/stage_solver.py: SciPy/HiGHS mixed-integer program, CVaR, and three
  lexicographic objectives. The solver accepts only the current stage forecast.
- code/price_stress.py: positive historical price bands, known-price fixing,
  and exact budgeted worst-price cost for a fixed executed trajectory.
- code/input_data.py: validated workbook input, right-endpoint time mapping,
  and kW to kWh conversion for actual load/PV.
- code/run_question_four.py: resumable annual simulation, detail CSV, summary
  JSON and official 4-2/4-3 workbook export.
- code/output_xlsx.mjs: artifact-tool writer for the two supplied templates.
- code/verify_outputs.py: independent cell-by-cell workbook verification.
- code/demo.py: synthetic three-day smoke test.
- tests/test_model.py: unit and integration tests using synthetic arrays.

Inputs to DayData are date, load_kwh[144], pv_kwh[144], price[144], and
pv_forecast_kw[4,24]. Load and PV are kWh per ten-minute interval; the forecast
is kW, and price is yuan/kWh. A future data loader should do unit conversion
before constructing DayData. Days must be chronological and consecutive.

Run from the repository root:

    python -m unittest discover -s Question_Four/tests -v
    python -m Question_Four.code.demo
    python -m Question_Four.code.run_question_four
    python -m Question_Four.code.verify_outputs

The annual run writes Question_Four/output/result4-2.xlsx and result4-3.xlsx,
plus one checkpoint JSONL, detail CSV and summary JSON per strategy. Rerunning
with the same model and attachments resumes validated checkpoints. For a small
solver test, use --max-days 2 --no-workbook; a partial run never writes an
official workbook. The workbooks contain only the February-December 334-day
formal period, while January remains in the checkpoints as SOC warm-up.

The Excel templates are authored by the bundled Node.js @oai/artifact-tool
runtime. System Python needs NumPy and SciPy; openpyxl can be installed
normally or loaded from the bundled workspace runtime.

Call run_strategy(days, "4-2") and run_strategy(days, "4-3") independently.
Each begins on 2025-01-01 at 6000 kWh and carries actual SOC from one day to
the next. The returned list includes January warm-up; formal_results filters
the list to February-December. Do not share one HistoryModel between strategy
runs, since run_day appends each completed day to its history.

The formal configuration has no extra terminal reserve or value. For separate
experiments, use ModelConfig(reserve_quantile=0.90) or
ModelConfig(terminal_value_weight=0.5), then rerun independently from January.
The fixed-trajectory price stress test is implemented. Section 11.3's
optional robust re-optimization is not implemented; stress_test must not be
reported as a robust dispatch solution.

MILP stages must finish at the requested MIP gap. A time-limited incumbent
raises instead of silently proceeding to the next lexicographic objective.
