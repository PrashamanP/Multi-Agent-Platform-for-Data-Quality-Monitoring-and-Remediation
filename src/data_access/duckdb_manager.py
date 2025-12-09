"""
DuckDB persistence layer for profiling metrics and anomalies.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import duckdb
except ImportError as exc:
    raise ImportError(
        "DuckDB package is required for persistence. Install with 'pip install duckdb'."
    ) from exc

try:
    import numpy as np
except ImportError:
    np = None

from src.core.data_models import (
    Anomaly,
    ColumnMetrics,
    DatasetProfile,
    Issue,
    ProfileResults,
)

logger = logging.getLogger(__name__)


class DuckDBManager:
    """Handles persistence of profiling runs, column metrics, and anomalies in DuckDB."""

    def __init__(
        self,
        db_path: str,
        profiling_run_table: str = "profiling_runs",
        column_metrics_table: str = "column_metrics",
        issues_table: str = "issues",
        anomalies_table: str = "anomalies",
        retention_days: Optional[int] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = duckdb.connect(str(self.db_path))
        self.profiling_run_table = profiling_run_table
        self.column_metrics_table = column_metrics_table
        self.issues_table = issues_table
        self.anomalies_table = anomalies_table
        self.retention_days = retention_days

        self._ensure_schema()

    def close(self) -> None:
        """Close the DuckDB connection."""
        try:
            self.conn.close()
        except Exception as exc:
            logger.warning("Failed to close DuckDB connection: %s", exc)
    
    # Schema management
    
    @staticmethod
    def _convert_to_duckdb_type(value: Any) -> Any:
        """
        Convert numpy types to Python native types for DuckDB compatibility.
        
        Args:
            value: Value that might be a numpy type
            
        Returns:
            Python native type value
        """
        if np is not None and isinstance(value, np.integer):
            return int(value)
        elif np is not None and isinstance(value, np.floating):
            return float(value)
        elif isinstance(value, (int, float, str, type(None))):
            return value
        else:
            return value

    def _ensure_schema(self) -> None:
        """Create required tables if they are missing."""
        logger.debug("Ensuring DuckDB schema exists at %s", self.db_path)

        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.profiling_run_table} (
                run_id VARCHAR PRIMARY KEY,
                dataset_name VARCHAR,
                profiling_timestamp TIMESTAMP,
                total_rows BIGINT,
                total_columns INTEGER,
                overall_completeness DOUBLE,
                overall_uniqueness DOUBLE,
                overall_conformity DOUBLE,
                execution_time_seconds DOUBLE,
                data_size_mb DOUBLE,
                processing_mode VARCHAR,
                rules_applied INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.column_metrics_table} (
                run_id VARCHAR,
                dataset_name VARCHAR,
                column_name VARCHAR,
                total_count BIGINT,
                non_null_count BIGINT,
                unique_count BIGINT,
                duplicate_count BIGINT,
                completeness_score DOUBLE,
                uniqueness_score DOUBLE,
                conformity_score DOUBLE,
                conforming_count BIGINT,
                non_conforming_count BIGINT,
                min_length INTEGER,
                max_length INTEGER,
                avg_length DOUBLE,
                most_common_values JSON,
                conformity_violations JSON,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.issues_table} (
                issue_id VARCHAR,
                run_id VARCHAR,
                dataset_name VARCHAR,
                column_name VARCHAR,
                issue_type VARCHAR,
                count BIGINT,
                percentage DOUBLE,
                description VARCHAR,
                examples JSON,
                rule_violated VARCHAR,
                detected_at TIMESTAMP
            )
            """
        )

        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.anomalies_table} (
                anomaly_id VARCHAR PRIMARY KEY,
                dataset_name VARCHAR,
                column_name VARCHAR,
                metric VARCHAR,
                current_value DOUBLE,
                baseline_value DOUBLE,
                delta DOUBLE,
                z_score DOUBLE,
                severity VARCHAR,
                detection_method VARCHAR,
                context JSON,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                run_id VARCHAR
            )
            """
        )

        if self.retention_days:
            self._enforce_retention()

    def _enforce_retention(self) -> None:
        """Delete stale rows to keep the database manageable."""
        try:
            cutoff = datetime.utcnow() - timedelta(days=self.retention_days)
            self.conn.execute(
                f"DELETE FROM {self.profiling_run_table} WHERE profiling_timestamp < ?",
                [cutoff],
            )
            self.conn.execute(
                f"DELETE FROM {self.column_metrics_table} WHERE detected_at < ?",
                [cutoff],
            )
            self.conn.execute(
                f"DELETE FROM {self.issues_table} WHERE detected_at < ?",
                [cutoff],
            )
            self.conn.execute(
                f"DELETE FROM {self.anomalies_table} WHERE detected_at < ?",
                [cutoff],
            )
        except Exception as exc:
            logger.warning("Failed to enforce DuckDB retention policy: %s", exc)

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #

    def store_profile_results(
        self,
        results: ProfileResults,
        run_id: Optional[str] = None,
    ) -> str:
        """
        Persist profiling results and return the run identifier.

        Args:
            results: profile results to persist
            run_id: optional override for the run identifier
        """
        run_id = run_id or str(uuid.uuid4())
        profile = results.dataset_profile

        exec_meta = {
            "execution_time_seconds": results.execution_metadata.get(
                "execution_time_seconds"
            ),
            "processing_mode": results.execution_metadata.get("processing_mode"),
            "rules_applied": results.execution_metadata.get("rules_applied"),
        }

        self.conn.execute(
            f"""
            INSERT OR REPLACE INTO {self.profiling_run_table}
            (
                run_id,
                dataset_name,
                profiling_timestamp,
                total_rows,
                total_columns,
                overall_completeness,
                overall_uniqueness,
                overall_conformity,
                execution_time_seconds,
                data_size_mb,
                processing_mode,
                rules_applied
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                profile.dataset_name,
                profile.profiling_timestamp,
                self._convert_to_duckdb_type(profile.total_rows),
                self._convert_to_duckdb_type(profile.total_columns),
                self._convert_to_duckdb_type(profile.overall_completeness),
                self._convert_to_duckdb_type(profile.overall_uniqueness),
                self._convert_to_duckdb_type(profile.overall_conformity),
                self._convert_to_duckdb_type(exec_meta["execution_time_seconds"]),
                self._convert_to_duckdb_type(profile.data_size_mb),
                exec_meta["processing_mode"],
                self._convert_to_duckdb_type(exec_meta["rules_applied"]),
            ],
        )

        self._store_column_metrics(run_id, profile)
        self._store_issues(run_id, profile.dataset_name, results.issues)

        return run_id

    def _store_column_metrics(self, run_id: str, profile: DatasetProfile) -> None:
        """Persist per-column metrics for a profiling run."""
        insert_rows: List[Tuple[Any, ...]] = []
        for metrics in profile.column_metrics.values():
            insert_rows.append(
                (
                    run_id,
                    profile.dataset_name,
                    metrics.column_name,
                    self._convert_to_duckdb_type(metrics.total_count),
                    self._convert_to_duckdb_type(metrics.non_null_count),
                    self._convert_to_duckdb_type(metrics.unique_count),
                    self._convert_to_duckdb_type(metrics.duplicate_count),
                    self._convert_to_duckdb_type(metrics.completeness_score),
                    self._convert_to_duckdb_type(metrics.uniqueness_score),
                    self._convert_to_duckdb_type(metrics.conformity_score),
                    self._convert_to_duckdb_type(metrics.conforming_count),
                    self._convert_to_duckdb_type(metrics.non_conforming_count),
                    self._convert_to_duckdb_type(metrics.min_length),
                    self._convert_to_duckdb_type(metrics.max_length),
                    self._convert_to_duckdb_type(metrics.avg_length),
                    json.dumps(metrics.most_common_values, default=str),
                    json.dumps(metrics.conformity_violations, default=str),
                )
            )

        self.conn.executemany(
            f"""
            INSERT INTO {self.column_metrics_table} (
                run_id,
                dataset_name,
                column_name,
                total_count,
                non_null_count,
                unique_count,
                duplicate_count,
                completeness_score,
                uniqueness_score,
                conformity_score,
                conforming_count,
                non_conforming_count,
                min_length,
                max_length,
                avg_length,
                most_common_values,
                conformity_violations
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_rows,
        )

    def _store_issues(
        self,
        run_id: str,
        dataset_name: str,
        issues: Iterable[Issue],
    ) -> None:
        """Persist detected issues for a profiling run."""
        if not issues:
            return

        rows: List[Tuple[Any, ...]] = []
        for issue in issues:
            rows.append(
                (
                    issue.issue_id,
                    run_id,
                    dataset_name,
                    issue.column_name,
                    issue.issue_type,
                    self._convert_to_duckdb_type(issue.count),
                    self._convert_to_duckdb_type(issue.percentage),
                    issue.description,
                    json.dumps(issue.examples, default=str),
                    issue.rule_violated,
                    issue.detected_at,
                )
            )

        self.conn.executemany(
            f"""
            INSERT INTO {self.issues_table} (
                issue_id,
                run_id,
                dataset_name,
                column_name,
                issue_type,
                count,
                percentage,
                description,
                examples,
                rule_violated,
                detected_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    # ------------------------------------------------------------------ #
    # Retrieval helpers
    # ------------------------------------------------------------------ #

    def fetch_column_history(
        self,
        dataset_name: str,
        column_name: str,
        metric: str,
        limit: Optional[int] = None,
        exclude_run_id: Optional[str] = None,
        before_timestamp: Optional[datetime] = None,
    ) -> List[Tuple[datetime, float, str]]:
        """
        Retrieve historical metric values for a column.

        Returns list of (profiling_timestamp, value, run_id)
        sorted ascending by timestamp.
        """
        if metric not in {"completeness", "uniqueness", "conformity"}:
            raise ValueError(f"Unsupported metric '{metric}'")

        metric_column = {
            "completeness": "completeness_score",
            "uniqueness": "uniqueness_score",
            "conformity": "conformity_score",
        }[metric]

        query = f"""
            SELECT r.profiling_timestamp, c.{metric_column}, c.run_id
            FROM {self.column_metrics_table} AS c
            JOIN {self.profiling_run_table} AS r
              ON c.run_id = r.run_id
            WHERE c.dataset_name = ?
              AND c.column_name = ?
              AND c.{metric_column} IS NOT NULL
        """

        params: List[Any] = [dataset_name, column_name]
        if exclude_run_id:
            query += " AND c.run_id <> ?"
            params.append(exclude_run_id)
        if before_timestamp:
            query += " AND r.profiling_timestamp < ?"
            params.append(before_timestamp)

        query += " ORDER BY r.profiling_timestamp ASC"
        if limit:
            query += f" LIMIT {int(limit)}"

        results = self.conn.execute(query, params).fetchall()
        return [(row[0], float(row[1]), row[2]) for row in results]

    def fetch_latest_run_id(self, dataset_name: str) -> Optional[str]:
        """Fetch run_id of the most recent profiling run for a dataset."""
        query = f"""
            SELECT run_id
            FROM {self.profiling_run_table}
            WHERE dataset_name = ?
            ORDER BY profiling_timestamp DESC
            LIMIT 1
        """
        result = self.conn.execute(query, [dataset_name]).fetchone()
        return result[0] if result else None

    def load_dataset_profile(self, run_id: str) -> Optional[DatasetProfile]:
        """Rehydrate a DatasetProfile from stored metrics."""
        run_query = f"""
            SELECT
                dataset_name,
                profiling_timestamp,
                total_rows,
                total_columns,
                overall_completeness,
                overall_uniqueness,
                overall_conformity,
                execution_time_seconds,
                data_size_mb
            FROM {self.profiling_run_table}
            WHERE run_id = ?
        """

        run_cursor = self.conn.execute(run_query, [run_id])
        run_row = run_cursor.fetchone()
        if not run_row:
            return None

        run_columns = [col[0] for col in run_cursor.description]
        run_data = dict(zip(run_columns, run_row))

        column_query = f"""
            SELECT
                column_name,
                total_count,
                non_null_count,
                unique_count,
                duplicate_count,
                completeness_score,
                uniqueness_score,
                conformity_score,
                conforming_count,
                non_conforming_count,
                min_length,
                max_length,
                avg_length,
                most_common_values,
                conformity_violations
            FROM {self.column_metrics_table}
            WHERE run_id = ?
        """

        column_cursor = self.conn.execute(column_query, [run_id])
        column_columns = [col[0] for col in column_cursor.description]

        column_metrics: Dict[str, ColumnMetrics] = {}
        for row in column_cursor.fetchall():
            record = dict(zip(column_columns, row))
            column_metrics[record["column_name"]] = ColumnMetrics(
                column_name=record["column_name"],
                total_count=int(record["total_count"] or 0),
                non_null_count=int(record["non_null_count"] or 0),
                unique_count=int(record["unique_count"] or 0),
                duplicate_count=int(record["duplicate_count"] or 0),
                conforming_count=int(record["conforming_count"] or 0),
                non_conforming_count=int(record["non_conforming_count"] or 0),
                completeness_score=float(record["completeness_score"] or 0.0),
                uniqueness_score=(
                    float(record["uniqueness_score"])
                    if record["uniqueness_score"] is not None
                    else None
                ),
                conformity_score=float(record["conformity_score"] or 0.0),
                min_length=(int(record["min_length"]) if record["min_length"] is not None else None),
                max_length=(int(record["max_length"]) if record["max_length"] is not None else None),
                avg_length=(float(record["avg_length"]) if record["avg_length"] is not None else None),
                most_common_values=json.loads(record["most_common_values"] or "[]"),
                conformity_violations=json.loads(record["conformity_violations"] or "[]"),
            )

        return DatasetProfile(
            dataset_name=run_data["dataset_name"],
            total_rows=int(run_data["total_rows"] or 0),
            total_columns=int(run_data["total_columns"] or 0),
            profiling_timestamp=run_data["profiling_timestamp"],
            column_metrics=column_metrics,
            overall_completeness=float(run_data["overall_completeness"] or 0.0),
            overall_uniqueness=float(run_data["overall_uniqueness"] or 0.0),
            overall_conformity=float(run_data["overall_conformity"] or 0.0),
            processing_time_seconds=float(run_data.get("execution_time_seconds") or 0.0),
            data_size_mb=float(run_data.get("data_size_mb") or 0.0),
        )

    # ------------------------------------------------------------------ #
    # Anomaly persistence
    # ------------------------------------------------------------------ #

    def store_anomalies(
        self,
        anomalies: Iterable[Anomaly],
        run_id: Optional[str] = None,
    ) -> None:
        """Persist detected anomalies."""
        rows: List[Tuple[Any, ...]] = []
        for anomaly in anomalies:
            record = asdict(anomaly)
            rows.append(
                (
                    record["anomaly_id"],
                    record["dataset_name"],
                    record["column_name"],
                    record["metric"],
                    self._convert_to_duckdb_type(record["current_value"]),
                    self._convert_to_duckdb_type(record["baseline_value"]),
                    self._convert_to_duckdb_type(record["delta"]),
                    self._convert_to_duckdb_type(record["z_score"]),
                    record["severity"],
                    record["detection_method"],
                    json.dumps(record.get("context", {}), default=str),
                    record["detected_at"],
                    run_id or record.get("run_id"),
                )
            )

        if rows:
            self.conn.executemany(
                f"""
                INSERT INTO {self.anomalies_table} (
                    anomaly_id,
                    dataset_name,
                    column_name,
                    metric,
                    current_value,
                    baseline_value,
                    delta,
                    z_score,
                    severity,
                    detection_method,
                    context,
                    detected_at,
                    run_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    # ------------------------------------------------------------------ #
    # Context managers
    # ------------------------------------------------------------------ #

    def __enter__(self) -> "DuckDBManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
