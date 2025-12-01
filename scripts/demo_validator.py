#!/usr/bin/env python3
"""
ValidatorAgent Demo Script

This script demonstrates the ValidatorAgent's conformity validation capabilities using real NPI data.
Focuses on data conformity validation, rule violations analysis, performance testing,
and rule coverage analysis. For completeness analysis, see the ProfilerAgent demo.

Usage:
    # Auto-detect dataset from available files
    python scripts/demo_validator.py
    
    # Specify dataset path
    python scripts/demo_validator.py --dataset data/input/npidata.csv
    
    # Process full dataset without sampling
    python scripts/demo_validator.py --dataset data/input/npidata.csv --full-dataset
    
    # Limit to specific number of records
    python scripts/demo_validator.py --dataset data/input/npidata.csv --sample-size 5000
    
    # Enable verbose logging
    python scripts/demo_validator.py --dataset data/input/npidata.csv --verbose
"""

import sys
import os
import pandas as pd
import logging
import time
import argparse
from pathlib import Path

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents import ValidatorAgent
from src.data_access.file_handler import FileHandler

# Dataset file paths - modify these to use different datasets
DEFAULT_DATASET = "data/input/npidata_sample_100.csv"
SAMPLE_DATASET_100 = "data/input/npidata_sample_100.csv"
SAMPLE_DATASET_1000 = "data/input/npidata_sample_1000.csv"
FULL_DATASET = "data/input/npidata.csv"

# Available datasets in priority order (smallest to largest)
# The script will automatically use the first available dataset from this list
AVAILABLE_DATASETS = [
    SAMPLE_DATASET_100,
    SAMPLE_DATASET_1000,
    FULL_DATASET
]


