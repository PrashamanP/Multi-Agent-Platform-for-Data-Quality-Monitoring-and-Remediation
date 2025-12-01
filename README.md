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

### Full pipeline (all four agents)
```bash
# Uses bundled sample data, writes fixed copy plus sidecar CSVs
python scripts/run_pipeline.py \
  --dataset data/input/npidata_sample_100.csv \
  --output-file data/output/npidata_sample_100_fixed.csv
```

- For the full baseline dataset, point to the larger file (may take longer):  
  `python scripts/run_pipeline.py --dataset data/input/npidata_baseline.csv --output-file data/output/npidata_baseline_fixed.csv`

### Profiler demo
```bash
python scripts/demo_profiler.py --dataset data/input/npidata_sample_100.csv
python scripts/demo_profiler.py --dataset data/input/npidata_sample_100.csv --verbose
```

### DuckDB Storage Setup
- Profiling metrics are automatically persisted to DuckDB at `data/storage/dq_metrics.duckdb` (configure via `config/storage.yaml`).
- Schemas are created lazily on first use; no manual migrations are required.
- Ensure the `duckdb` Python package is installed (`pip install duckdb`) when enabling persistence.

### Anomaly Detection Demo
```bash
# Profile the dataset and immediately run anomaly detection
python scripts/demo_anomaly_detection.py --dataset data/input/npidata_sample_1000.csv --verbose

# Reuse the latest persisted run without re-profiling
python scripts/demo_anomaly_detection.py --dataset data/input/npidata_sample_1000.csv --skip-profiling
```

The anomaly agent compares the current profiling run against historical metrics stored in DuckDB and surfaces significant shifts for review.

### Fix Recommendation Demo
```bash
# Run complete pipeline: profiler → anomaly detection → fix recommendations
python scripts/demo_fix_recommendation.py --dataset data/input/npidata_sample_100.csv

# Skip anomaly detection (if DuckDB not available)
python scripts/demo_fix_recommendation.py --dataset data/input/npidata_sample_100.csv --skip-anomaly-detection

# Export recommendations to JSON
python scripts/demo_fix_recommendation.py --dataset data/input/npidata_sample_100.csv --output-json results.json
```

The fix recommendation agent analyzes issues and anomalies to suggest intelligent fixes with confidence scores.

### Docker
- Build: `docker build -t dq-pipeline .`
- Run with mounted data folder:  
  `docker run --rm -v $(pwd)/data:/app/data dq-pipeline --dataset data/input/npidata_sample_100.csv --output-file data/output/npidata_sample_100_fixed.csv`
- Compose alternative (uses `DATASET_PATH` and `OUTPUT_PATH`):  
  `DATASET_PATH=data/input/npidata_sample_100.csv OUTPUT_PATH=data/output/npidata_sample_100_fixed.csv docker compose run dq-pipeline`
- For the full baseline dataset:  
  `docker run --rm -v $(pwd)/data:/app/data dq-pipeline --dataset data/input/npidata_baseline.csv --output-file data/output/npidata_baseline_fixed.csv`
  (or set `DATASET_PATH`/`OUTPUT_PATH` in compose accordingly).

### Query DuckDB Storage
```bash
# View all stored data (runs, issues, anomalies)
python scripts/query_duckdb.py

# View only profiling runs
python scripts/query_duckdb.py --runs-only

# View only issues
python scripts/query_duckdb.py --issues-only

# View only anomalies
python scripts/query_duckdb.py --anomalies-only

# Limit results
python scripts/query_duckdb.py --limit 5
```

### Output Files
The system generates several output files:
- **Column Details CSV**: Detailed metrics for each column (`data/results/`)
- **Profiling Logs**: Execution logs (`profiler_demo.log`)
- **DuckDB Storage**: `data/storage/dq_metrics.duckdb` (profiling runs, metrics, issues, anomalies)
