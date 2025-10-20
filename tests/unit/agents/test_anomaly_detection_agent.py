"""
Unit tests for the AnomalyDetectionAgent.
"""

import uuid
from datetime import datetime, timedelta

import pytest

duckdb = pytest.importorskip("duckdb")

from src.agents.anomaly_detection_agent import AnomalyDetectionAgent
from src.core.data_models import (
    ColumnMetrics,
    DatasetProfile,
    ProfileResults,
)
from src.data_access.duckdb_manager import DuckDBManager


@pytest.fixture
def duckdb_manager(tmp_path):
    """Create a temporary DuckDB manager for tests."""
    db_path = tmp_path / "dq_metrics.duckdb"
    manager = DuckDBManager(str(db_path))
    yield manager
    manager.close()


def _build_profile_results(dataset_name: str, completeness: float, timestamp: datetime) -> ProfileResults:
    """Helper to create ProfileResults with basic column metrics."""
    metrics = ColumnMetrics(
        column_name="NPI",
        total_count=100,
        non_null_count=100,
        unique_count=100,
        duplicate_count=0,
        conforming_count=100,
        non_conforming_count=0,
        completeness_score=completeness,
        uniqueness_score=100.0,
        conformity_score=100.0,
        min_length=10,
        max_length=10,
        avg_length=10.0,
        most_common_values=[],
        conformity_violations=[],
    )

    profile = DatasetProfile(
        dataset_name=dataset_name,
        total_rows=100,
        total_columns=1,
        profiling_timestamp=timestamp,
        column_metrics={"NPI": metrics},
        overall_completeness=completeness,
        overall_uniqueness=100.0,
        overall_conformity=100.0,
        processing_time_seconds=0.1,
        data_size_mb=0.01,
    )

    run_id = str(uuid.uuid4())
    return ProfileResults(
        dataset_profile=profile,
        issues=[],
        execution_metadata={"run_id": run_id, "processing_mode": "standard"},
    )


def _persist_history(duckdb_manager: DuckDBManager, dataset_name: str, values):
    """Persist historical completeness values for test setup."""
    base_time = datetime.utcnow() - timedelta(days=len(values))
    for offset, completeness in enumerate(values):
        results = _build_profile_results(
            dataset_name=dataset_name,
            completeness=completeness,
            timestamp=base_time + timedelta(days=offset),
        )
        duckdb_manager.store_profile_results(
            results, run_id=results.execution_metadata["run_id"]
        )


def test_anomaly_detects_significant_drop(duckdb_manager):
    dataset_name = "test_dataset"
    _persist_history(duckdb_manager, dataset_name, [96.0, 95.5, 97.0, 95.8])

    current_results = _build_profile_results(
        dataset_name=dataset_name,
        completeness=70.0,
        timestamp=datetime.utcnow(),
    )
    duckdb_manager.store_profile_results(
        current_results, run_id=current_results.execution_metadata["run_id"]
    )

    agent = AnomalyDetectionAgent(
        config={
            "min_history_points": 3,
            "delta_thresholds": {"completeness": 10.0, "uniqueness": 5.0, "conformity": 5.0},
            "zscore_threshold": 2.0,
        },
        duckdb_manager=duckdb_manager,
    )

    results = agent.execute(profile_results=current_results)

    assert results.has_anomalies is True
    assert len(results.anomalies) == 1
    anomaly = results.anomalies[0]
    assert anomaly.metric == "completeness"
    assert anomaly.severity in {"medium", "high"}
    assert pytest.approx(anomaly.current_value, rel=1e-3) == 70.0
    assert anomaly.baseline_value > anomaly.current_value


def test_anomaly_skips_when_history_insufficient(duckdb_manager):
    dataset_name = "test_dataset_small_history"
    _persist_history(duckdb_manager, dataset_name, [90.0, 91.0])  # Only two points

    current_results = _build_profile_results(
        dataset_name=dataset_name,
        completeness=60.0,
        timestamp=datetime.utcnow(),
    )
    duckdb_manager.store_profile_results(
        current_results, run_id=current_results.execution_metadata["run_id"]
    )

    agent = AnomalyDetectionAgent(
        config={"min_history_points": 3},
        duckdb_manager=duckdb_manager,
    )

    results = agent.execute(profile_results=current_results)
    assert results.has_anomalies is False


def test_anomaly_persists_results_to_duckdb(duckdb_manager):
    dataset_name = "test_dataset_persistence"
    _persist_history(duckdb_manager, dataset_name, [95.0, 94.5, 96.0, 95.2])

    current_results = _build_profile_results(
        dataset_name=dataset_name,
        completeness=75.0,
        timestamp=datetime.utcnow(),
    )
    duckdb_manager.store_profile_results(
        current_results, run_id=current_results.execution_metadata["run_id"]
    )

    agent = AnomalyDetectionAgent(
        config={
            "min_history_points": 3,
            "delta_thresholds": {"completeness": 15.0, "uniqueness": 5.0, "conformity": 5.0},
            "zscore_threshold": 1.5,
        },
        duckdb_manager=duckdb_manager,
    )

    results = agent.execute(profile_results=current_results)
    assert results.has_anomalies is True

    stored_count = duckdb_manager.conn.execute(
        "SELECT COUNT(*) FROM anomalies"
    ).fetchone()[0]
    assert stored_count >= len(results.anomalies)


def test_execute_with_stored_profile_only(duckdb_manager):
    dataset_name = "test_dataset_skip_profiling"
    _persist_history(duckdb_manager, dataset_name, [98.0, 97.5, 98.2, 97.9])

    current_results = _build_profile_results(
        dataset_name=dataset_name,
        completeness=70.0,
        timestamp=datetime.utcnow(),
    )
    duckdb_manager.store_profile_results(
        current_results, run_id=current_results.execution_metadata["run_id"]
    )

    agent = AnomalyDetectionAgent(
        config={
            "min_history_points": 3,
            "delta_thresholds": {"completeness": 5.0, "uniqueness": 5.0, "conformity": 5.0},
            "zscore_threshold": 1.5,
        },
        duckdb_manager=duckdb_manager,
    )

    results = agent.execute(dataset_name=dataset_name)
    assert results.has_anomalies is True
    assert results.run_id == current_results.execution_metadata["run_id"]
