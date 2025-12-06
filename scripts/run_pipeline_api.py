#!/usr/bin/env python3
"""
End-to-end runner that chains the four agents:
Profiler -> Anomaly Detection -> Fix Recommendation -> Fix Executor.
Provides BOTH:
- importable functions for FastAPI
- CLI entrypoint for terminal usage
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents import (
    AnomalyDetectionAgent,
    FixRecommendationAgent,
    ProfilerAgent,
)
from src.agents.fix_executor_agent import FixExecutorAgent


# ------------------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------------------

def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def ensure_dirs(output_path: Path) -> None:
    """Ensure all required directories exist."""
    for path in [
        output_path.parent,
        Path("data/results"),
        Path("data/storage"),
        Path("data/output"),
    ]:
        path.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------------------
# IMPORTABLE API FUNCTIONS (fastapi uses these)
# ------------------------------------------------------------------------------

def run_profiling_only(dataset_path: str):
    """
    Run ONLY the profiling agent. Returns profile_results.
    """
    profiler = ProfilerAgent()
    profile_results = profiler.execute(dataset_path)
    return profile_results


def run_anomaly_detection_only(profile_results):
    """
    Run ONLY anomaly detection. Returns anomaly_results or None.
    """
    anomaly_agent = AnomalyDetectionAgent()
    try:
        return anomaly_agent.execute(profile_results=profile_results)
    except RuntimeError:
        # Likely DuckDB unavailable
        return None


def run_fix_recommendations_only(profile_results, anomaly_results, dataset_path: str):
    """
    Run ONLY fix recommendation agent. Returns recommendation_results.
    """
    fix_agent = FixRecommendationAgent()
    rec_results = fix_agent.execute(
        profile_results=profile_results,
        anomaly_results=anomaly_results,
        dataset_path=dataset_path,
    )
    return rec_results


def run_fix_execution(rec_results, dataset_path: str, output_path: str, apply_non_actionable=False):
    """
    Run ONLY the fix executor. Returns execution_results.
    """
    executor = FixExecutorAgent()
    exec_results = executor.execute(
        recommendations=rec_results,
        dataset_path=dataset_path,
        output_path=output_path,
        apply_only_actionable=not apply_non_actionable,
    )
    return exec_results


def run_full_pipeline(
    dataset_path: str,
    output_path: str = None,
    skip_anomaly_detection: bool = False,
    skip_executor: bool = False,
    apply_non_actionable: bool = False,
):
    """
    Full pipeline used by FastAPI:
    Returns {
        "profile": ...,
        "anomalies": ...,
        "recommendations": ...,
        "fix_results": ...
    }
    """

    dataset_path = Path(dataset_path)
    if output_path is None:
        output_dir = Path("data/output")
        output_path = output_dir / f"{dataset_path.stem}_fixed{dataset_path.suffix}"
    output_path = Path(output_path)
    ensure_dirs(output_path)

    # Step 1: profiling
    profile_results = run_profiling_only(str(dataset_path))

    # Step 2: anomaly detection
    anomaly_results = None
    if not skip_anomaly_detection:
        anomaly_results = run_anomaly_detection_only(profile_results)

    # Step 3: recommendations
    rec_results = run_fix_recommendations_only(
        profile_results,
        anomaly_results,
        str(dataset_path)
    )

    # Step 4: executor
    exec_results = None
    if not skip_executor:
        exec_results = run_fix_execution(
            rec_results,
            str(dataset_path),
            str(output_path),
            apply_non_actionable,
        )

    return {
        "profile": profile_results,
        "anomalies": anomaly_results,
        "recommendations": rec_results,
        "fix_results": exec_results,
        "output_path": str(output_path) if exec_results else None,
    }


# ------------------------------------------------------------------------------
# CLI entrypoint (unchanged)
# ------------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Run full data quality pipeline on a dataset.")
    parser.add_argument("--dataset", default="data/input/npidata_sample_100.csv")
    parser.add_argument("--output-file")
    parser.add_argument("--skip-anomaly-detection", action="store_true")
    parser.add_argument("--skip-executor", action="store_true")
    parser.add_argument("--apply-non-actionable", action="store_true")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    configure_logging(args.verbose)

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"Dataset not found: {dataset_path}")
        return 1

    result = run_full_pipeline(
        dataset_path=str(dataset_path),
        output_path=args.output_file,
        skip_anomaly_detection=args.skip_anomaly_detection,
        skip_executor=args.skip_executor,
        apply_non_actionable=args.apply_non_actionable,
    )

    print("\nPIPELINE COMPLETE")
    print(f"Output file: {result['output_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
