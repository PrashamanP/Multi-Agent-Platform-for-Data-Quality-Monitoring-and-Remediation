"""
Delete outlier profiling runs from DuckDB.

Usage:
    python scripts/delete_outlier_runs.py --dataset npidata_baseline
    python scripts/delete_outlier_runs.py --dataset npidata_baseline --dry-run  # Preview only
    python scripts/delete_outlier_runs.py --run-id <specific-run-id>  # Delete specific run
"""

import argparse
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from src.data_access.duckdb_manager import DuckDBManager
except ImportError:
    print("ERROR: DuckDB package not installed. Run: pip install duckdb")
    sys.exit(1)


def show_runs_for_dataset(db_manager, dataset_name):
    """Display all runs for a dataset."""
    print(f"\n{'='*80}")
    print(f"PROFILING RUNS FOR DATASET: {dataset_name}")
    print(f"{'='*80}")
    
    try:
        results = db_manager.conn.execute("""
            SELECT 
                run_id,
                dataset_name,
                profiling_timestamp,
                total_rows,
                total_columns,
                overall_completeness,
                overall_uniqueness,
                overall_conformity,
                data_size_mb
            FROM profiling_runs
            WHERE dataset_name = ?
            ORDER BY profiling_timestamp ASC
        """, [dataset_name]).fetchall()
        
        if not results:
            print(f"  No profiling runs found for dataset '{dataset_name}'.")
            return []
        
        print(f"{'Run ID':<40} {'Timestamp':<20} {'Rows':<12} {'Cols':<6} "
              f"{'Complete':<10} {'Unique':<10} {'Conform':<10} {'Size (MB)':<10}")
        print("-" * 120)
        
        runs = []
        for run_id, dataset, timestamp, rows, cols, completeness, uniqueness, conformity, size_mb in results:
            print(f"{run_id:<40} {timestamp:<20} {rows:<12,} {cols:<6} "
                  f"{completeness:<10.1f}% {uniqueness:<10.1f}% {conformity:<10.1f}% {size_mb:<10.2f}")
            runs.append({
                'run_id': run_id,
                'timestamp': timestamp,
                'total_rows': rows,
                'total_columns': cols,
                'overall_completeness': completeness,
                'overall_uniqueness': uniqueness,
                'overall_conformity': conformity,
                'data_size_mb': size_mb
            })
        
        return runs
    
    except Exception as e:
        print(f"  Error querying profiling runs: {e}")
        return []


