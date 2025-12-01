"""
Check conformity scores and baseline comparison for a dataset.

Usage:
    python scripts/check_conformity_baseline.py --dataset npidata_baseline
    python scripts/check_conformity_baseline.py --dataset npidata_baseline --column "Entity Type Code"
"""

import argparse
import sys
import statistics
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from src.data_access.duckdb_manager import DuckDBManager
except ImportError:
    print("ERROR: DuckDB package not installed. Run: pip install duckdb")
    sys.exit(1)


def show_conformity_comparison(db_manager, dataset_name, column_name=None):
    """Show conformity scores and baseline comparison."""
    print(f"\n{'='*80}")
    print(f"CONFORMITY ANALYSIS FOR: {dataset_name}")
    if column_name:
        print(f"COLUMN: {column_name}")
    print(f"{'='*80}")
    
    try:
        # Get all runs for this dataset
        if column_name:
            query = """
                SELECT 
                    r.run_id,
                    r.profiling_timestamp,
                    c.conformity_score,
                    c.non_null_count,
                    c.conforming_count,
                    c.non_conforming_count
                FROM column_metrics c
                JOIN profiling_runs r ON c.run_id = r.run_id
                WHERE c.dataset_name = ? AND c.column_name = ?
                  AND c.conformity_score IS NOT NULL
                ORDER BY r.profiling_timestamp DESC
            """
            params = [dataset_name, column_name]
        else:
            query = """
                SELECT 
                    r.run_id,
                    r.profiling_timestamp,
                    r.overall_conformity,
                    r.total_rows
                FROM profiling_runs r
                WHERE r.dataset_name = ?
                  AND r.overall_conformity IS NOT NULL
                ORDER BY r.profiling_timestamp DESC
            """
            params = [dataset_name]
        
        results = db_manager.conn.execute(query, params).fetchall()
        
        if not results:
            print(f"  No conformity data found for dataset '{dataset_name}'")
            if column_name:
                print(f"  Column: '{column_name}'")
            return
        
        print(f"\n{'Run ID':<40} {'Timestamp':<25} {'Conformity':<12} {'Details':<30}")
        print("-" * 120)
        
        runs = []
        for row in results:
            if column_name:
                run_id, timestamp, conformity, non_null, conforming, non_conforming = row
                details = f"Conforming: {conforming:,}/{non_null:,}"
            else:
                run_id, timestamp, conformity, total_rows = row
                details = f"Total rows: {total_rows:,}"
            
            print(f"{run_id:<40} {timestamp:<25} {conformity:<12.2f}% {details:<30}")
            runs.append({
                'run_id': run_id,
                'timestamp': timestamp,
                'conformity': conformity
            })
        
        if len(runs) < 2:
            print(f"\n⚠️  Only {len(runs)} run(s) found. Need at least 2 runs for baseline comparison.")
            return
        
        # Current run (most recent)
        current_run = runs[0]
        current_conformity = current_run['conformity']
        
        # Historical baseline (excluding current run)
        historical_values = [r['conformity'] for r in runs[1:]]
        baseline_mean = statistics.mean(historical_values)
        baseline_std = statistics.pstdev(historical_values) if len(historical_values) > 1 else 0.0
        
        # Calculate delta
        delta = current_conformity - baseline_mean
        
        print(f"\n{'='*80}")
        print("BASELINE COMPARISON")
        print(f"{'='*80}")
        print(f"Current Run:")
        print(f"  Run ID: {current_run['run_id']}")
        print(f"  Timestamp: {current_run['timestamp']}")
        print(f"  Conformity: {current_conformity:.2f}%")
        print(f"\nHistorical Baseline (from {len(historical_values)} previous run(s)):")
        print(f"  Mean: {baseline_mean:.2f}%")
        print(f"  Std Dev: {baseline_std:.2f}%")
        print(f"  Values: {[f'{v:.2f}%' for v in historical_values]}")
        print(f"\nComparison:")
        print(f"  Delta: {delta:+.2f}% ({'IMPROVEMENT' if delta > 0 else 'DEGRADATION' if delta < 0 else 'NO CHANGE'})")
        
        # Check anomaly detection thresholds
        delta_threshold = 5.0  # Default from config
        zscore_threshold = 2.0  # Default from config
        
        if baseline_std > 0:
            z_score = abs(delta) / baseline_std
        else:
            z_score = float('inf') if delta != 0 else 0.0
        
        print(f"\nAnomaly Detection:")
        print(f"  Delta threshold: {delta_threshold}%")
        print(f"  Z-score threshold: {zscore_threshold}")
        print(f"  Current delta: {abs(delta):.2f}%")
        print(f"  Current z-score: {z_score:.2f}")
        
        # Check if it would be flagged
        flag_only_degradations = True  # From config
        would_flag = False
        reason = ""
        
        if flag_only_degradations and delta > 0:
            reason = "Improvement ignored (flag_only_degradations=True)"
        elif abs(delta) < delta_threshold and z_score < zscore_threshold:
            reason = "Within thresholds"
        else:
            would_flag = True
            if delta < 0:
                reason = "DEGRADATION detected"
            else:
                reason = "IMPROVEMENT detected (but would be ignored with flag_only_degradations=True)"
        
        print(f"  Would flag as anomaly: {'YES' if would_flag else 'NO'}")
        print(f"  Reason: {reason}")
        
        # Check existing anomalies
        if column_name:
            anomaly_query = """
                SELECT 
                    column_name,
                    metric,
                    current_value,
                    baseline_value,
                    delta,
                    z_score,
                    severity,
                    detected_at
                FROM anomalies
                WHERE dataset_name = ? AND column_name = ? AND metric = 'conformity'
                ORDER BY detected_at DESC
                LIMIT 5
            """
            anomaly_params = [dataset_name, column_name]
        else:
            anomaly_query = """
                SELECT 
                    column_name,
                    metric,
                    current_value,
                    baseline_value,
                    delta,
                    z_score,
                    severity,
                    detected_at
                FROM anomalies
                WHERE dataset_name = ? AND metric = 'conformity'
                ORDER BY detected_at DESC
                LIMIT 10
            """
            anomaly_params = [dataset_name]
        
        existing_anomalies = db_manager.conn.execute(anomaly_query, anomaly_params).fetchall()
        
        if existing_anomalies:
            print(f"\n{'='*80}")
            print(f"EXISTING ANOMALIES IN DATABASE")
            print(f"{'='*80}")
            print(f"{'Column':<30} {'Current':<10} {'Baseline':<10} {'Delta':<10} {'Z-Score':<10} {'Severity':<10}")
            print("-" * 80)
            for col, metric, current, baseline, delta_val, z_score_val, severity, detected_at in existing_anomalies:
                print(f"{col:<30} {current:<10.1f}% {baseline:<10.1f}% {delta_val:<10.1f} {z_score_val:<10.2f} {severity:<10}")
        else:
            print(f"\n{'='*80}")
            print(f"No existing anomalies found in database for this dataset/column")
            print(f"{'='*80}")
        
    except Exception as e:
        print(f"  Error querying conformity data: {e}")
        import traceback
        traceback.print_exc()


