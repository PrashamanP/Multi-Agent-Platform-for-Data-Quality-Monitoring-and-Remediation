"""
Query and display DuckDB storage contents.

Usage:
    python scripts/query_duckdb.py                    # Show all recent data
    python scripts/query_duckdb.py --runs-only        # Show only profiling runs
    python scripts/query_duckdb.py --issues-only      # Show only issues
    python scripts/query_duckdb.py --anomalies-only   # Show only anomalies
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


def print_profiling_runs(db_manager, limit=10):
    """Display recent profiling runs."""
    print("\n" + "="*80)
    print("RECENT PROFILING RUNS")
    print("="*80)
    
    try:
        results = db_manager.conn.execute("""
            SELECT 
                run_id,
                dataset_name,
                profiling_timestamp,
                total_rows,
                overall_completeness,
                overall_uniqueness,
                overall_conformity
            FROM profiling_runs
            ORDER BY profiling_timestamp DESC
            LIMIT ?
        """, [limit]).fetchall()
        
        if not results:
            print("  No profiling runs found in database.")
            return
        
        print(f"{'Dataset':<30} {'Timestamp':<20} {'Rows':<10} {'Completeness':<12} {'Uniqueness':<12} {'Conformity':<10}")
        print("-" * 80)
        
        for run_id, dataset, timestamp, rows, completeness, uniqueness, conformity in results:
            print(f"{dataset:<30} {timestamp:<20} {rows:<10,} "
                  f"{completeness:<12.1f}% {uniqueness:<12.1f}% {conformity:<10.1f}%")
    
    except Exception as e:
        print(f"  Error querying profiling runs: {e}")


def print_issues(db_manager, limit=20):
    """Display recent issues."""
    print("\n" + "="*80)
    print("RECENT ISSUES")
    print("="*80)
    
    try:
        results = db_manager.conn.execute("""
            SELECT 
                run_id,
                dataset_name,
                column_name,
                issue_type,
                count,
                percentage,
                description
            FROM issues
            ORDER BY detected_at DESC
            LIMIT ?
        """, [limit]).fetchall()
        
        if not results:
            print("  No issues found in database.")
            return
        
        print(f"{'Column':<40} {'Type':<15} {'Count':<8} {'%':<8} {'Description':<60}")
        print("-" * 80)
        
        for run_id, dataset, column, issue_type, count, percentage, desc in results:
            # Truncate long descriptions
            desc = desc[:57] + "..." if len(desc) > 60 else desc
            print(f"{column:<40} {issue_type:<15} {count:<8,} {percentage:<8.1f}% {desc:<60}")
    
    except Exception as e:
        print(f"  Error querying issues: {e}")


def print_anomalies(db_manager, limit=20):
    """Display recent anomalies."""
    print("\n" + "="*80)
    print("RECENT ANOMALIES")
    print("="*80)
    
    try:
        results = db_manager.conn.execute("""
            SELECT 
                dataset_name,
                column_name,
                metric,
                current_value,
                baseline_value,
                delta,
                z_score,
                severity
            FROM anomalies
            ORDER BY detected_at DESC
            LIMIT ?
        """, [limit]).fetchall()
        
        if not results:
            print("  No anomalies found in database.")
            return
        
        print(f"{'Column':<30} {'Metric':<12} {'Severity':<10} {'Current':<10} {'Baseline':<10} {'Δ':<10} {'Z-Score':<10}")
        print("-" * 80)
        
        for dataset, column, metric, current, baseline, delta, z_score, severity in results:
            print(f"{column:<30} {metric:<12} {severity:<10} "
                  f"{current:<10.1f}% {baseline:<10.1f}% {delta:<10.1f} {z_score:<10.2f}")
    
    except Exception as e:
        print(f"  Error querying anomalies: {e}")


def print_statistics(db_manager):
    """Print database statistics."""
    print("\n" + "="*80)
    print("DATABASE STATISTICS")
    print("="*80)
    
    try:
        stats = db_manager.conn.execute("""
            SELECT 
                (SELECT COUNT(*) FROM profiling_runs) as run_count,
                (SELECT COUNT(*) FROM column_metrics) as metric_count,
                (SELECT COUNT(*) FROM issues) as issue_count,
                (SELECT COUNT(*) FROM anomalies) as anomaly_count,
                (SELECT COUNT(DISTINCT dataset_name) FROM profiling_runs) as dataset_count
        """).fetchone()
        
        run_count, metric_count, issue_count, anomaly_count, dataset_count = stats
        
        print(f"  Total Profiling Runs:      {run_count:,}")
        print(f"  Total Column Metrics:      {metric_count:,}")
        print(f"  Total Issues:              {issue_count:,}")
        print(f"  Total Anomalies:           {anomaly_count:,}")
        print(f"  Unique Datasets:           {dataset_count}")
        
    except Exception as e:
        print(f"  Error querying statistics: {e}")


def main():
    """Main query function."""
    parser = argparse.ArgumentParser(description='Query DuckDB storage contents')
    parser.add_argument(
        '--runs-only',
        action='store_true',
        help='Show only profiling runs'
    )
    parser.add_argument(
        '--issues-only',
        action='store_true',
        help='Show only issues'
    )
    parser.add_argument(
        '--anomalies-only',
        action='store_true',
        help='Show only anomalies'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=10,
        help='Limit number of results (default: 10)'
    )
    
    args = parser.parse_args()
    
    # Connect to DuckDB
    try:
        db_manager = DuckDBManager("data/storage/dq_metrics.duckdb")
    except Exception as e:
        print(f"ERROR: Could not connect to DuckDB: {e}")
        print(f"Database path: data/storage/dq_metrics.duckdb")
        print("\nMake sure you've run the profiler at least once.")
        sys.exit(1)
    
    try:
        print("="*80)
        print("DUCKDB DATABASE QUERY")
        print("="*80)
        
        # Print statistics unless viewing specific sections
        if not (args.runs_only or args.issues_only or args.anomalies_only):
            print_statistics(db_manager)
            print()  # Empty line
        
        # Print requested sections
        if args.runs_only:
            print_profiling_runs(db_manager, args.limit)
        elif args.issues_only:
            print_issues(db_manager, args.limit)
        elif args.anomalies_only:
            print_anomalies(db_manager, args.limit)
        else:
            # Show all
            print_profiling_runs(db_manager, args.limit)
            print_issues(db_manager, args.limit)
            print_anomalies(db_manager, args.limit)
        
        print("\n" + "="*80)
    
    finally:
        db_manager.close()


if __name__ == '__main__':
    main()
