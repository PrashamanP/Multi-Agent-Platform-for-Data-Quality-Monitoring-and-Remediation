"""
Demo script for the Fix Recommendation Agent with NPI dataset.

Usage:
    python scripts/demo_fix_recommendation.py --dataset data/input/npidata.csv
"""

import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.profiler_agent import ProfilerAgent
from src.agents.anomaly_detection_agent import AnomalyDetectionAgent
from src.agents.fix_recommendation_agent import FixRecommendationAgent
from src.agents.fix_executor_agent import FixExecutorAgent


def setup_logging():
    """Setup basic logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )


def print_recommendations_summary(results):
    """Print a summary of the recommendation results."""
    # Filter out completeness recommendations (not fixing completeness issues for now)
    recommendations = [rec for rec in results.recommendations if rec.issue_type != "completeness"]
    
    print("\n" + "="*80)
    print("FIX RECOMMENDATION SUMMARY")
    print("="*80)
    
    print(f"Dataset: {results.dataset_name}")
    print(f"Total Recommendations: {len(recommendations)}")
    
    if not recommendations:
        print("\nNo recommendations generated.")
        return
    
    print("\n" + "-"*80)
    print("RECOMMENDATIONS:")
    print("-"*80)
    
    # Group by issue type
    grouped = {}
    for rec in recommendations:
        if rec.issue_type not in grouped:
            grouped[rec.issue_type] = []
        grouped[rec.issue_type].append(rec)
    
    for issue_type, recs in grouped.items():
        print(f"\n{issue_type.upper()} ISSUES ({len(recs)} recommendations):")
        for i, rec in enumerate(recs, 1):
            print(f"\n{i}. {rec.column_name}")
            print(f"   Strategy: {rec.fix_strategy}")
            print(f"   Description: {rec.fix_description}")
            print(f"   Confidence: {rec.confidence_score:.1%}")
            print(f"   Impact: {rec.estimated_impact}")
            if rec.suggested_value:
                print(f"   Suggested Value: {rec.suggested_value}")


def export_recommendations_to_json(results, output_path):
    """Export recommendation results to JSON for further analysis."""
    import json
    
    export_data = {
        'dataset_name': results.dataset_name,
        'run_id': results.run_id,
        'total_recommendations': len(results.recommendations),
        'actionable_count': results.actionable_count,
        'requires_review_count': results.requires_review_count,
        'recommendations': [
            {
                'recommendation_id': rec.recommendation_id,
                'issue_id': rec.issue_id,
                'column_name': rec.column_name,
                'issue_type': rec.issue_type,
                'fix_strategy': rec.fix_strategy,
                'fix_description': rec.fix_description,
                'confidence_score': rec.confidence_score,
                'estimated_impact': rec.estimated_impact,
                'actionable': rec.actionable,
                'suggested_value': rec.suggested_value,
                'rationale': rec.rationale,
                'prerequisites': rec.prerequisites,
                'risk_assessment': rec.risk_assessment,
                'created_at': rec.created_at.isoformat()
            }
            for rec in results.recommendations
        ],
        'execution_metadata': results.execution_metadata
    }
    
    with open(output_path, 'w') as f:
        json.dump(export_data, f, indent=2, default=str)
    
    print(f"\nRecommendations exported to: {output_path}")


def main():
    """Main demo function."""
    parser = argparse.ArgumentParser(description='Demo Fix Recommendation Agent with NPI dataset')
    parser.add_argument(
        '--dataset', 
        required=True,
        help='Path to NPI CSV dataset file'
    )
    parser.add_argument(
        '--skip-profiling',
        action='store_true',
        help='Skip profiler run and reuse the most recent stored profile'
    )
    parser.add_argument(
        '--skip-anomaly-detection',
        action='store_true',
        help='Skip anomaly detection and only process issues from profiling'
    )
    parser.add_argument(
        '--output-json',
        help='Path to export results as JSON (optional)'
    )
    parser.add_argument(
        '--output-file',
        help='Explicit output file for fixed dataset (optional). If omitted, writes <stem>_fixed<ext> next to input.'
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
    
    print("="*80)
    print("NPI DATASET FIX RECOMMENDATION DEMO")
    print("="*80)
    print(f"Dataset: {dataset_path}")
    
    try:
        # Step 1: Run Profiler
        profile_results = None
        if not args.skip_profiling:
            print("\n[Step 1/3] Running Profiler Agent...")
            profiler = ProfilerAgent()
            profile_results = profiler.execute(str(dataset_path))
            print(f"✓ Profiling completed. Found {len(profile_results.issues)} issues")
        else:
            print("\n[Step 1/3] Skipping profiler run (using existing profile)")
        
        # Step 2: Run Anomaly Detection
        anomaly_results = None
        if not args.skip_anomaly_detection:
            print("\n[Step 2/3] Running Anomaly Detection Agent...")
            try:
                anomaly_agent = AnomalyDetectionAgent()
                anomaly_results = anomaly_agent.execute(
                    profile_results=profile_results,
                    dataset_name=dataset_path.stem
                )
                print(f"✓ Anomaly detection completed. Found {len(anomaly_results.anomalies)} anomalies")
            except RuntimeError as e:
                if "DuckDB" in str(e):
                    print(f"⚠ Anomaly detection skipped: DuckDB storage not available")
                else:
                    raise
        else:
            print("\n[Step 2/3] Skipping anomaly detection")
        
        # Step 3: Generate Fix Recommendations
        print("\n[Step 3/3] Running Fix Recommendation Agent...")
        fix_agent = FixRecommendationAgent()
        recommendation_results = fix_agent.execute(
            profile_results=profile_results,
            anomaly_results=anomaly_results,
            dataset_path=str(dataset_path)  # Pass dataset path for row-by-row analysis
        )
        
        # Print summary
        print_recommendations_summary(recommendation_results)
        
        # Export to JSON if requested
        if args.output_json:
            export_recommendations_to_json(recommendation_results, args.output_json)
        
        # Apply fixes and write updated dataset (new file)
        print("\n[Step 4/4] Applying fixes and writing updated dataset...")
        exec_agent = FixExecutorAgent()
        if args.output_file:
            output_path = Path(args.output_file)
        else:
            output_path = dataset_path.with_name(f"{dataset_path.stem}_fixed{dataset_path.suffix}")
        exec_results = exec_agent.execute(
            recommendations=recommendation_results,
            dataset_path=str(dataset_path),
            output_path=str(output_path),
            apply_only_actionable=True
        )
        print(f"✓ Fixed dataset written to: {exec_results.fixed_path}")
        sidecar = output_path.with_name(f"{output_path.stem}_fix_recommendations.csv")
        print(f"Sidecar written to: {sidecar}")

        # Print execution summary
        exec_time = recommendation_results.execution_metadata.get('execution_time_seconds', 0)
        print(f"\n{'='*80}")
        print(f"Fix recommendation completed successfully in {exec_time:.2f} seconds")
        print(f"{'='*80}\n")
        
        sys.exit(0)
    
    except Exception as e:
        print(f"\nError during fix recommendation: {str(e)}")
        logging.exception("Fix recommendation failed")
        sys.exit(1)


if __name__ == '__main__':
    main()