def setup_logging():
    """Set up logging with timestamps."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )


def load_sample_data(file_path=DEFAULT_DATASET, sample_size=None):
    """Load sample data for validation testing."""
    if not Path(file_path).exists():
        print(f"❌ File not found: {file_path}")
        return None
    
    try:
        df = FileHandler.load_dataset(file_path)
        
        if sample_size and len(df) > sample_size:
            df = df.sample(n=sample_size, random_state=42)
        
        return df
        
    except Exception as e:
        print(f"❌ Error loading data: {e}")
        return None


def demo_detailed_conformity_statistics(validator, df):
    """Show detailed conformity statistics for each column with validation rules."""
    print(f"\nDETAILED CONFORMITY STATISTICS")
    print("=" * 80)
    
    # Get focused columns from config
    from src.data_access.file_handler import FileHandler
    focused_columns = FileHandler._get_focused_columns()
    
    # Get all columns that exist in dataset and have validation rules
    columns_with_rules = [col for col in focused_columns if col in df.columns and validator.has_validation_rule(col)]
    
    validation_results = {}
    
    # Sort columns alphabetically for consistent display
    columns_with_rules.sort()
    
    print(f"{'Column Name':<50} {'Conformity':<11} {'Violations':<11} {'Total Checked':<13} {'Rule Type'}")
    print("-" * 80)
    
    for column in columns_with_rules:
        # Use validator for conformity only
        conforming, non_conforming, violations = validator.check_conformity(df[column], column)
        total_checked = conforming + non_conforming
        conformity_rate = (conforming / total_checked * 100) if total_checked > 0 else 0
        
        # Get rule type
        rule = validator.get_validation_rule(column)
        rule_type = rule.rule_type if rule else 'N/A'
        
        validation_results[column] = {
            'conforming': conforming,
            'non_conforming': non_conforming,
            'total_checked': total_checked,
            'conformity_rate': conformity_rate,
            'violations': violations,
            'rule_type': rule_type
        }
        
        # Format column name (truncate if too long)
        col_display = column[:47] + "..." if len(column) > 50 else column
        
        print(f"{col_display:<50} {conformity_rate:>7.1f}%    {non_conforming:>7,}     {total_checked:>9,}     {rule_type}")
    
    return validation_results


def demo_top_violating_columns(validator, df, validation_results):
    """Show detailed analysis of columns with most violations."""
    print(f"\n\nTOP VIOLATING COLUMNS")
    print("=" * 80)
    
    if not validation_results:
        print("No validation results to analyze.")
        return
    
    # Sort columns by violation count (highest first)
    violating_columns = [(col, results) for col, results in validation_results.items() 
                        if results['non_conforming'] > 0]
    violating_columns.sort(key=lambda x: x[1]['non_conforming'], reverse=True)
    
    if not violating_columns:
        print("✅ No columns have violations! All data conforms to validation rules.")
        return
    
    print(f"Found {len(violating_columns)} columns with violations:")
    print()
    print(f"{'Column Name':<45} {'Violations':<11} {'Conformity':<11} {'Sample Violations'}")
    print("-" * 80)
    
    # Show top 10 violating columns
    for i, (column, results) in enumerate(violating_columns[:10]):
        # Format column name (truncate if too long)
        col_display = column[:42] + "..." if len(column) > 45 else column
        
        # Get sample violations (first 2)
        sample_violations = results['violations'][:2] if results['violations'] else []
        violations_str = ", ".join(str(v) for v in sample_violations)
        if len(violations_str) > 30:
            violations_str = violations_str[:27] + "..."
        
        print(f"{col_display:<45} {results['non_conforming']:>7,}    {results['conformity_rate']:>7.1f}%    {violations_str}")
    
    if len(violating_columns) > 10:
        remaining = len(violating_columns) - 10
        total_remaining_violations = sum(results['non_conforming'] for _, results in violating_columns[10:])
        print(f"... and {remaining} more columns with {total_remaining_violations:,} additional violations")
    
    # Rule coverage analysis
    print(f"\n\nRULE COVERAGE ANALYSIS")
    print("=" * 80)
    
    # Get focused columns from config
    from src.data_access.file_handler import FileHandler
    focused_columns = FileHandler._get_focused_columns()
    
    # Check which focused columns are in the dataset
    available_focused_columns = [col for col in focused_columns if col in df.columns]
    
    # Check which focused columns have validation rules
    focused_columns_with_rules = [col for col in available_focused_columns if validator.has_validation_rule(col)]
    
    # Coverage calculation based on focused columns
    if available_focused_columns:
        coverage_rate = (len(focused_columns_with_rules) / len(available_focused_columns)) * 100
    else:
        coverage_rate = 0
    
    print(f"Coverage: {len(focused_columns_with_rules)}/{len(available_focused_columns)} columns ({coverage_rate:.1f}%)")
    
    # Show rule types distribution for focused columns
    rule_types = {}
    for col in focused_columns_with_rules:
        rule = validator.get_validation_rule(col)
        rule_types[rule.rule_type] = rule_types.get(rule.rule_type, 0) + 1
    
    if rule_types:
        print("Rule types: " + ", ".join([f"{rule_type} ({count})" for rule_type, count in sorted(rule_types.items())]))
    else:
        print("No validation rules found for available focused columns")


def demo_performance_by_rule_type(validator, df):
    """Show performance statistics grouped by rule type."""
    print(f"\n\nPERFORMANCE BY RULE TYPE")
    print("=" * 80)
    
    # Get all columns with rules
    from src.data_access.file_handler import FileHandler
    focused_columns = FileHandler._get_focused_columns()
    test_columns = [col for col in focused_columns if col in df.columns and validator.has_validation_rule(col)]
    
    # Group columns by rule type for performance testing
    rule_type_performance = {}
    
    print(f"{'Rule Type':<12} {'Columns Tested':<15} {'Avg Performance':<16} {'Fastest Column':<20} {'Slowest Column'}")
    print("-" * 80)
    
    # Test each rule type
    rule_types = {}
    for col in test_columns[:15]:  # Test up to 15 columns for performance
        rule = validator.get_validation_rule(col)
        rule_type = rule.rule_type
        
        if rule_type not in rule_types:
            rule_types[rule_type] = []
        rule_types[rule_type].append(col)
    
    total_performance_data = []
    
    for rule_type, columns in rule_types.items():
        performances = []
        
        for col in columns:
            series = df[col]
            records_count = len(series)
            
            start_time = time.time()
            conforming, non_conforming, violations = validator.check_conformity(series, col)
            validation_time = time.time() - start_time
            
            records_per_sec = records_count / validation_time if validation_time > 0 else 0
            performances.append((col, records_per_sec))
            total_performance_data.append((col, records_per_sec, rule_type))
        
        if performances:
            avg_performance = sum(perf[1] for perf in performances) / len(performances)
            fastest = max(performances, key=lambda x: x[1])
            slowest = min(performances, key=lambda x: x[1])
            
            fastest_name = fastest[0][:17] + "..." if len(fastest[0]) > 20 else fastest[0]
            slowest_name = slowest[0][:17] + "..." if len(slowest[0]) > 20 else slowest[0]
            
            print(f"{rule_type:<12} {len(columns):>13}    {avg_performance:>12,.0f}/sec    {fastest_name:<20} {slowest_name}")
    
    # Overall performance summary
    if total_performance_data:
        all_performances = [data[1] for data in total_performance_data]
        avg_overall = sum(all_performances) / len(all_performances)
        fastest_overall = max(total_performance_data, key=lambda x: x[1])
        slowest_overall = min(total_performance_data, key=lambda x: x[1])
        
        print(f"\nOverall Performance Summary:")
        print(f"Average: {avg_overall:,.0f} records/second")
        print(f"Fastest: {fastest_overall[0]} ({fastest_overall[1]:,.0f} records/sec, {fastest_overall[2]} rule)")
        print(f"Slowest: {slowest_overall[0]} ({slowest_overall[1]:,.0f} records/sec, {slowest_overall[2]} rule)")
        print(f"Total columns tested: {len(total_performance_data)}")
    else:
        print("No performance data collected.")


def demo_bulk_validation_features(validator, dataset_path):
    """Demonstrate the new bulk validation capabilities."""
    print(f"\nBULK VALIDATION (Dataset Processing)")
    print("=" * 50)
    
    # Testing bulk validation
    
    # Test bulk validation
    start_time = time.time()
    bulk_result = validator.execute_bulk_validation(dataset_path)
    bulk_time = time.time() - start_time
    
    metadata = bulk_result['execution_metadata']
    stats = bulk_result['overall_stats']
    
    print(f"Dataset: {bulk_result['dataset_name']}")
    print(f"Processing mode: {metadata['processing_mode']}")
    print(f"Size: {bulk_result['total_rows']:,} rows, {metadata['file_size_mb']:.2f} MB")
    print(f"Processing time: {bulk_time:.2f}s")
    print(f"Overall conformity: {stats['overall_conformity']:.1f}%")
    print(f"Total violations: {stats['total_violations']:,}")
    
    return bulk_result


def export_validation_results(validator, bulk_result, dataset_path):
    """Export validation results to CSV and optionally JSON files."""
    from pathlib import Path
    
    dataset_name = Path(dataset_path).stem
    
    # Create results directory
    results_dir = Path("data/results")
    results_dir.mkdir(parents=True, exist_ok=True)
    
    # Export to CSV
    csv_path = results_dir / f"{dataset_name}_validation_details.csv"
    validator.export_validation_results_to_csv(bulk_result, dataset_name, str(csv_path))
    
    # Export to JSON  
    json_path = results_dir / f"{dataset_name}_validation_results.json"
    validator.export_validation_results_to_json(bulk_result, dataset_name, str(json_path))
    
    print(f"Validation results exported to: {csv_path}")
    print(f"Full results exported to: {json_path}")


def main():
    """Main demo function."""
    parser = argparse.ArgumentParser(description='Demo ValidatorAgent with NPI dataset')
    parser.add_argument(
        '--dataset', 
        help='Path to NPI CSV dataset file (optional - will auto-detect if not provided)'
    )
    parser.add_argument(
        '--sample-size',
        type=int,
        help='Limit dataset to N records for faster demo (default: auto-detect based on dataset size)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    parser.add_argument(
        '--full-dataset',
        action='store_true',
        help='Force processing of full dataset without sampling'
    )
    
    args = parser.parse_args()
    
    # Determine which dataset to use
    dataset_to_use = None
    
    if args.dataset:
        # Use dataset specified on command line
        dataset_to_use = args.dataset
        if not Path(dataset_to_use).exists():
            print(f"❌ Specified dataset not found: {dataset_to_use}")
            return 1
    else:
        # Auto-detect from available datasets
        for dataset in AVAILABLE_DATASETS:
            if Path(dataset).exists():
                dataset_to_use = dataset
                break
    
    if not dataset_to_use:
        print("❌ No datasets found. Please:")
        print("   1. Specify a dataset with --dataset <path>")
        print("   2. Or ensure sample data files exist in data/input/")
        print(f"\nExpected files: {AVAILABLE_DATASETS}")
        return 1
    
    print("Starting NPI Dataset Validation Demo...")
    print(f"Dataset: {dataset_to_use}")
    
    # Setup logging
    setup_logging()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        # Initialize ValidatorAgent (focused on conformity validation)
        validator = ValidatorAgent()
        
        # Determine sample size
        if args.full_dataset:
            sample_size = None  # Process full dataset
        elif args.sample_size:
            sample_size = args.sample_size
        else:
            # Auto-determine sample size based on dataset
            if dataset_to_use == FULL_DATASET:
                sample_size = 1000  # Default limit for full dataset demo
            else:
                sample_size = None  # Use entire sample dataset
        
        # Load dataset
        df = load_sample_data(dataset_to_use, sample_size)
        
        if df is None:
            return 1
        
        # Demo 1: Detailed conformity statistics
        validation_results = demo_detailed_conformity_statistics(validator, df)
        
        # Summary
        print(f"\nValidator demo completed successfully.")
        print(f"Dataset validated: {len(df):,} records processed")
        
        
    except Exception as e:
        print(f"\n❌ Demo failed: {str(e)}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
