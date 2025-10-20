"""
Agent responsible for detecting anomalies in historical data quality metrics.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from typing import Dict, List, Optional, Tuple

from src.agents.base_agent import BaseAgent
from src.core.config_manager import ConfigManager
from src.core.data_models import (
    Anomaly,
    AnomalyDetectionResults,
    DatasetProfile,
    ProfileResults,
)
try:
    from src.data_access.duckdb_manager import DuckDBManager
except ImportError:  # pragma: no cover - optional dependency handling
    DuckDBManager = None  # type: ignore

logger = logging.getLogger(__name__)


class AnomalyDetectionAgent(BaseAgent):
    """
    Analyze historical profiling metrics to flag significant shifts.

    The agent compares the current profiling run against historical baselines
    and raises anomalies when the delta or z-score exceed configurable thresholds.
    """

    SUPPORTED_METRICS = ("completeness", "uniqueness", "conformity")

    def __init__(
        self,
        config: Optional[Dict] = None,
        duckdb_manager: Optional[DuckDBManager] = None,
    ) -> None:
        super().__init__("anomaly_detection", config)

        config_manager = ConfigManager()
        if config is None:
            self.config = config_manager.get_agent_config("anomaly_detection")

        storage_cfg = config_manager.get_config("storage", {}).get("duckdb", {})
        self.storage_enabled = storage_cfg.get("enabled", False)

        if duckdb_manager is not None:
            self.duckdb_manager = duckdb_manager
        elif self.storage_enabled and DuckDBManager is not None:
            self.duckdb_manager = DuckDBManager(
                db_path=storage_cfg.get("path", "data/storage/dq_metrics.duckdb"),
                profiling_run_table=storage_cfg.get("profiling_run_table", "profiling_runs"),
                column_metrics_table=storage_cfg.get("column_metrics_table", "column_metrics"),
                issues_table=storage_cfg.get("issues_table", "issues"),
                anomalies_table=storage_cfg.get("anomalies_table", "anomalies"),
                retention_days=storage_cfg.get("retention_days"),
            )
        else:
            self.duckdb_manager = None
            if self.storage_enabled and DuckDBManager is None:
                logger.warning(
                    "DuckDB package unavailable; anomaly detection agent cannot access persistence."
                )
            else:
                logger.warning(
                    "DuckDB storage is disabled; anomaly detection will not persist results."
                )

        # Detection thresholds
        self.min_history_points = self.config.get("min_history_points", 3)
        self.delta_thresholds = self.config.get(
            "delta_thresholds",
            {"completeness": 10.0, "uniqueness": 5.0, "conformity": 5.0},
        )
        self.zscore_threshold = self.config.get("zscore_threshold", 2.5)
        self.max_history_points = self.config.get("max_history_points", 50)

    def execute(
        self,
        profile_results: Optional[ProfileResults] = None,
        dataset_name: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> AnomalyDetectionResults:
        """
        Run anomaly detection analysis.

        Args:
            profile_results: latest profiling output. If None, dataset_name and run_id
                must be provided to load results from DuckDB.
            dataset_name: dataset identifier (used when profile_results is None).
            run_id: optional run identifier to use as current baseline.
        """
        start = time.time()

        if profile_results is None and (dataset_name is None or not self.duckdb_manager):
            raise ValueError(
                "Provide profile_results or ensure dataset_name with DuckDB storage."
            )

        current_profile: Optional[DatasetProfile] = None
        current_run_id: Optional[str] = run_id

        if profile_results:
            dataset_name = profile_results.dataset_profile.dataset_name
            current_run_id = current_run_id or profile_results.execution_metadata.get("run_id")
            current_profile = profile_results.dataset_profile

        if not dataset_name:
            raise ValueError("Dataset name is required for anomaly detection.")

        if not self.duckdb_manager:
            raise RuntimeError("DuckDB manager is unavailable; cannot compute anomalies.")

        if current_profile is None:
            current_profile, current_run_id = self._load_profile_from_history(
                dataset_name, current_run_id
            )

        if current_profile is None or current_run_id is None:
            raise RuntimeError("Unable to obtain current dataset profile for analysis.")

        anomalies = self._detect_for_profile(current_profile, current_run_id)

        if anomalies:
            self.duckdb_manager.store_anomalies(anomalies, run_id=current_run_id)

        metadata = {
            "execution_time_seconds": time.time() - start,
            "anomaly_count": len(anomalies),
            "dataset_name": dataset_name,
            "run_id": current_run_id,
        }

        return AnomalyDetectionResults(
            dataset_name=dataset_name,
            run_id=current_run_id,
            anomalies=anomalies,
            execution_metadata=metadata,
        )

    # ------------------------------------------------------------------ #
    # Detection helpers
    # ------------------------------------------------------------------ #

    def _load_profile_from_history(
        self, dataset_name: str, run_id: Optional[str]
    ) -> Tuple[Optional[DatasetProfile], Optional[str]]:
        """Load the most recent dataset profile from DuckDB."""
        if not self.duckdb_manager:
            return None, None

        resolved_run_id = run_id or self.duckdb_manager.fetch_latest_run_id(dataset_name)
        if not resolved_run_id:
            logger.warning("No historical profiling runs found for dataset '%s'.", dataset_name)
            return None, None

        profile = self.duckdb_manager.load_dataset_profile(resolved_run_id)
        if profile is None:
            logger.warning(
                "Failed to reconstruct dataset profile from run %s for dataset '%s'.",
                resolved_run_id,
                dataset_name,
            )
            return None, None

        return profile, resolved_run_id

    def _detect_for_profile(
        self,
        profile: DatasetProfile,
        run_id: Optional[str],
    ) -> List[Anomaly]:
        """Compute anomalies for the provided profile."""
        anomalies: List[Anomaly] = []

        for column_name, metrics in profile.column_metrics.items():
            for metric_name in self.SUPPORTED_METRICS:
                metric_value = getattr(metrics, f"{metric_name}_score", None)
                if metric_value is None:
                    continue

                history = self.duckdb_manager.fetch_column_history(
                    dataset_name=profile.dataset_name,
                    column_name=column_name,
                    metric=metric_name,
                    limit=self.max_history_points,
                    exclude_run_id=run_id,
                )

                if len(history) < self.min_history_points:
                    continue

                historical_values = [value for _, value, _ in history]
                anomaly = self._evaluate_metric(
                    dataset_name=profile.dataset_name,
                    column_name=column_name,
                    metric=metric_name,
                    current_value=metric_value,
                    historical_values=historical_values,
                    run_id=run_id,
                )

                if anomaly:
                    anomalies.append(anomaly)

        return anomalies

    def _evaluate_metric(
        self,
        dataset_name: str,
        column_name: str,
        metric: str,
        current_value: float,
        historical_values: List[float],
        run_id: Optional[str],
    ) -> Optional[Anomaly]:
        """Determine whether the current_value is anomalous."""
        baseline = statistics.mean(historical_values)
        delta = current_value - baseline
        abs_delta = abs(delta)
        threshold = self.delta_thresholds.get(metric, 5.0)

        if len(historical_values) > 1:
            std_dev = statistics.pstdev(historical_values)
        else:
            std_dev = 0.0

        if std_dev == 0:
            if abs_delta == 0:
                return None
            z_score = float("inf")
        else:
            z_score = abs(delta) / std_dev

        if abs_delta < threshold and (z_score < self.zscore_threshold):
            return None

        severity = self._determine_severity(abs_delta, threshold, z_score)

        context: Dict[str, float] = {
            "baseline_mean": baseline,
            "baseline_std": std_dev,
            "absolute_delta": abs_delta,
            "threshold": threshold,
            "z_score_threshold": self.zscore_threshold,
            "history_size": len(historical_values),
        }

        return Anomaly(
            dataset_name=dataset_name,
            column_name=column_name,
            metric=metric,
            current_value=current_value,
            baseline_value=baseline,
            delta=delta,
            z_score=z_score if z_score != math.inf else float("inf"),
            severity=severity,
            detection_method="delta_zscore_composite",
            context=context,
        )

    def _determine_severity(
        self, abs_delta: float, threshold: float, z_score: float
    ) -> str:
        """Classify anomaly severity."""
        if abs_delta >= threshold * 2 or z_score >= self.zscore_threshold * 1.5:
            return "high"
        if abs_delta >= threshold * 1.2 or z_score >= self.zscore_threshold:
            return "medium"
        return "low"
