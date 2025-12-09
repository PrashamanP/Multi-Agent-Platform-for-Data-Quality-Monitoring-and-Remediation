#!/usr/bin/env python3
"""
End-to-end runner that chains the four agents:
Profiler -> Anomaly Detection -> Fix Recommendation -> Fix Executor.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents import (  # noqa: E402
    AnomalyDetectionAgent,
    FixRecommendationAgent,
    ProfilerAgent,
)
from src.agents.fix_executor_agent import FixExecutorAgent  # noqa: E402


def configure_logging(verbose: bool) -> None:
    """Configure root logger for CLI use."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def print_divider(title: str) -> None:
    """Pretty-print a section divider."""
    line = "=" * 80
    print(f"\n{line}\n{title}\n{line}")


def summarize_recommendations(results) -> None:
    """Print a brief summary of recommendation results."""
    total = len(results.recommendations)
    actionable = sum(1 for r in results.recommendations if r.actionable)
    by_type = {}
    for rec in results.recommendations:
        by_type[rec.issue_type] = by_type.get(rec.issue_type, 0) + 1

    print(f"Total recommendations: {total} ({actionable} actionable)")
    if by_type:
        summary = ", ".join(f"{k}: {v}" for k, v in by_type.items())
        print(f"By issue type: {summary}")


def ensure_dirs(output_path: Path) -> None:
    """Create common data directories when missing."""
    for path in [
        output_path.parent,
        Path("data/results"),
        Path("data/storage"),
        Path("data/output"),
    ]:
        path.mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run full data quality pipeline on a dataset."
    )
    parser.add_argument(
        "--dataset",
        default="data/input/npidata_sample_100.csv",
        help="Path to input dataset (CSV/Parquet/Excel). Default uses bundled sample.",
    )
    parser.add_argument(
        "--dataset-name",
        help="Optional dataset identifier to use in DuckDB history (defaults to file stem).",
    )
    parser.add_argument(
        "--output-file",
        help="Where to write the fixed dataset. Defaults to <dataset>_fixed.<ext> next to input.",
    )
    parser.add_argument(
        "--skip-anomaly-detection",
        action="store_true",
        help="Skip anomaly detection (useful when DuckDB storage is not available).",
    )
    parser.add_argument(
        "--skip-executor",
        action="store_true",
        help="Generate recommendations only, without applying fixes.",
    )
    parser.add_argument(
        "--apply-non-actionable",
        action="store_true",
        help="Also apply recommendations marked as non-actionable (use with caution).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args()
    configure_logging(args.verbose)

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"Dataset not found: {dataset_path}")
        return 1

    if args.output_file:
        output_path = Path(args.output_file)
    else:
        # Default to data/output so we don't write into the input folder
        output_dir = Path("data/output")
        output_path = output_dir / f"{dataset_path.stem}_fixed{dataset_path.suffix}"
    ensure_dirs(output_path)

    dataset_name = args.dataset_name
    print_divider("RUNNING DATA QUALITY PIPELINE")
    print(f"Dataset: {dataset_path}")
    if dataset_name:
        print(f"Dataset name (override): {dataset_name}")

    try:
        # Step 1: Profiling
        print_divider("STEP 1/4 - Profiling")
        profiler = ProfilerAgent()
        profile_results = profiler.execute(str(dataset_path), dataset_name=dataset_name)
        profile = profile_results.dataset_profile
        print(
            f"Rows: {profile.total_rows:,}, Columns: {profile.total_columns}, "
            f"Issues found: {len(profile_results.issues)}"
        )

        # Step 2: Anomaly detection
        anomaly_results = None
        if args.skip_anomaly_detection:
            print("Anomaly detection skipped.")
        else:
            print_divider("STEP 2/4 - Anomaly Detection")
            anomaly_agent = AnomalyDetectionAgent()
            try:
                anomaly_results = anomaly_agent.execute(profile_results=profile_results)
                print(f"Anomalies detected: {len(anomaly_results.anomalies)}")
            except RuntimeError as exc:
                # Likely due to missing DuckDB; keep going.
                print(f"Anomaly detection unavailable ({exc}); continuing without it.")

        # Step 3: Fix recommendations
        print_divider("STEP 3/4 - Fix Recommendations")
        fix_agent = FixRecommendationAgent()
        rec_results = fix_agent.execute(
            profile_results=profile_results,
            anomaly_results=anomaly_results,
            dataset_path=str(dataset_path),
        )
        summarize_recommendations(rec_results)

        if args.skip_executor:
            print("Fix executor skipped. Recommendations only.")
            return 0

        # Step 4: Apply fixes
        print_divider("STEP 4/4 - Fix Execution")
        executor = FixExecutorAgent()
        exec_results = executor.execute(
            recommendations=rec_results,
            dataset_path=str(dataset_path),
            output_path=str(output_path),
            apply_only_actionable=not args.apply_non_actionable,
        )
        print(f"Fixed dataset written to: {exec_results.fixed_path}")
        sidecar = output_path.with_name(f"{output_path.stem}_fix_recommendations.csv")
        if sidecar.exists():
            print(f"Per-row recommendation sidecar: {sidecar}")

        print_divider("PIPELINE COMPLETE")
        exec_time = rec_results.execution_metadata.get("execution_time_seconds", 0.0)
        print(
            f"Total recommendations: {len(rec_results.recommendations)} | "
            f"Rows fixed: {exec_results.total_rows_fixed} | "
            f"Elapsed (recommendations stage): {exec_time:.2f}s"
        )
        return 0

    except Exception as exc:  # pragma: no cover - safety net for CLI usage
        logging.exception("Pipeline failed")
        print(f"Pipeline failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
