# Fix Recommendation Logic Explanation

## Overview

Your fix recommendation system is a multi-stage pipeline that:
1. **Profiles** the dataset to detect data quality issues
2. **Detects anomalies** by comparing against historical baselines
3. **Generates intelligent fix recommendations** with confidence scores
4. **Executes fixes** automatically (for safe, actionable recommendations)

---

## Fix Recommendation Logic Flow

### Stage 1: Issue Detection (Profiler Agent)

The profiler identifies three types of issues:

1. **Completeness Issues**: Missing/null values
   - Uses intelligent null detection (recognizes `NaN`, `""`, `"-"`, `"N/A"`, `"UNAVAILABLE"`, etc.)
   - Special handling: EIN column treats `"<UNAVAIL>"` as valid (not null)

2. **Conformity Issues**: Values that don't match format rules
   - Validates against rules in `config/validation_rules.yaml`
   - Rule types: regex, enum, length, date, phone, numeric

3. **Uniqueness Issues**: Duplicate values (currently only for NPI column)

### Stage 2: Anomaly Detection (Optional)

Compares current metrics against historical baselines stored in DuckDB to detect:
- Significant drops in completeness/conformity/uniqueness
- Unexpected improvements (if configured)

### Stage 3: Fix Recommendation Generation

The `FixRecommendationAgent` analyzes each issue and generates recommendations with:

- **Fix Strategy**: How to fix the issue
- **Confidence Score**: 0.0-1.0 (higher = more reliable)
- **Actionable Flag**: Whether it's safe to auto-fix
- **Suggested Value**: The recommended fix (if applicable)
- **Rationale**: Explanation of why this fix is recommended

---

## Issue Type-Specific Logic

### 1. Completeness Issues (Missing Values)

#### Entity Type Code (Special Row-by-Row Logic)
**Location**: `_recommend_entity_type_code_fix()` in `fix_recommendation_agent.py`

**Logic**:
- If dataset path is available, performs **row-by-row analysis**:
  - Row has Organization Name → Entity Type Code = "2" (Organization)
  - Row has Individual Names (Last/First) but no Org Name → Entity Type Code = "1" (Individual)
  - Row has neither → Flag for manual review

**Confidence Calculation**:
- If 85%+ of missing rows can be determined: 85-95% confidence
- If <85% can be determined: 0-85% confidence (scaled linearly)
- Actionable if confidence ≥ 85%

**Fallback** (if dataset_path not available):
- Uses aggregate metrics (less reliable)
- Checks if Provider Organization Name has data → suggests "2"
- Checks if Provider Last/First Name has data → suggests "1"
- Uses mode value if available
- Default: "1" (Individual) with 60% confidence

#### City/State Columns (Context-Based Inference)
**Location**: `_recommend_completeness_fix()` lines 267-302

**Logic**:
- For missing City or State values, uses **context_imputation** strategy
- Requires context columns:
  - Address Line 1 & 2
  - Postal Code
  - Country Code
- If all context columns exist:
  - State: 85% confidence, actionable
  - City: 80% confidence, actionable
- If context missing: 60% confidence, not actionable

**Note**: Actual inference happens in the executor (row-by-row), not in recommendation generation

#### General Completeness (Default Imputation)
**Location**: `_recommend_completeness_fix()` lines 304-345

**Logic**:
- Checks for column-specific defaults (e.g., "Last Update Date" → current date)
- If default exists: 60% confidence, "default_imputation" strategy
- If no default: "flag_for_review" strategy, not actionable

**Actionable Threshold**: Confidence ≥ 85% (SAFE_IMPUTATION_CONFIDENCE)

---

### 2. Conformity Issues (Format Violations)

**Location**: `_recommend_conformity_fix()` in `fix_recommendation_agent.py`

