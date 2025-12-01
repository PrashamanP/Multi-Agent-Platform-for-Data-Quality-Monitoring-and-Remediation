"""
Clear old anomalies from DuckDB that reference outdated baselines.

Usage:
    python scripts/clear_old_anomalies.py --dataset npidata_baseline
    python scripts/clear_old_anomalies.py --dataset npidata_baseline --dry-run
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


def clear_anomalies_for_dataset(db_manager, dataset_name, dry_run=False):
    """Clear all anomalies for a dataset."""
    print(f"\n{'='*80}")
    print(f"{'DRY RUN: ' if dry_run else ''}CLEARING ANOMALIES FOR: {dataset_name}")
    print(f"{'='*80}")
    
    try:
        # Count existing anomalies
        count_result = db_manager.conn.execute("""
            SELECT COUNT(*) FROM anomalies WHERE dataset_name = ?
        """, [dataset_name]).fetchone()
        
        anomaly_count = count_result[0] if count_result else 0
        
        if anomaly_count == 0:
            print(f"  No anomalies found for dataset '{dataset_name}'.")
            return
        
        print(f"  Found {anomaly_count:,} anomaly/anomalies to delete")
        
        # Show sample of what will be deleted
        sample = db_manager.conn.execute("""
            SELECT column_name, metric, current_value, baseline_value, delta, detected_at
            FROM anomalies
            WHERE dataset_name = ?
            ORDER BY detected_at DESC
            LIMIT 5
        """, [dataset_name]).fetchall()
        
        if sample:
            print(f"\n  Sample anomalies to delete:")
            print(f"  {'Column':<30} {'Metric':<12} {'Current':<10} {'Baseline':<10} {'Delta':<10}")
            print(f"  {'-'*80}")
            for col, metric, current, baseline, delta, detected_at in sample:
                print(f"  {col:<30} {metric:<12} {current:<10.1f}% {baseline:<10.1f}% {delta:<10.1f}")
        
        if dry_run:
            print(f"\n⚠️  DRY RUN: No changes made.")
            return
        
        # Delete anomalies
        db_manager.conn.execute("BEGIN TRANSACTION")
        deleted = db_manager.conn.execute("""
            DELETE FROM anomalies WHERE dataset_name = ?
        """, [dataset_name]).rowcount
        db_manager.conn.execute("COMMIT")
        
        print(f"\n✅ Successfully deleted {deleted:,} anomaly/anomalies")
        
    except Exception as e:
        db_manager.conn.execute("ROLLBACK")
        print(f"\n❌ Error clearing anomalies: {e}")
        raise


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description='Clear old anomalies from DuckDB',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what will be deleted
  python scripts/clear_old_anomalies.py --dataset npidata_baseline --dry-run
  
  # Clear all anomalies for a dataset
  python scripts/clear_old_anomalies.py --dataset npidata_baseline
  
  # Clear all anomalies (all datasets)
  python scripts/clear_old_anomalies.py --all
        """
    )
    parser.add_argument(
        '--dataset',
        type=str,
        help='Dataset name to clear anomalies for'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Clear anomalies for all datasets'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without deleting (default: False)'
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
    
    if not args.dataset and not args.all:
        parser.error("Must specify either --dataset or --all")
    
    # Connect to DuckDB
    try:
        db_manager = DuckDBManager(args.db_path)
    except Exception as e:
        print(f"ERROR: Could not connect to DuckDB: {e}")
        print(f"Database path: {args.db_path}")
        sys.exit(1)
    
    try:
        if args.all:
            # Get all datasets with anomalies
            datasets = db_manager.conn.execute("""
                SELECT DISTINCT dataset_name FROM anomalies
            """).fetchall()
            
            if not datasets:
                print("No anomalies found in database.")
                return
            
            dataset_names = [d[0] for d in datasets]
            print(f"Found anomalies for {len(dataset_names)} dataset(s): {', '.join(dataset_names)}")
            
            if args.dry_run:
                for dataset_name in dataset_names:
                    clear_anomalies_for_dataset(db_manager, dataset_name, dry_run=True)
                print("\n⚠️  DRY RUN: Use --all without --dry-run to delete these anomalies.")
            else:
                if args.yes:
                    response = 'yes'
                else:
                    response = input(f"\n⚠️  Delete all anomalies for {len(dataset_names)} dataset(s)? (yes/no): ")
                
                if response.lower() in ['yes', 'y']:
                    for dataset_name in dataset_names:
                        clear_anomalies_for_dataset(db_manager, dataset_name, dry_run=False)
                    print(f"\n✅ Deleted anomalies for {len(dataset_names)} dataset(s).")
                else:
                    print("\n❌ Deletion cancelled.")
        
        elif args.dataset:
            clear_anomalies_for_dataset(db_manager, args.dataset, dry_run=args.dry_run)
        
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

