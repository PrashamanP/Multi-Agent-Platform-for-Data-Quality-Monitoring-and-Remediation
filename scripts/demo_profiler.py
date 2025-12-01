"""
Demo script for the ProfilerAgent with NPI dataset.

Usage:
    python demo_profiler.py --dataset data/input/npidata.csv
"""

import argparse
import sys
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.profiler_agent import ProfilerAgent


def setup_logging():
    """Setup basic logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('profiler_demo.log')
        ]
    )


def print_profile_summary(profile_results):
    """Print a summary of the profile results."""
    profile = profile_results.dataset_profile
    
    print("\n" + "="*60)
    print("DATASET PROFILE SUMMARY")
    print("="*60)
    
    print(f"Dataset: {profile.dataset_name}")
    print(f"Rows: {profile.total_rows:,}")
    print(f"Columns: {profile.total_columns}")
    print(f"Data Size: {profile.data_size_mb:.2f} MB")
    print(f"Profiled at: {profile.profiling_timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
    
    print("\nOVERALL METRICS:")
    print(f"  Completeness: {profile.overall_completeness:.1f}%")
    print(f"  Uniqueness: {profile.overall_uniqueness:.1f}%")
    print(f"  Conformity: {profile.overall_conformity:.1f}%")
    
    print(f"\nISSUES DETECTED: {len(profile_results.issues)} total")


def print_detailed_column_statistics(profile_results):
    """Print detailed statistics table for each column with completeness, conformity, and uniqueness scores."""
    import yaml
    from pathlib import Path
    
    profile = profile_results.dataset_profile
    
    print(f"\nDETAILED COLUMN STATISTICS")
    print("=" * 120)
    
    # Load focused columns from config
    try:
        config_path = Path(__file__).parent.parent / "config" / "columns.yaml"
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        focused_columns = config.get('focused_columns', [])
    except Exception as e:
        print(f"Warning: Could not load focused columns config: {str(e)}")
        focused_columns = None
    
    # Determine which columns to display
    if focused_columns:
        # Filter to only focused columns that exist in the profile
        columns_to_display = [
            (col_name, profile.column_metrics[col_name]) 
            for col_name in focused_columns 
            if col_name in profile.column_metrics
        ]
        # Sort focused columns by overall quality (worst first) for better visibility
        columns_to_display.sort(key=lambda x: (x[1].conformity_score + x[1].completeness_score) / 2)
    else:
        # Fallback: display all columns sorted by quality
        columns_to_display = sorted(
            profile.column_metrics.items(),
            key=lambda x: (x[1].conformity_score + x[1].completeness_score) / 2
        )
    
    # Print table header
    print(f"{'Column Name':<45} {'Completeness':<13} {'Conformity':<12} {'Uniqueness':<12} {'Issues'}")
    print("-" * 120)
    
    for col_name, metrics in columns_to_display:
        # Format column name (truncate if too long)
        col_display = col_name[:42] + "..." if len(col_name) > 45 else col_name
        
        # Format completeness
        completeness_str = f"{metrics.completeness_score:.1f}%"
        
        # Format conformity
        conformity_str = f"{metrics.conformity_score:.1f}%"
        
        # Format uniqueness (handle None case)
        if metrics.uniqueness_score is not None:
            uniqueness_str = f"{metrics.uniqueness_score:.1f}%"
        else:
            uniqueness_str = "N/A"
        
        # Format issues summary
        issues = []
        if metrics.null_count > 0:
            issues.append(f"{metrics.null_count:,} nulls")
        if metrics.non_conforming_count > 0:
            issues.append(f"{metrics.non_conforming_count:,} invalid")
        if metrics.duplicate_count and metrics.duplicate_count > 0:
            issues.append(f"{metrics.duplicate_count:,} dups")
        
        issues_str = ", ".join(issues) if issues else "None"
        if len(issues_str) > 55:
            issues_str = issues_str[:52] + "..."
        
        print(f"{col_display:<45} {completeness_str:>10}   {conformity_str:>9}   {uniqueness_str:>9}   {issues_str}")
    
    print(f"\nTotal columns displayed: {len(columns_to_display)}")
    if focused_columns:
        missing_columns = [col for col in focused_columns if col not in profile.column_metrics]
        if missing_columns:
            print(f"Note: {len(missing_columns)} focused columns not found in dataset")


def export_column_details_to_csv(profile_results, output_path):
    """Export detailed column metrics to CSV file for all focused columns."""
    import csv
    import yaml
    from pathlib import Path
    from datetime import datetime
    
    profile = profile_results.dataset_profile
    
    # Load focused columns from config
    try:
        config_path = Path(__file__).parent.parent / "config" / "columns.yaml"
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        focused_columns = config.get('focused_columns', [])
    except Exception as e:
        print(f"Warning: Could not load focused columns config: {str(e)}")
        # Fallback to all columns sorted by quality
        focused_columns = None
    
    # Determine which columns to export
    if focused_columns:
        # Filter to only focused columns that exist in the profile
        columns_to_export = [
            (col_name, profile.column_metrics[col_name]) 
            for col_name in focused_columns 
            if col_name in profile.column_metrics
        ]
        # Sort focused columns by conformity score (worst first) for better visibility
        columns_to_export.sort(key=lambda x: (x[1].conformity_score, x[1].completeness_score))
    else:
        # Fallback: export all columns sorted by quality
        columns_to_export = sorted(
            profile.column_metrics.items(),
            key=lambda x: (x[1].conformity_score, x[1].completeness_score)
        )
    
    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['Column Name', 'Completeness %', 'Null Count', 'Uniqueness %', 'Duplicate Count', 
                     'Conformity %', 'Invalid Count']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        writer.writeheader()
        for col_name, metrics in columns_to_export:
            uniqueness_str = f"{metrics.uniqueness_score:.2f}%" if metrics.uniqueness_score is not None else "N/A"
            duplicate_str = str(metrics.duplicate_count) if metrics.uniqueness_score is not None else "N/A"
            
            writer.writerow({
                'Column Name': col_name,
                'Completeness %': f"{metrics.completeness_score:.2f}%",
                'Null Count': metrics.null_count,
                'Uniqueness %': uniqueness_str,
                'Duplicate Count': duplicate_str,
                'Conformity %': f"{metrics.conformity_score:.2f}%",
                'Invalid Count': metrics.non_conforming_count
            })
    
    print(f"Column quality details exported to: {output_path}")
    print(f"Total columns exported: {len(columns_to_export)}")
    if focused_columns:
        missing_columns = [col for col in focused_columns if col not in profile.column_metrics]
        if missing_columns:
            print(f"Note: {len(missing_columns)} focused columns not found in dataset: {missing_columns[:5]}{'...' if len(missing_columns) > 5 else ''}")

def export_results_to_json(profile_results, output_path):
    """Export profile results to JSON for further analysis."""
    import json
    from dataclasses import asdict
    
    def serialize_datetime(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object {obj} is not JSON serializable")
    
    # Convert to dict (simplified for JSON export)
    export_data = {
        'dataset_name': profile_results.dataset_profile.dataset_name,
        'total_rows': profile_results.dataset_profile.total_rows,
        'total_columns': profile_results.dataset_profile.total_columns,
        'overall_metrics': {
            'completeness': profile_results.dataset_profile.overall_completeness,
            'uniqueness': profile_results.dataset_profile.overall_uniqueness,
            'conformity': profile_results.dataset_profile.overall_conformity
        },
        'issues': [
            {
                'column': issue.column_name,
                'type': issue.issue_type,
                'count': issue.count,
                'percentage': issue.percentage,
                'description': issue.description,
                'examples': issue.examples[:5] if issue.examples else []
            }
            for issue in profile_results.issues
        ],
        'execution_metadata': profile_results.execution_metadata,
        'profiled_at': profile_results.dataset_profile.profiling_timestamp.isoformat()
    }
    
    with open(output_path, 'w') as f:
        json.dump(export_data, f, indent=2, default=serialize_datetime)
    
    print(f"\nResults exported to: {output_path}")


def main():
    """Main demo function."""
    parser = argparse.ArgumentParser(description='Demo ProfilerAgent with NPI dataset')
    parser.add_argument(
        '--dataset', 
        required=True,
        help='Path to NPI CSV dataset file'
    )
    parser.add_argument(
        '--output-json',
        help='Path to export results as JSON (optional)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Check if dataset file exists
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"Error: Dataset file not found: {dataset_path}")
        sys.exit(1)
    
    print("Starting NPI Dataset Quality Profiling...")
    print(f"Dataset: {dataset_path}")
    
    try:
        # Initialize profiler agent
        profiler = ProfilerAgent()
        
        # Execute profiling
        print("\nExecuting profiler agent...")
        results = profiler.execute(str(dataset_path))
        
        # Print results
        print_profile_summary(results)
        
        # Print detailed column statistics table
        print_detailed_column_statistics(results)
        
        # Export column details to CSV
        results_dir = Path("data/results")
        results_dir.mkdir(parents=True, exist_ok=True)
        columns_csv_path = results_dir / f"{dataset_path.stem}_column_details.csv"
        export_column_details_to_csv(results, str(columns_csv_path))
        
        # Export to JSON if requested
        if args.output_json:
            export_results_to_json(results, args.output_json)
        
        # Print execution summary
        exec_time = results.execution_metadata.get('execution_time_seconds', 0)
        print(f"\nProfiling completed successfully in {exec_time:.2f} seconds")
        
        sys.exit(0)
    
    except Exception as e:
        print(f"\nError during profiling: {str(e)}")
        logging.exception("Profiling failed")
        sys.exit(1)


if __name__ == '__main__':
    main()