def identify_outliers(runs, threshold_multiplier=2.0):
    """
    Identify outlier runs based on row count.
    
    Args:
        runs: List of run dictionaries
        threshold_multiplier: Consider a run an outlier if its row count is 
                             more than threshold_multiplier times the median row count
    """
    if len(runs) < 2:
        return []
    
    row_counts = [r['total_rows'] for r in runs]
    median_rows = sorted(row_counts)[len(row_counts) // 2]
    threshold = median_rows * threshold_multiplier
    
    outliers = [r for r in runs if r['total_rows'] > threshold]
    
    print(f"\n{'='*80}")
    print("OUTLIER DETECTION")
    print(f"{'='*80}")
    print(f"Median row count: {median_rows:,}")
    print(f"Threshold (>{threshold_multiplier}x median): {threshold:,.0f}")
    print(f"Outliers found: {len(outliers)}")
    
    return outliers


def count_related_records(db_manager, run_id):
    """Count related records for a run_id."""
    counts = {}
    
    try:
        # Count column metrics
        result = db_manager.conn.execute("""
            SELECT COUNT(*) FROM column_metrics WHERE run_id = ?
        """, [run_id]).fetchone()
        counts['column_metrics'] = result[0] if result else 0
        
        # Count issues
        result = db_manager.conn.execute("""
            SELECT COUNT(*) FROM issues WHERE run_id = ?
        """, [run_id]).fetchone()
        counts['issues'] = result[0] if result else 0
        
        # Count anomalies
        result = db_manager.conn.execute("""
            SELECT COUNT(*) FROM anomalies WHERE run_id = ?
        """, [run_id]).fetchone()
        counts['anomalies'] = result[0] if result else 0
        
    except Exception as e:
        print(f"  Error counting related records: {e}")
    
    return counts


def delete_run(db_manager, run_id, dry_run=False):
    """Delete a run and all its related records."""
    print(f"\n{'='*80}")
    print(f"{'DRY RUN: ' if dry_run else ''}DELETING RUN: {run_id}")
    print(f"{'='*80}")
    
    # Count related records
    counts = count_related_records(db_manager, run_id)
    print(f"Records to delete:")
    print(f"  - Column metrics: {counts['column_metrics']:,}")
    print(f"  - Issues: {counts['issues']:,}")
    print(f"  - Anomalies: {counts['anomalies']:,}")
    print(f"  - Profiling run: 1")
    
    if dry_run:
        print("\n⚠️  DRY RUN: No changes made.")
        return
    
    try:
        # Use a transaction to ensure atomicity
        db_manager.conn.execute("BEGIN TRANSACTION")
        
        # Delete in order: anomalies, issues, column_metrics, profiling_runs
        # (to respect foreign key constraints if any)
        
        deleted_anomalies = db_manager.conn.execute("""
            DELETE FROM anomalies WHERE run_id = ?
        """, [run_id]).rowcount
        
        deleted_issues = db_manager.conn.execute("""
            DELETE FROM issues WHERE run_id = ?
        """, [run_id]).rowcount
        
        deleted_metrics = db_manager.conn.execute("""
            DELETE FROM column_metrics WHERE run_id = ?
        """, [run_id]).rowcount
        
        deleted_run = db_manager.conn.execute("""
            DELETE FROM profiling_runs WHERE run_id = ?
        """, [run_id]).rowcount
        
        db_manager.conn.execute("COMMIT")
        
        print(f"\n✅ Successfully deleted:")
        print(f"  - Anomalies: {deleted_anomalies:,}")
        print(f"  - Issues: {deleted_issues:,}")
        print(f"  - Column metrics: {deleted_metrics:,}")
        print(f"  - Profiling run: {deleted_run}")
        
    except Exception as e:
        db_manager.conn.execute("ROLLBACK")
        print(f"\n❌ Error deleting run: {e}")
        raise


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description='Delete outlier profiling runs from DuckDB',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show runs for a dataset and identify outliers
  python scripts/delete_outlier_runs.py --dataset npidata_baseline --dry-run
  
  # Delete all outliers for a dataset
  python scripts/delete_outlier_runs.py --dataset npidata_baseline
  
  # Delete a specific run by ID
  python scripts/delete_outlier_runs.py --run-id <run-id>
        """
    )
    parser.add_argument(
        '--dataset',
        type=str,
        help='Dataset name to find outliers for'
    )
    parser.add_argument(
        '--run-id',
        type=str,
        help='Specific run_id to delete'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without deleting (default: False)'
    )
    parser.add_argument(
        '--threshold',
        type=float,
        default=2.0,
        help='Row count threshold multiplier for outlier detection (default: 2.0)'
    )
    parser.add_argument(
        '--db-path',
        type=str,
        default='data/storage/dq_metrics.duckdb',
        help='Path to DuckDB database (default: data/storage/dq_metrics.duckdb)'
    )
    parser.add_argument(
        '--yes',
        action='store_true',
        help='Auto-confirm deletion without prompting'
    )
    
    args = parser.parse_args()
    
    if not args.dataset and not args.run_id:
        parser.error("Must specify either --dataset or --run-id")
    
    # Connect to DuckDB
    try:
        db_manager = DuckDBManager(args.db_path)
    except Exception as e:
        print(f"ERROR: Could not connect to DuckDB: {e}")
        print(f"Database path: {args.db_path}")
        sys.exit(1)
    
    try:
        if args.run_id:
            # Delete specific run
            counts = count_related_records(db_manager, args.run_id)
            if sum(counts.values()) == 0:
                print(f"⚠️  Run ID '{args.run_id}' not found in database.")
                return
            
            delete_run(db_manager, args.run_id, dry_run=args.dry_run)
        
        elif args.dataset:
            # Find and delete outliers for dataset
            runs = show_runs_for_dataset(db_manager, args.dataset)
            
            if not runs:
                print(f"\n⚠️  No runs found for dataset '{args.dataset}'.")
                return
            
            if len(runs) == 1:
                print(f"\n⚠️  Only one run found for dataset '{args.dataset}'. Cannot identify outliers.")
                return
            
            outliers = identify_outliers(runs, threshold_multiplier=args.threshold)
            
            if not outliers:
                print(f"\n✅ No outliers found for dataset '{args.dataset}'.")
                return
            
            print(f"\n{'='*80}")
            print("OUTLIERS TO DELETE:")
            print(f"{'='*80}")
            for outlier in outliers:
                print(f"  - Run ID: {outlier['run_id']}")
                print(f"    Timestamp: {outlier['timestamp']}")
                print(f"    Rows: {outlier['total_rows']:,}")
                print(f"    Conformity: {outlier['overall_conformity']:.1f}%")
                print()
            
            if args.dry_run:
                print("⚠️  DRY RUN: Use --dataset without --dry-run to delete these outliers.")
            else:
                if args.yes:
                    response = 'yes'
                else:
                    response = input(f"\n⚠️  Delete {len(outliers)} outlier run(s)? (yes/no): ")
                
                if response.lower() in ['yes', 'y']:
                    for outlier in outliers:
                        delete_run(db_manager, outlier['run_id'], dry_run=False)
                    print(f"\n✅ Deleted {len(outliers)} outlier run(s).")
                else:
                    print("\n❌ Deletion cancelled.")
        
    except KeyboardInterrupt:
        print("\n\n❌ Operation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db_manager.close()


if __name__ == '__main__':
    main()