def list_columns(db_manager, dataset_name):
    """List all columns with conformity data for a dataset."""
    try:
        results = db_manager.conn.execute("""
            SELECT DISTINCT column_name, COUNT(*) as run_count
            FROM column_metrics
            WHERE dataset_name = ? AND conformity_score IS NOT NULL
            GROUP BY column_name
            ORDER BY column_name
        """, [dataset_name]).fetchall()
        
        if results:
            print(f"\nColumns with conformity data for '{dataset_name}':")
            for col, count in results:
                print(f"  - {col} ({count} runs)")
        else:
            print(f"No columns with conformity data found for '{dataset_name}'")
    except Exception as e:
        print(f"Error listing columns: {e}")


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description='Check conformity scores and baseline comparison',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check overall conformity for a dataset
  python scripts/check_conformity_baseline.py --dataset npidata_baseline
  
  # Check specific column conformity
  python scripts/check_conformity_baseline.py --dataset npidata_baseline --column "Entity Type Code"
  
  # List all columns with conformity data
  python scripts/check_conformity_baseline.py --dataset npidata_baseline --list-columns
        """
    )
    parser.add_argument(
        '--dataset',
        type=str,
        required=True,
        help='Dataset name to check'
    )
    parser.add_argument(
        '--column',
        type=str,
        help='Specific column name to check (optional)'
    )
    parser.add_argument(
        '--list-columns',
        action='store_true',
        help='List all columns with conformity data'
    )
    parser.add_argument(
        '--db-path',
        type=str,
        default='data/storage/dq_metrics.duckdb',
        help='Path to DuckDB database (default: data/storage/dq_metrics.duckdb)'
    )
    
    args = parser.parse_args()
    
    # Connect to DuckDB
    try:
        db_manager = DuckDBManager(args.db_path)
    except Exception as e:
        print(f"ERROR: Could not connect to DuckDB: {e}")
        print(f"Database path: {args.db_path}")
        sys.exit(1)
    
    try:
        if args.list_columns:
            list_columns(db_manager, args.dataset)
        else:
            show_conformity_comparison(db_manager, args.dataset, args.column)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db_manager.close()


if __name__ == '__main__':
    main()

