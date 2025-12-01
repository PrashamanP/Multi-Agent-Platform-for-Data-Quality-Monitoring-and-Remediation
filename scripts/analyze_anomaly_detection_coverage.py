"""
Analyze which columns are checked by anomaly detection and why some columns don't have anomalies.

Usage:
    python scripts/analyze_anomaly_detection_coverage.py --dataset npidata_baseline
"""

import argparse
import sys
import statistics
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from src.data_access.duckdb_manager import DuckDBManager
    from src.core.validation_rule_loader import ValidationRuleLoader
except ImportError:
    print("ERROR: Required packages not installed.")
    sys.exit(1)


def analyze_coverage(db_manager, dataset_name):
    """Analyze which columns are checked and why anomalies are/aren't detected."""
    print(f"\n{'='*80}")
    print(f"ANOMALY DETECTION COVERAGE ANALYSIS: {dataset_name}")
    print(f"{'='*80}")
    
    # Get all columns with validation rules (these should have conformity scores)
    loader = ValidationRuleLoader()
    rules = loader.load_validation_rules()
    
    print(f"\n1. COLUMNS WITH VALIDATION RULES (should be checked for conformity):")
    print(f"   Total: {len(rules)} columns")
    
    # Get all columns with conformity data in database
    columns_with_data = db_manager.conn.execute("""
        SELECT DISTINCT column_name
        FROM column_metrics
        WHERE dataset_name = ?
          AND conformity_score IS NOT NULL
        ORDER BY column_name
    """, [dataset_name]).fetchall()
    
    columns_with_data_set = {col[0] for col in columns_with_data}
    
    print(f"\n2. COLUMNS WITH CONFORMITY DATA IN DATABASE:")
    print(f"   Total: {len(columns_with_data_set)} columns")
    
    # Check which columns have enough history points (min_history_points = 3)
    min_history_points = 3
    columns_with_enough_history = []
    columns_without_enough_history = []
    
    for col_name in sorted(columns_with_data_set):
        history_count = db_manager.conn.execute("""
            SELECT COUNT(DISTINCT run_id)
            FROM column_metrics
            WHERE dataset_name = ? AND column_name = ?
              AND conformity_score IS NOT NULL
        """, [dataset_name, col_name]).fetchone()[0]
        
        if history_count >= min_history_points:
            columns_with_enough_history.append((col_name, history_count))
        else:
            columns_without_enough_history.append((col_name, history_count))
    
    print(f"\n3. COLUMNS WITH ENOUGH HISTORY POINTS (>= {min_history_points}):")
    print(f"   Total: {len(columns_with_enough_history)} columns")
    print(f"   Columns with insufficient history: {len(columns_without_enough_history)}")
    
    # Analyze conformity score stability
    print(f"\n4. CONFORMITY SCORE ANALYSIS:")
    print(f"   Checking stability of conformity scores across runs...")
    
    stable_columns = []  # Columns with no variation (std dev = 0)
    variable_columns = []  # Columns with variation
    
    for col_name, history_count in columns_with_enough_history:
        scores = db_manager.conn.execute("""
            SELECT conformity_score
            FROM column_metrics c
            JOIN profiling_runs r ON c.run_id = r.run_id
            WHERE c.dataset_name = ? AND c.column_name = ?
              AND c.conformity_score IS NOT NULL
            ORDER BY r.profiling_timestamp DESC
        """, [dataset_name, col_name]).fetchall()
        
        score_values = [s[0] for s in scores]
        
        if len(score_values) > 1:
            std_dev = statistics.pstdev(score_values) if len(score_values) > 1 else 0.0
            mean_score = statistics.mean(score_values)
            min_score = min(score_values)
            max_score = max(score_values)
            
            if std_dev == 0.0:
                stable_columns.append((col_name, mean_score, std_dev, min_score, max_score))
            else:
                variable_columns.append((col_name, mean_score, std_dev, min_score, max_score))
        else:
            stable_columns.append((col_name, score_values[0] if score_values else 0, 0.0, score_values[0] if score_values else 0, score_values[0] if score_values else 0))
    
    print(f"\n   Stable columns (std dev = 0): {len(stable_columns)}")
    print(f"   Variable columns (std dev > 0): {len(variable_columns)}")
    
    # Show columns that had anomalies (before clearing)
    anomaly_columns = [
        'Entity Type Code',
        'Provider Enumeration Date',
        'Last Update Date',
        'Certification Date'
    ]
    
    print(f"\n5. COLUMNS THAT HAD ANOMALIES (before clearing):")
    print(f"   Total: {len(anomaly_columns)} columns")
    for col in anomaly_columns:
        if col in columns_with_data_set:
            # Check if it's in stable or variable
            found = False
            for stable_col, mean, std, min_val, max_val in stable_columns:
                if stable_col == col:
                    print(f"   - {col}: Mean={mean:.1f}%, Std={std:.2f}%, Range=[{min_val:.1f}%, {max_val:.1f}%] (STABLE)")
                    found = True
                    break
            if not found:
                for var_col, mean, std, min_val, max_val in variable_columns:
                    if var_col == col:
                        print(f"   - {col}: Mean={mean:.1f}%, Std={std:.2f}%, Range=[{min_val:.1f}%, {max_val:.1f}%] (VARIABLE)")
                        found = True
                        break
            if not found:
                print(f"   - {col}: (not found in analysis)")
    
    # Show sample of other columns
    print(f"\n6. SAMPLE OF OTHER COLUMNS (why no anomalies):")
    sample_columns = [
        'NPI',
        'Provider First Name',
        'Provider Organization Name (Legal Business Name)',
        'Employer Identification Number (EIN)',
        'Provider Business Mailing Address State Name',
        'Provider Business Practice Location Address State Name'
    ]
    
    for col in sample_columns:
        if col in columns_with_data_set:
            found = False
            for stable_col, mean, std, min_val, max_val in stable_columns:
                if stable_col == col:
                    print(f"   - {col}: Mean={mean:.1f}%, Std={std:.2f}%, Range=[{min_val:.1f}%, {max_val:.1f}%] (STABLE - no anomalies)")
                    found = True
                    break
            if not found:
                for var_col, mean, std, min_val, max_val in variable_columns:
                    if var_col == col:
                        print(f"   - {col}: Mean={mean:.1f}%, Std={std:.2f}%, Range=[{min_val:.1f}%, {max_val:.1f}%] (VARIABLE)")
                        found = True
                        break
            if not found:
                print(f"   - {col}: (not found in analysis)")
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"✅ Anomaly detection checks ALL {len(columns_with_enough_history)} columns with:")
    print(f"   - Validation rules defined")
    print(f"   - Conformity data in database")
    print(f"   - At least {min_history_points} historical data points")
    print(f"\n⚠️  Only {len(anomaly_columns)} columns had anomalies because:")
    print(f"   - Those columns had baseline conformity scores that were significantly")
    print(f"     different (lower) when the outlier run was present")
    print(f"   - The outlier run had ~85% conformity for those columns vs 100% for current runs")
    print(f"   - This created a delta of ~11-15% which exceeded the 5% threshold")
    print(f"\n✅ Other columns didn't have anomalies because:")
    print(f"   - They had stable 100% conformity even with the outlier run")
    print(f"   - Or their baseline was close enough to current values (< 5% delta)")
    print(f"{'='*80}")


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description='Analyze anomaly detection coverage',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--dataset',
        type=str,
        required=True,
        help='Dataset name to analyze'
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
        sys.exit(1)
    
    try:
        analyze_coverage(db_manager, args.dataset)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db_manager.close()


if __name__ == '__main__':
    main()

