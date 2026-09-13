"""Verify reports for the packaged causal-monthly result."""
import argparse
from pathlib import Path
from build_causal_summary import verify_analysis

if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="核验当前历史滚动调参分析")
    parser.add_argument("--output-dir", type=Path, default=root / "output")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    verify_analysis(args.output_dir, args.report or args.output_dir / "analysis_verification.json")