#### Analysis Process:
1. **Sample Violations**: Analyzes up to 10 violation examples (or all if `analyze_all_violations: true`)
2. **Pattern Detection**: Tries to identify fixable patterns based on rule type:
   - **Regex**: Extra whitespace, non-digit removal, case normalization
   - **Enum**: Case normalization, float-to-int conversion (Entity Type Code)
   - **Length**: Truncation for too-long values
   - **Phone**: Digit extraction
   - **Date**: Format conversion

3. **Confidence Calculation**:
   - If 80%+ violations are fixable → 90% confidence
   - If 60-80% fixable → 85% confidence
   - If 40-60% fixable → 75% confidence
   - If <40% fixable → 65% confidence

4. **Actionable Decision**:
   - Common format fixes (whitespace, case, trim): ≥85% confidence → actionable
   - Other fixes: ≥90% confidence → actionable

**Special Handling**:
- **State Columns**: Skips auto-fix in executor (lines 467-468), relies on context inference instead
- **Entity Type Code**: Normalizes "1.0" → "1", "2.0" → "2"

---

### 3. Uniqueness Issues (Duplicates)

**Location**: `_recommend_uniqueness_fix()` in `fix_recommendation_agent.py`

**Logic**:
- For NPI duplicates: Always flags for review (not auto-fixable)
- High confidence (95%) but not actionable
- Requires manual investigation to determine which duplicate to keep

---

### 4. Anomaly Issues

**Location**: `_recommend_for_anomaly()` in `fix_recommendation_agent.py`

**Logic**:
- Anomalies indicate systematic issues (not individual row problems)
- Strategy: "investigate_root_cause"
- Not actionable (requires upstream investigation)
- Confidence based on severity and z-score

---

## Fix Execution Logic

### Fix Executor Agent

**Location**: `fix_executor_agent.py`

#### Processing Flow:

1. **Filter Recommendations**:
   - Conformity: Processes ALL conformity recommendations (ignores actionable flag)
   - Completeness: Processes ALL completeness recommendations with `suggested_value` or `context_imputation` strategy
   - Other types: Only processes if actionable (if `apply_only_actionable=True`)

2. **Non-Destructive Mode** (Default):
   - **Original columns are NEVER modified** (`never_modify_original_columns: true`)
   - Creates new columns:
     - `<column>__auto_fixed`: Fixed values
     - `<column>__fix_applied`: Boolean flag (if `add_fix_flags: true`)
     - `<column>__fix_reason`: Reason for fix (if `add_fix_flags: true`)
     - `<column>__recommendation`: Human-readable recommendation text (if `add_recommendation_column: true`)

3. **Fix Application**:

   **Conformity Fixes** (`_fix_conformity()`):
   - Processes ALL rows in dataset
   - For each row:
     - Skips null values
     - Skips already-conforming values
     - Tries to fix using rule-specific logic
     - Writes fixed value to new column (if fixable)
     - Marks as "Need Review" in sidecar (if not fixable)

   **Completeness Fixes** (`_fix_completeness()`):
   - **Entity Type Code**: Row-by-row conditional logic (same as recommendation)
   - **City/State**: Context-based inference using:
     - City-to-state mapping file
     - Postal-to-state mapping file
     - Address line parsing
     - State inference priority:
       1. Full state name mapping (most reliable)
       2. City name lookup
       3. Address line parsing
       4. Postal code lookup (last resort)
   - **General**: Uses suggested_value from recommendation

4. **Output Files**:
   - Fixed dataset: `<dataset>_fixed.csv` (or custom output path)
   - Sidecar: `<dataset>_fixed_fix_recommendations.csv` (per-row recommendations)

---

## Terminal Commands

### Basic Usage

