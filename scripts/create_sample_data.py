#!/usr/bin/env python3
"""
Script to create sample data from the full NPI dataset.
Extracts the first 100 rows for testing purposes.
"""

import pandas as pd
import os
from pathlib import Path
import argparse
import sys


def create_sample_data(input_file: str, output_file: str, num_rows: int = 100):
    """
    Create a sample dataset from the first N rows of the input file.
    
    Args:
        input_file: Path to the full NPI dataset
        output_file: Path where sample data will be saved
        num_rows: Number of rows to extract (default: 100)
    """
    input_path = Path(input_file)
    output_path = Path(output_file)
    
    # Check if input file exists
    if not input_path.exists():
        print(f" Error: Input file not found: {input_path}")
        sys.exit(1)
    
    # Create output directory if it doesn't exist
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f" Reading first {num_rows} rows from: {input_path}")
    print(f" Output will be saved to: {output_path}")
    
    try:
        # Read only the first N rows to avoid loading the entire large file
        # Use chunksize to read efficiently
        chunk_iter = pd.read_csv(input_path, chunksize=num_rows, low_memory=False)
        sample_df = next(chunk_iter)  # Get the first chunk
        
        # If the chunk has fewer rows than requested, take what we have
        actual_rows = len(sample_df)
        print(f" Extracted {actual_rows} rows")
        
        # Display some basic info about the sample
        print(f" Columns: {len(sample_df.columns)}")
        print(f" Data size: {sample_df.memory_usage(deep=True).sum() / 1024 / 1024:.2f} MB")
        
        # Show column names
        print("\n Column names:")
        for i, col in enumerate(sample_df.columns, 1):
            print(f"  {i:2d}. {col}")
        
        # Save the sample data
        sample_df.to_csv(output_path, index=False)
        print(f"\n Sample data saved successfully!")
        print(f" Use this file for testing: {output_path}")
        
        # Show some sample statistics
        print(f"\n Sample Statistics:")
        print(f"  - Total rows: {len(sample_df)}")
        print(f"  - Total columns: {len(sample_df.columns)}")
        print(f"  - Missing values: {sample_df.isnull().sum().sum()}")
        print(f"  - Memory usage: {sample_df.memory_usage(deep=True).sum() / 1024:.1f} KB")
        
        return str(output_path)
        
    except Exception as e:
        print(f" Error creating sample data: {str(e)}")
        sys.exit(1)


def main():
    """Main function with command line interface."""
    parser = argparse.ArgumentParser(
        description="Create sample data from NPI dataset for testing",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument(
        "--input", "-i",
        default="data/input/npidata.csv",
        help="Path to the full NPI dataset file"
    )
    
    parser.add_argument(
        "--output", "-o", 
        default="data/input/npidata_sample_100.csv",
        help="Path for the output sample file"
    )
    
    parser.add_argument(
        "--rows", "-n",
        type=int,
        default=100,
        help="Number of rows to extract"
    )
    
    parser.add_argument(
        "--test-demo", "-t",
        action="store_true",
        help="Run the demo script with the created sample data"
    )
    
    args = parser.parse_args()
    
    print(" Creating NPI Sample Data")
    print("=" * 40)
    
    # Create sample data
    sample_file = create_sample_data(args.input, args.output, args.rows)
    
    # Optionally run the demo with the sample data
    if args.test_demo:
        print(f"\n Running demo with sample data...")
        demo_command = f"python demo_profiler.py --dataset {sample_file} --verbose"
        print(f"Command: {demo_command}")
        
        import subprocess
        try:
            result = subprocess.run(demo_command.split(), capture_output=True, text=True)
            if result.returncode == 0:
                print("Demo completed successfully!")
            else:
                print(f"Demo failed with return code: {result.returncode}")
                if result.stderr:
                    print(f"Error output: {result.stderr}")
        except Exception as e:
            print(f"Failed to run demo: {str(e)}")
    
    print(f"\n Sample data creation complete!")
    print(f" Sample file: {sample_file}")
    print(f"\n Usage examples:")
    print(f"  python demo_profiler.py --dataset {sample_file}")
    print(f"  python demo_profiler.py --dataset {sample_file} --output-json results.json")


if __name__ == "__main__":
    main()
