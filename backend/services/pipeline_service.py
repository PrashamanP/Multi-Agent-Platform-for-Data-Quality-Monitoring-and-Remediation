import json
import numpy as np
import math
from typing import Optional
from scripts.run_pipeline_api import (
    run_profiling_only,
    run_full_pipeline
)

def convert_to_serializable(obj):
    """Recursively convert numpy types to native Python types for JSON serialization"""
    if isinstance(obj, dict):
        return {key: convert_to_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_serializable(item) for item in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif hasattr(obj, '__dict__'):
        return convert_to_serializable(obj.__dict__)
    else:
        return obj

def profiling_only(dataset_path: str):
    """Run the profiler agent and return normalized results."""
    # Run the profiler agent
    profile_results = run_profiling_only(dataset_path)

    # Convert the profile_results object to dict
    result_dict = convert_to_serializable(profile_results)

    # Extract dataset_profile and flatten column_metrics to column_profiles
    if 'dataset_profile' in result_dict:
        dataset_profile = result_dict['dataset_profile']

        # The column data is in 'column_metrics', not 'column_profiles'
        # Move it to top level as 'column_profiles' for frontend compatibility
        if 'column_metrics' in dataset_profile:
            result_dict['column_profiles'] = dataset_profile['column_metrics']

        # Keep the summary stats in dataset_profile
        result_dict['dataset_profile'] = {
            'total_rows': dataset_profile.get('total_rows', 0),
            'total_columns': dataset_profile.get('total_columns', 0),
            'dataset_name': dataset_profile.get('dataset_name', ''),
            'profiled_at': dataset_profile.get('profiling_timestamp', ''),
            'overall_completeness': dataset_profile.get('overall_completeness', 0),
            'overall_conformity': dataset_profile.get('overall_conformity', 0),
            'overall_uniqueness': dataset_profile.get('overall_uniqueness', 0),
        }

    return result_dict

def anomaly_detection_with_config(dataset_path: str, config: dict, run_id: Optional[str] = None):
    """
    Run anomaly detection with custom configuration.
    Normalizes values to 0-1 range if they're stored as 0-100.
    """
    from src.agents.anomaly_detection_agent import AnomalyDetectionAgent

    # Run profiling first
    profile_results = run_profiling_only(dataset_path)

    # FIX: Normalize all values to 0-1 range (they're stored as 0-100)
    # This fixes the issue without modifying teammates' agent code

    # Normalize overall metrics
    if profile_results.dataset_profile.overall_completeness > 1.0:
        profile_results.dataset_profile.overall_completeness /= 100.0
    if profile_results.dataset_profile.overall_conformity > 1.0:
        profile_results.dataset_profile.overall_conformity /= 100.0
    if profile_results.dataset_profile.overall_uniqueness > 1.0:
        profile_results.dataset_profile.overall_uniqueness /= 100.0

    # Normalize column-level metrics
    for col_name, metrics in profile_results.dataset_profile.column_metrics.items():
        if hasattr(metrics, 'completeness_score') and metrics.completeness_score and metrics.completeness_score > 1.0:
            metrics.completeness_score /= 100.0
        if hasattr(metrics, 'conformity_score') and metrics.conformity_score and metrics.conformity_score > 1.0:
            metrics.conformity_score /= 100.0
        if hasattr(metrics, 'uniqueness_score') and metrics.uniqueness_score and metrics.uniqueness_score > 1.0:
            metrics.uniqueness_score /= 100.0

    # Create anomaly agent with custom config
    try:
        anomaly_agent = AnomalyDetectionAgent(config=config)

        anomaly_results = anomaly_agent.execute(
            profile_results=profile_results,
            run_id=run_id
        )

        if anomaly_results is None:
            return {
                "status": "skipped",
                "message": "Anomaly detection unavailable (DuckDB not configured or insufficient history)",
                "anomalies": [],
                "shifts": []
            }

        # Convert to serializable format
        result_dict = convert_to_serializable(anomaly_results)

        # Extract execution metadata and enhance it
        if 'execution_metadata' not in result_dict:
            result_dict['execution_metadata'] = {}

        # Add historical run information if available
        if anomaly_agent.duckdb_manager:
            try:
                # Use the existing DuckDB connection from the anomaly agent
                conn = anomaly_agent.duckdb_manager.conn
                dataset_name = profile_results.dataset_profile.dataset_name

                # Count how many historical runs exist
                historical_count_result = conn.execute(f"""
                    SELECT COUNT(*)
                    FROM profiling_runs
                    WHERE dataset_name = '{dataset_name}'
                """).fetchone()

                historical_count = historical_count_result[0] if historical_count_result else 0

                # Subtract 1 for the current run
                result_dict['execution_metadata']['history_size'] = max(0, historical_count - 1)

                # Get the list of unique datasets in baseline
                baseline_datasets = conn.execute("""
                    SELECT DISTINCT dataset_name, COUNT(*) as count
                    FROM profiling_runs
                    GROUP BY dataset_name
                    ORDER BY count DESC
                """).fetchall()

                result_dict['baseline_info'] = {
                    "total_historical_runs": historical_count - 1,
                    "baseline_datasets": [f"{row[0]} ({row[1]} runs)" for row in baseline_datasets],
                }

            except Exception as e:
                # If we can't get the info, just skip it
                print(f"ERROR getting historical runs: {e}")
                import traceback
                traceback.print_exc()
                result_dict['execution_metadata']['history_size'] = "Unknown"

        # FIX: Handle infinity and NaN values in anomalies, normalize baseline values
        if 'anomalies' in result_dict:
            for anomaly in result_dict['anomalies']:
                # FIX: Baseline values are stored as 0-100, normalize them first
                if 'baseline_value' in anomaly:
                    baseline = anomaly['baseline_value']
                    # If baseline is > 1, it's stored as percentage (0-100), convert to 0-1 first
                    if baseline > 1.0:
                        baseline = baseline / 100.0
                    # Now convert to percentage for display
                    anomaly['baseline_value'] = baseline * 100.0

                # Current value is already 0-1, convert to percentage
                if 'current_value' in anomaly:
                    anomaly['current_value'] *= 100

                # Recalculate delta correctly (current - baseline, both in percentage)
                if 'current_value' in anomaly and 'baseline_value' in anomaly:
                    anomaly['delta'] = anomaly['current_value'] - anomaly['baseline_value']

                # Replace infinity/NaN with safe values
                if 'z_score' in anomaly:
                    z_score = anomaly['z_score']
                    if math.isinf(z_score) or math.isnan(z_score):
                        anomaly['z_score'] = 999.0  # Use a large number instead of inf

                # Handle context values
                if 'context' in anomaly:
                    context = anomaly['context']
                    for key, value in context.items():
                        if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
                            context[key] = 999.0 if math.isinf(value) else 0.0

        return result_dict

    except RuntimeError as e:
        return {
            "status": "error",
            "message": str(e),
            "anomalies": [],
            "shifts": []
        }
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "message": f"Unexpected error: {str(e)}",
            "traceback": traceback.format_exc(),
            "anomalies": [],
            "shifts": []
        }

def full_pipeline(dataset_path: str):
    """Run the full pipeline."""
    result = run_full_pipeline(dataset_path)
    serializable_result = convert_to_serializable(result)
    return serializable_result