```bash
# Complete pipeline: Profile → Anomaly Detection → Fix Recommendations → Apply Fixes
python scripts/demo_fix_recommendation.py --dataset data/input/npidata_baseline.csv

# Skip anomaly detection (if DuckDB not available)
python scripts/demo_fix_recommendation.py --dataset data/input/npidata_baseline.csv --skip-anomaly-detection

# Skip profiling (reuse existing profile)
python scripts/demo_fix_recommendation.py --dataset data/input/npidata_baseline.csv --skip-profiling

# Export recommendations to JSON
python scripts/demo_fix_recommendation.py --dataset data/input/npidata_baseline.csv --output-json results.json

# Specify custom output file
python scripts/demo_fix_recommendation.py --dataset data/input/npidata_baseline.csv --output-file data/results/custom_fixed.csv

# Verbose logging
python scripts/demo_fix_recommendation.py --dataset data/input/npidata_baseline.csv --verbose
```

### Other Useful Commands

```bash
# Profile only
python scripts/demo_profiler.py --dataset data/input/npidata_baseline.csv

# Anomaly detection only
python scripts/demo_anomaly_detection.py --dataset data/input/npidata_baseline.csv

# Query DuckDB storage
python scripts/query_duckdb.py
python scripts/query_duckdb.py --runs-only
python scripts/query_duckdb.py --issues-only
python scripts/query_duckdb.py --anomalies-only
```

---

## Key Configuration Options

### `config/agents.yaml`

#### Fix Recommendation Agent:
```yaml
fix_recommendation:
  confidence_threshold: 0.75  # Minimum confidence for recommendations
  enable_auto_suggestions: true
  analyze_all_violations: true  # Analyze ALL violations (recommended for accuracy)
  max_violation_examples_for_analysis: 50  # Fallback if analyze_all_violations=false
```

#### Fix Executor Agent:
```yaml
fix_executor:
  # Write mode
  write_mode: "new_file"  # "in_place" or "new_file"
  new_file_suffix: "_fixed"
  
  # Non-destructive mode (RECOMMENDED)
  never_modify_original_columns: true  # Original columns NEVER modified
  add_fix_columns: true  # Create new columns for fixes
  fix_column_suffix: "__auto_fixed"
  add_fix_flags: true  # Add fix_applied and fix_reason columns
  add_recommendation_column: true  # Add human-readable recommendation text
  
  # Allowed fix types
  allowed_fix_types:
    - "conformity"
    - "completeness"
  
  # Reference files for context inference
  city_state_mapping_file: "data/reference/city_state_mapping.csv"
  postal_state_mapping_file: "data/reference/postal_state_mapping.csv"
  
  # Sidecar output
  emit_recommendations_sidecar: "same_dir"  # Creates per-row recommendations CSV
```

---

## Important Notes

1. **Row-by-Row Processing**: The executor processes **ALL rows** in the dataset, not a sample. The `max_sample_details` setting only controls how many fix examples are stored in results for reporting.

2. **Non-Destructive by Default**: Original columns are never modified. Fixed values are written to new columns with `__auto_fixed` suffix.

3. **State Column Handling**: State columns skip standard conformity fixes and rely on context-based inference (city/postal/address lookup).

4. **Entity Type Code**: Uses sophisticated row-by-row logic based on related fields (Organization Name vs Individual Names).

5. **Confidence Thresholds**:
   - Completeness: ≥85% for actionable
   - Conformity: ≥85-90% for actionable (depending on fix type)
   - Uniqueness: Never actionable (requires manual review)

6. **Context Inference**: City/State inference requires reference mapping files. If files are missing, inference falls back to limited ZIP3 mapping.

---

## Example Output

After running the fix recommendation pipeline, you'll get:

1. **Fixed Dataset**: `npidata_baseline_fixed.csv`
   - Original columns (unchanged)
   - New columns: `<column>__auto_fixed`, `<column>__fix_applied`, etc.

2. **Sidecar File**: `npidata_baseline_fixed_fix_recommendations.csv`
   - Per-row recommendations with:
     - `row_index`, `column`, `issue_type`
     - `original_value`, `recommended_value`
     - `strategy`, `confidence`, `reason`

3. **Console Output**: Summary of recommendations grouped by issue type

