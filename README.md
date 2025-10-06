# Multi-Agent System for Data Quality Monitoring and Remediation

A multi-agent system designed to analyze National Provider Identifier (NPI) data. 

## Profiler Agent

The Profiler Agent is responsible for comprehensive data quality profiling of NPI datasets. It computes three primary data quality dimensions: completeness, conformity and uniqueness.

### Data Quality Metrics

#### 1. Completeness Score
- Measures percentage of non-null values per column
- Uses intelligent null detection that recognizes various null-like values:
  - Standard nulls: `NaN`, `None`, `null`, `""`, `"-"`
  - Business nulls: `"N/A"`, `"UNAVAILABLE"`, `"UNKNOWN"`
  - Special handling for EIN field where `"<UNAVAIL>"` is considered valid

#### 2. Conformity Score
- Validates data against format rules defined in `validation_rules.yaml`
- Supports multiple validation types:
  - **Regex patterns**: For format validation (e.g., NPI must be 10 digits)
  - **Enumerated values**: For categorical data (e.g., Entity Type Code: 1 or 2)
  - **Length constraints**: For text fields
  - **Date validation**: With multiple format support
  - **Phone number validation**: With flexible formatting

#### 3. Uniqueness Score
- Currently computed specifically for NPI column
- Identifies and counts duplicate values
- Calculates percentage of unique values

### Conditional Business Logic

The ProfilerAgent implements sophisticated business rules for NPI data:

- **Entity Type Code 1 (Individual)**: Requires `Provider Last Name` and `Provider First Name`
- **Entity Type Code 2 (Organization)**: Requires `Provider Organization Name`
- **Universal Required Fields**: `NPI` and `Entity Type Code` are always required

### Performance Features

#### Chunked Processing
- Automatically enabled for datasets larger than 1GB
- Configurable chunk size (default: 50,000 rows)
- Memory-efficient processing with progress tracking

#### Optimization Settings
- **Parallel Processing**: Multi-threaded column analysis
- **Data Type Optimization**: Automatic dtype optimization for memory efficiency

## Installation and Setup

### Installation
```bash
# Clone the repository
git clone https://github.com/PrashamanP/techstra-capstone
cd techstra-capstone

# Create virtual environment
python -m venv env
source env/bin/activate  # On Windows: env\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

```bash
# Run the demo script
python scripts/demo_profiler.py --dataset data/input/npidata_sample_100.csv

# With verbose output
python scripts/demo_profiler.py --dataset data/input/npidata_sample_100.csv --verbose
```

### Output Files
The system generates several output files:
- **Column Details CSV**: Detailed metrics for each column (`data/results/`)
- **Profiling Logs**: Execution logs (`profiler_demo.log`)