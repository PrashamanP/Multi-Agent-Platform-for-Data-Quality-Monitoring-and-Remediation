

import argparse
import logging
import sys
from pathlib import Path

from loguru import logger

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.anomaly_detection_agent import AnomalyDetectionAgent
from src.agents.profiler_agent import ProfilerAgent


def configure_logging(verbose: bool) -> None:
    """Configure console logging."""
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stdout, level=level)
    logging.basicConfig(level=level)


def run_demo(dataset_path: str, skip_profiling: bool, verbose: bool) -> None:
    """Execute profiling and anomaly detection for a dataset."""
    configure_logging(verbose)

    dataset_path = Path(dataset_path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    logger.info("Starting anomaly detection demo for %s", dataset_path)

    profiler_agent = ProfilerAgent()
    profile_results = None
    dataset_name = dataset_path.stem

    if not skip_profiling:
        logger.info("Running profiler agent to generate latest metrics...")
        profile_results = profiler_agent.execute(str(dataset_path))
        logger.info(
            "Profiler completed. Run ID: %s", profile_results.execution_metadata.get("run_id")
        )
        dataset_name = profile_results.dataset_profile.dataset_name
    else:
        logger.info("Skipping profiling and using latest stored run for anomaly detection")

    anomaly_agent = AnomalyDetectionAgent()
    anomaly_results = anomaly_agent.execute(
        profile_results=profile_results,
        dataset_name=dataset_name,
    )

    if not anomaly_results.anomalies:
        logger.info("No anomalies detected for dataset '%s'.", anomaly_results.dataset_name)
        return

    logger.warning(
        "Detected %d anomalies for dataset '%s' (run %s)",
        len(anomaly_results.anomalies),
        anomaly_results.dataset_name,
        anomaly_results.run_id,
    )

    for anomaly in anomaly_results.anomalies:
        logger.warning(
            "[%s] %s.%s current=%.2f baseline=%.2f delta=%.2f severity=%s",
            anomaly.metric,
            anomaly.dataset_name,
            anomaly.column_name,
            anomaly.current_value,
            anomaly.baseline_value,
            anomaly.delta,
            anomaly.severity,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run anomaly detection demo")
    parser.add_argument(
        "--dataset",
        required=True,
        help="Path to the dataset CSV file",
    )
    parser.add_argument(
        "--skip-profiling",
        action="store_true",
        help="Skip profiler run and reuse the most recent stored profile",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    run_demo(arguments.dataset, arguments.skip_profiling, arguments.verbose)
