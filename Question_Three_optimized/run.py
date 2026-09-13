"""Portable entry point for the packaged, optimized third-question model."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def execute(arguments: list[str]) -> None:
    subprocess.run([sys.executable, "-B", *arguments], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="第三问优化版：完整模型与结果包")
    parser.add_argument("action", nargs="?", default="verify",
                        choices=("verify", "test", "solve", "compare", "report", "all"))
    args = parser.parse_args()
    code = ROOT / "code"
    data_arguments = ["--base-dir", str(ROOT / "inputs")]
    output_arguments = ["--output-dir", str(ROOT / "output_optimized")]
    actions = ("test", "solve", "compare", "report", "verify") if args.action == "all" else (args.action,)
    for action in actions:
        print(f"[{action}] {ROOT}", flush=True)
        if action == "test":
            execute(["-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"])
        elif action == "solve":
            execute([str(code / "run_question_three.py"), *data_arguments, *output_arguments])
        elif action == "compare":
            execute([str(code / "run_question_three.py"), *data_arguments,
                     "--strategy", "rolling", "--forecast-mode", "four_day",
                     "--output-dir", str(ROOT / "comparison_four_day")])
        elif action == "report":
            execute([str(code / "build_optimization_report.py")])
        else:
            execute([str(code / "verify_question_three.py"), *data_arguments, *output_arguments])


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
