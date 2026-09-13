"""Generate reports for the packaged causal-monthly result."""
import argparse
from pathlib import Path
from build_causal_summary import build_summary

if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="汇总当前历史滚动调参结果")
    parser.add_argument("--output-dir", type=Path, default=root / "output")
    args = parser.parse_args()
    build_summary(args.output_dir.resolve())
