# Technical Documentation: Data Quality Pipeline
## Comprehensive Logic, Validation Rules, and Edge Cases

**Version:** 1.0  
**Last Updated:** 2025-01-11  
**System:** NPI Data Quality Anomaly Detection and Fix Agent

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Data Quality Issue Types](#data-quality-issue-types)
3. [Validation Rules](#validation-rules)
4. [Profiler Agent](#profiler-agent)
5. [Validator Agent](#validator-agent)
6. [Fix Recommendation Agent](#fix-recommendation-agent)
7. [Fix Executor Agent](#fix-executor-agent)
8. [Special Column Handling](#special-column-handling)
9. [Edge Cases and Special Logic](#edge-cases-and-special-logic)
10. [Reference Files](#reference-files)
11. [Configuration](#configuration)

---

## System Overview

The system implements a 4-stage data quality pipeline:

1. **Profiler Agent**: Identifies data quality issues (completeness, conformity, uniqueness)
2. **Anomaly Detection Agent**: Detects statistical anomalies and outliers
3. **Fix Recommendation Agent**: Generates intelligent fix recommendations with confidence scores
4. **Fix Executor Agent**: Applies fixes based on recommendations (supports recommendations-only mode)

### Processing Modes

- **Standard Mode**: Processes entire dataset in memory
- **Chunked Mode**: Processes large datasets in configurable chunks
- **Recommendations-Only Mode**: Generates recommendations without modifying data

---

## Data Quality Issue Types

### 1. Completeness Issues

**Definition**: Missing or null values in required fields.

**Sub-types**:
- **Universal Required**: Always required (NPI, Entity Type Code, dates)
- **Conditional Required**: Required based on Entity Type Code
  - Entity Type Code = 1 (Individual): Requires Provider Last Name and First Name
  - Entity Type Code = 2 (Organization): Requires Provider Organization Name
- **Optional Fields**: Not required but tracked for completeness metrics

**Null Value Detection**:
- Empty strings (`""`)
- Whitespace-only strings (`"   "`)
- `None` / `NaN` / `pd.NA`
- Special values: `"<UNAVAIL>"` (only for EIN field)

### 2. Conformity Issues

**Definition**: Values that don't match required format rules.

**Rule Types**:
- **Regex**: Pattern matching (e.g., NPI must be exactly 10 digits)
- **Enum**: Must be one of allowed values (e.g., state codes)
- **Length**: Must be within min/max character limits
- **Phone**: Must have 7-20 digits after removing punctuation
- **Date**: Must be valid date format, optionally not in future
- **Numeric**: Must be numeric format

### 3. Uniqueness Issues

**Definition**: Duplicate values in fields that should be unique.

**Currently Tracked**:
- **NPI**: Must be unique across entire dataset

---

## Validation Rules

### Rule Types and Configurations

#### 1. Regex Rules

**Format**: `pattern: "^regex_pattern$"`

**Examples**:
- **NPI**: `^\\d{10}$` - Exactly 10 digits
- **EIN**: `^(\\d{9}|<UNAVAIL>)$` - 9 digits or "<UNAVAIL>"
- **Postal Code**: `^(\\d{5}(-\\d{4})?(\\.0)?|\\d{9}(\\.0)?)$` - 5 digits, ZIP+4, or 9 digits (with optional .0 suffix)
- **Name Fields**: `^[A-Za-z\\s\\-\\'\\.]{1,100}$` - Alphabetic with optional punctuation, 1-100 chars
- **City Names**: `^[A-Za-z\\s\\-\\'\\.]{1,40}$` - Alphabetic with optional punctuation, 1-40 chars
- **Country Code**: `^[A-Z]{2}$` - Exactly 2 uppercase letters
- **Taxonomy Code**: `^[A-Za-z0-9]{10}$` - Exactly 10 alphanumeric characters
- **License Number**: `^[A-Za-z0-9]{1,25}$` - 1-25 alphanumeric characters

**Special Handling**:
- Case-insensitive matching where appropriate
- Whitespace normalization
- Punctuation preservation for names

#### 2. Enum Rules

**Format**: `values: ["value1", "value2", ...]`

**Examples**:
- **Entity Type Code**: `["1", "2"]`
- **State Codes**: All 50 US states + DC + territories + military codes
  - US States: `["AL", "AK", "AZ", ..., "WY"]`
  - Territories: `["AS", "GU", "MP", "PR", "VI", "FM", "MH", "PW"]`
  - Military: `["AE", "AP", "AA"]`
- **Primary Taxonomy Switch**: `["Y", "N"]`

**Special Handling**:
- Case-insensitive matching
- Full state name to abbreviation conversion (e.g., "New York" → "NY")
- Float to integer normalization for Entity Type Code ("1.0" → "1")

#### 3. Length Rules

**Format**: 
```yaml
min: <number>
max: <number>
```

**Examples**:
- **Organization Name**: min: 2, max: 120
- **Address Lines**: min: 1, max: 80
- **Taxonomy Group**: min: 1, max: 50
- **Other Provider Identifier**: min: 1, max: 100

**Special Handling**:
- Truncation if too long (preserves original in non-destructive mode)
- Cannot fix if too short (requires data, not just formatting)

#### 4. Phone Rules

**Format**:
```yaml
min_digits: <number>
max_digits: <number>
```

**Examples**:
- **Telephone/Fax Numbers**: min_digits: 7, max_digits: 20

**Special Handling**:
- Removes all non-digit characters before validation
- Validates digit count only
- Preserves original formatting in non-destructive mode

#### 5. Date Rules

**Format**:
```yaml
not_future: true/false
```

**Examples**:
- **Provider Enumeration Date**: not_future: true
- **Last Update Date**: not_future: true
- **Certification Date**: not_future: true

**Accepted Formats**:
- `MM/DD/YYYY`
- `YYYY-MM-DD`
- `MM-DD-YYYY`
- `YYYY/MM/DD`
- `DD/MM/YYYY` (European format, converted)
- `MM.DD.YYYY`
- `YYYY.MM.DD`

**Special Handling**:
- Converts all formats to standard `MM/DD/YYYY`
- Validates calendar dates (e.g., rejects Feb 30)
- Checks future date constraint if `not_future: true`

#### 6. Numeric Rules

**Format**: Implicit (validates numeric format)

**Special Handling**:
- Removes non-numeric characters
- Validates numeric format
- Preserves decimal points where appropriate

---

## Profiler Agent

### Purpose

Identifies and quantifies data quality issues in the dataset.

### Responsibilities and Scope

- Computes core data quality metrics at dataset and column level:
  - Completeness (percentage of non-null values, with conditional logic)
  - Conformity (percentage of values matching the validation rules)
  - Uniqueness (currently scoped to NPI, where uniqueness is required)
- Applies NPI-specific business rules for conditional completeness:
  - Universal required: `NPI`, `Entity Type Code`
  - Conditional required:
    - Entity Type Code = 1 (Individual): requires Provider Last Name + Provider First Name
    - Entity Type Code = 2 (Organization): requires Provider Organization Name
- Delegates all format/conformity checks to the `ValidatorAgent`
  - ProfilerAgent is now the orchestrator that uses ValidatorAgent for rule evaluation
  - ProfilerAgent focuses on metrics, issue detection, and persistence

### Inputs and Outputs

- **Input**: Path to a CSV dataset (typically NPI data)
- **Output**: `ProfileResults` object containing:
  - `DatasetProfile`
    - `dataset_name` (from file stem)
    - `total_rows`, `total_columns`
    - `overall_completeness`, `overall_conformity`, `overall_uniqueness`
    - `column_metrics`: per-column `ColumnMetrics`
  - `issues`: list of `Issue` objects for:
    - completeness violations
    - conformity violations
    - uniqueness violations (NPI duplicates)
  - `execution_metadata`:
    - runtime, processing mode (standard vs chunked), chunk size
    - number of rules applied, run identifier, file size, timestamp

### Processing Modes

#### Standard Mode
- Processes entire dataset in memory
- Comprehensive issue detection
- Full statistical analysis

#### Chunked Mode
- Processes dataset in configurable chunks
- Accumulates metrics across chunks
- **Note**: Some issue types may be detected differently in chunked vs standard mode

### Issue Detection Logic

#### Completeness Detection

1. **Universal Required Fields**:
   - NPI
   - Entity Type Code
   - Provider Enumeration Date
   - Last Update Date
   - Always checked for null values

2. **Conditional Required Fields**:
   - **Entity Type Code = 1 (Individual)**:
     - Provider Last Name (Legal Name) - REQUIRED
     - Provider First Name - REQUIRED
     - Provider Organization Name - OPTIONAL
   - **Entity Type Code = 2 (Organization)**:
     - Provider Organization Name (Legal Business Name) - REQUIRED
     - Provider Last Name - OPTIONAL
     - Provider First Name - OPTIONAL
   - **Entity Type Code Missing/Invalid**:
     - Cannot determine conditional requirements
     - Flags as invalid entity type issue

3. **Completeness Calculation**:
   ```
   Completeness = (Non-null count / Total count) * 100
   ```

#### Conformity Detection

1. **Per-Column Validation**:
   - Applies validation rule for each column
   - Counts non-conforming values
   - Stores violation examples (first 5)

2. **Conformity Calculation**:
   ```
   Conformity = (Conforming count / Non-null count) * 100
   ```

3. **Violation Examples**:
   - Stores first 5 violations per column
   - Used for fix recommendation analysis

#### Uniqueness Detection

1. **NPI Uniqueness**:
   - Identifies duplicate NPI values
   - Counts occurrences of each duplicate
   - Flags as critical issue

### Output

- **DatasetProfile**: Comprehensive metrics for entire dataset
- **ColumnMetrics**: Per-column statistics
- **Issues List**: All detected data quality issues

---

## Validator Agent

### Purpose

Provides a reusable, high-performance validation layer for checking data conformity against the configured validation rules.

The ValidatorAgent is responsible for:
- Loading and managing validation rules
- Validating individual values for a specific column
- Validating entire columns (`pd.Series`) for conformity
- Running bulk validation over full datasets (CSV files), with the same scalable processing model as ProfilerAgent

### Responsibilities and Separation of Concerns

- **ValidatorAgent**
  - Owns the implementation of all rule types (regex, enum, length, numeric, date, phone, etc.)
  - Encapsulates null detection (via `is_value_null_for_completeness`) so that “null-like” business values are handled consistently
  - Provides both row-wise and vectorized validation paths for performance
  - Can be used independently (e.g., from scripts or tests) without profiling

- **ProfilerAgent**
  - Uses ValidatorAgent to compute conformity metrics as part of full profiling
  - Focuses on aggregating metrics, computing derived scores, and detecting issues
  - Does not re-implement validation rules; all rule logic lives in ValidatorAgent

This separation allows you to:
- Reuse validation logic across agents and tools
- Evolve rules without changing profiling logic
- Benchmark and tune validation performance independently

### Inputs and Execution Modes

The ValidatorAgent supports two primary usage patterns:

1. **Series-level validation**
   - Input: `pd.Series` and a column name (`column`)
   - Typical method: `execute(series, column="NPI")` or `check_conformity(series, "NPI")`
   - Returns: counts of conforming/non-conforming values, conformity score, and sample violations

2. **Bulk dataset validation**
   - Input: path to a CSV dataset (string)
   - Method: `execute("data/input/npidata_sample_100.csv")`
   - Returns a dictionary with:
     - `column_results`: per-column metrics (total/non-null/null counts, conformity %, violations, rule info)
     - `overall_stats`: dataset-wide averages and totals
     - `execution_metadata`: runtime, processing mode, file size, rules applied, etc.

### Rule Types and Logic

ValidatorAgent is the reference implementation for rule evaluation. It supports:

- **Regex rules**
  - Applies compiled patterns to stringified values
  - Used for fixed-format fields (NPI, EIN, postal codes, taxonomy codes, etc.)

- **Enum rules**
  - Enforces membership in a configured set of values
  - Special handling for `Entity Type Code` (e.g., `"1.0"` → `"1"` when appropriate)

- **Length rules**
  - Enforces minimum/maximum string length (e.g., names, addresses)
  - Uses vectorized operations for performance where possible

- **Numeric rules**
  - Requires values to parse as numeric

- **Date rules**
  - Supports multiple input formats
  - Optional `not_future` constraint to reject future dates

- **Phone rules**
  - Strips non-digits and validates digit count range

For any column without a configured rule:
- Non-null values are treated as conforming from a *conformity* perspective
- Completeness (i.e., null counts) is handled by ProfilerAgent, not ValidatorAgent

### Null Handling and Violations

- Uses shared helpers (`is_value_null_for_completeness`, `count_non_null_values`) to maintain consistent null semantics across agents.
- Skips null-equivalent values when computing conformity (they impact completeness, not format conformity).
- Collects a limited number of sample violations per column (configurable via `max_violation_examples`) for downstream analysis and fix recommendations.

### Performance and Scalability

ValidatorAgent mirrors the performance features of ProfilerAgent:
- Chunked processing for large files (configurable chunk size and threshold)
- Optional parallel column validation via `ThreadPoolExecutor`
- Data type optimization for memory-efficient validation
- Progress tracking for long-running validations

ValidatorAgent can be:
- Used standalone from scripts (e.g., `scripts/demo_validator.py`, `scripts/test_agents.py`)
- Composed into agents like ProfilerAgent to provide a consistent validation foundation

## Fix Recommendation Agent

### Purpose

Generates intelligent fix recommendations with confidence scores and actionability flags.

### Recommendation Strategies

#### 1. Context-Based Imputation

**Use Case**: Missing city/state values with available context

**Context Sources**:
- Address lines (Address Line 1, Address Line 2)
- Postal codes
- Related address components (city ↔ state)
- Country codes

**Confidence Scores**:
- State inference: 85% (high reliability)
- City inference: 80% (moderate reliability)

**Actionability**: Always actionable if context available

#### 2. Conditional Imputation

**Use Case**: Entity Type Code and name fields

**Logic**:
- **Entity Type Code**: Determined from presence of organization vs individual names
  - If Organization Name present → "2"
  - If Last Name or First Name present → "1"
  - If both present → "2" (organization takes precedence)
  - If neither present → Cannot determine

- **Name Fields**: Based on Entity Type Code
  - Entity Type Code = 1 → Requires Last Name and First Name
  - Entity Type Code = 2 → Requires Organization Name

**Confidence Scores**:
- Entity Type Code: 90% (high reliability)
- Name fields: 50% (cannot auto-fill, requires manual input)

**Actionability**: 
- Entity Type Code: Actionable if determinable
- Name fields: Not actionable (requires manual input)

#### 3. Format Correction

**Use Case**: Conformity issues with fixable patterns

**Examples**:
- State name → abbreviation ("New York" → "NY")
- Case normalization ("ny" → "NY")
- Whitespace cleanup
- Float to integer ("1.0" → "1")
- Phone number cleanup (remove punctuation, validate digits)

**Confidence Scores**:
- Based on fixable ratio: `fixable_count / total_violations`
- 80%+ fixable → 90% confidence
- 50-80% fixable → 75% confidence
- <50% fixable → 60% confidence

**Actionability**: Actionable if fixable_count > 0

#### 4. Data Enhancement

**Use Case**: Splitting combined values into separate columns

**Examples**:
- Provider Last Name with medical degree → Split to Last Name + Medical Title/Degree column

**Confidence Scores**: 100% (deterministic operation)

**Actionability**: Always actionable

### Recommendation Generation Process

1. **Issue Analysis**:
   - Analyzes violation examples
   - Identifies common patterns
   - Determines fixability

2. **Strategy Selection**:
   - Chooses appropriate strategy based on issue type and context
   - Considers available reference files
   - Evaluates actionability

3. **Confidence Calculation**:
   - Based on fixable ratio
   - Context availability
   - Historical success rates

4. **Deduplication**:
   - Prevents duplicate recommendations for same (column, issue_type) pair
   - Ensures one recommendation per column per issue type

### Output

- **FixRecommendation**: Detailed recommendation with strategy, confidence, suggested value
- **FixRecommendationResults**: Collection of all recommendations

---

## Fix Executor Agent

### Purpose

Applies fix recommendations to the dataset with safety features.

### Processing Modes

#### Standard Mode
- Applies fixes to dataset
- Creates backup (if configured)
- Validates after fixes

#### Recommendations-Only Mode
- Generates recommendations only
- No data modification
- Outputs to sidecar CSV file
- Validates using recommended values for "AFTER FIX" metrics

### Fix Application Logic

#### Conformity Fixes

**Process**:
1. For each row with conformity issue:
   - Extract original value
   - Apply fix based on rule type
   - Validate fixed value
   - Apply fix (if not in recommendations-only mode)

2. **Rule Type Handling**:
   - **Enum**: Normalize case, convert full names to abbreviations
   - **Regex**: Pattern cleanup, whitespace normalization
   - **Length**: Truncate if too long (cannot fix if too short)
   - **Phone**: Remove non-digits, validate digit count
   - **Date**: Parse and reformat to standard format
   - **Numeric**: Remove non-numeric characters

3. **Special Column Handling** (see [Special Column Handling](#special-column-handling))

#### Completeness Fixes

**Current Status**: Disabled by default (completeness not in `allowed_fix_types`)

**When Enabled**:
- Context-based imputation for city/state
- Conditional imputation for Entity Type Code
- Name fields: Identified but not auto-filled (requires manual input)

### Safety Features

1. **Non-Destructive Mode**:
   - Writes fixes to new columns (`<column>__auto_fixed`)
   - Preserves original columns
   - Adds fix flags (`<column>__fix_applied`, `<column>__fix_reason`)

2. **Backup Creation**:
   - Automatic backup before modifications
   - Timestamped backup files

3. **Validation After Fix**:
   - Re-validates all fixed columns
   - Calculates before/after metrics
   - Reports improvement

4. **Detailed Logging**:
   - All fix operations logged
   - Row-level fix details
   - Error handling and reporting

### Output

- **Fixed Dataset**: Original or new file with fixes applied
- **Sidecar CSV**: Per-row recommendations (`<dataset>_fix_recommendations.csv`)
- **Validation Results**: Before/after conformity and completeness metrics

---

## Special Column Handling

### State Columns

**Columns**:
- Provider Business Mailing Address State Name
- Provider Business Practice Location Address State Name
- Provider License Number State Code_1
- Provider License Number State Code_2
- Other Provider Identifier State_1
- Other Provider Identifier State_2

**Special Logic**:

1. **Full Name to Abbreviation Conversion**:
   - "New York" → "NY"
   - "California" → "CA"
   - "North Carolina" → "NC"
   - Handles case variations: "new york", "NEW YORK", "NewYork"
   - Handles all 50 states + DC + territories

2. **Military State Codes**:
   - "AE", "AP", "AA" are valid
   - Recommendation: "Military Mail Locations"
   - No conversion needed

3. **Foreign State Validation**:
   - Checks if state is valid for non-US country
   - Uses `state_country_mapping.csv` reference file
   - If valid foreign state: Recommendation: "foreign state name"
   - If invalid: Requires manual review

4. **Context-Based Inference**:
   - If state is missing/invalid, infers from:
     - City name (using `city_state_mapping.csv`)
     - Postal code (using `postal_state_mapping.csv`)
     - Address lines (parsing for state names)
   - Priority: City > Address > Postal Code

### City Columns

**Columns**:
- Provider Business Mailing Address City Name
- Provider Business Practice Location Address City Name

**Special Logic**:

1. **Duplicate City Name Fix**:
   - "WASHINGTON WASHINGTON, DC" → "WASHINGTON, DC"
   - Removes duplicate city names
   - Preserves state suffix

2. **Context-Based Inference**:
   - If city is missing, infers from:
     - State name (using `city_state_mapping.csv`)
     - Postal code (using `postal_state_mapping.csv`)
     - Address lines (parsing for city-like patterns)
   - Validates inferred city against state mapping

3. **Trailing Punctuation Cleanup**:
   - Removes trailing commas, periods, spaces
   - Preserves internal punctuation

### Postal Code Columns

**Columns**:
- Provider Business Mailing Address Postal Code
- Provider Business Practice Location Address Postal Code

**Special Logic**:

1. **US Postal Code Formats**:
   - 5-digit: `12345` or `12345.0`
   - ZIP+4: `12345-6789`
   - 9-digit: `123456789` or `123456789.0`
   - Normalization: Removes `.0` suffix, handles hyphens

2. **Foreign Postal Code Detection**:
   - **Canadian Format**: `A1A 1A1` (letter-digit-letter space digit-letter-digit)
     - Examples: `T4L1Z3`, `T5K0P1`, `S0N2M0`, `M9P0A1`
     - Also accepts without space: `A1A1A1`
   - **UK Format**: `SW1A 1AA`, `M1 1AA` (1-2 letters, 1-2 digits, space, 1 digit, 2 letters)
   - **Other Countries**: If country code is non-US and postal code contains letters
   - **Recommendation**: "validate foreign postal code"
   - **Action**: Preserves original value, flags as valid foreign format

3. **Postal Code Normalization**:
   - Removes `.0` suffix
   - Handles various formats
   - Pads short codes with leading zeros (if < 5 digits)
   - Truncates long codes (if > 9 digits, takes first 5)

### Provider Last Name (Legal Name)

**Special Logic**:

1. **Medical Degree/Title Splitting**:
   - Detects medical degrees/titles after comma
   - Examples: "Smith, MD" → Last Name: "Smith", Medical Title: "MD"
   - "Johnson, LCSW, LPC" → Last Name: "Johnson", Medical Title: "LCSW, LPC"

2. **Recognized Medical Degrees/Titles**:
   - **Medical**: MD, DO, MBBS, MBCHB, MBBCH
   - **Doctoral**: PhD, Ph.D., EdD, Ed.D.
   - **Legal**: JD, J.D., LLM, LL.M.
   - **Nursing**: RN, LPN, NP, APRN, CNM, CRNA, LCSW, LMSW, LPC, LPCC, LMFT, LMHC, LISW, LICSW
   - **Allied Health**: PA, PA-C, PT, DPT, OT, OTR, RT, RRT, RD, RDN
   - **Dental**: DDS, DMD
   - **Veterinary**: DVM, VMD
   - **Other**: DC (Chiropractor), OD (Optometrist), AUD (Audiologist), CNS

3. **New Column Creation**:
   - Creates "Provider Medical Title/Degree" column
   - Stores extracted degrees/titles
   - Preserves original Last Name without degree

4. **Processing**:
   - Splits name and degree
   - Validates name part (without degree)
   - Stores degree in separate column
   - Adds recommendation noting the split

### Entity Type Code

**Special Logic**:

1. **Float to Integer Normalization**:
   - "1.0" → "1"
   - "2.0" → "2"
   - Handles decimal values from Excel/CSV imports

2. **Conditional Imputation**:
   - If missing, determines from name fields:
     - Organization Name present → "2"
     - Last Name or First Name present → "1"
     - Both present → "2" (organization takes precedence)
     - Neither present → Cannot determine

3. **Validation**:
   - Must be exactly "1" or "2"
   - Case-insensitive matching
   - Float normalization before validation

### Name Fields

**Columns**:
- Provider Last Name (Legal Name)
- Provider First Name
- Provider Organization Name (Legal Business Name)

**Special Logic**:

1. **Conditional Completeness**:
   - **Entity Type Code = 1**: Last Name and First Name required
   - **Entity Type Code = 2**: Organization Name required
   - Cannot auto-fill (requires actual name data)
   - Recommendation: "Cannot auto-fill — requires manual input"

2. **Format Validation**:
   - Alphabetic characters only
   - Optional punctuation: spaces, hyphens, apostrophes, periods
   - Max length: 100 characters

3. **Medical Degree Handling**:
   - See [Provider Last Name](#provider-last-name-legal-name) section

### Phone/Fax Numbers

**Columns**:
- Provider Business Mailing Address Telephone Number
- Provider Business Practice Location Address Telephone Number
- Provider Business Mailing Address Fax Number
- Provider Business Practice Location Address Fax Number

**Special Logic**:

1. **Format Cleanup**:
   - Removes all non-digit characters
   - Validates digit count (7-20 digits)
   - Preserves original formatting in non-destructive mode

2. **Validation**:
   - Only validates digit count
   - Does not validate phone number format (area codes, etc.)

### Dates

**Columns**:
- Provider Enumeration Date
- Last Update Date
- Certification Date

**Special Logic**:

1. **Format Conversion**:
   - Accepts multiple input formats
   - Converts to standard `MM/DD/YYYY` format
   - Handles: `MM/DD/YYYY`, `YYYY-MM-DD`, `MM-DD-YYYY`, `YYYY/MM/DD`, `DD/MM/YYYY`, `MM.DD.YYYY`, `YYYY.MM.DD`

2. **Future Date Validation**:
   - If `not_future: true`, rejects dates in the future
   - Validates calendar dates (e.g., rejects Feb 30)

3. **Date Parsing**:
   - Tries multiple formats in order
   - Returns first successfully parsed date
   - Converts to standard format

---

## Edge Cases and Special Logic

### 1. Null Value Handling

**Completeness Detection**:
- Empty strings (`""`) → Null
- Whitespace-only (`"   "`) → Null
- `None` / `NaN` / `pd.NA` → Null
- Special case: `"<UNAVAIL>"` for EIN field → Valid (not null)

**Conformity Validation**:
- Null values skip conformity validation
- Only non-null values are checked against rules

### 2. Case Sensitivity

**Case-Insensitive Matching**:
- State codes: "ny" = "NY" = "Ny"
- Enum values: Case-insensitive matching attempted
- Country codes: Must be uppercase (validated)

**Case Preservation**:
- Name fields: Preserves original case
- Address fields: Preserves original case
- Only normalizes for validation, preserves in output

### 3. Whitespace Handling

**Normalization**:
- Leading/trailing whitespace: Trimmed
- Multiple spaces: Collapsed to single space
- Tabs/newlines: Converted to spaces

**Preservation**:
- Internal spaces in names: Preserved
- Address formatting: Preserved
- Only normalized for validation

### 4. Float to Integer Conversion

**Entity Type Code**:
- "1.0" → "1"
- "2.0" → "2"
- "1" → "1" (no change)
- "2.5" → Invalid (not integer)

**Other Numeric Fields**:
- Generally preserves decimal values
- Only Entity Type Code has this special handling

### 5. State Name Variations

**Accepted Formats**:
- Full name: "New York", "North Carolina", "West Virginia"
- Abbreviation: "NY", "NC", "WV"
- Case variations: "new york", "NEW YORK", "NewYork"
- With/without spaces: "New York" = "NewYork"

**Conversion Priority**:
1. Check if already valid abbreviation → Return as-is
2. Check full name mapping → Convert to abbreviation
3. Try space variations → Convert to abbreviation
4. Return None if no match

### 6. Postal Code Edge Cases

**US Postal Codes**:
- `12345.0` → `12345` (removes .0 suffix)
- `12345-6789` → `12345-6789` (ZIP+4 format)
- `123456789` → `123456789` (9-digit format)
- `917` → `00917` (pads with leading zeros)
- `1234567890` → `12345` (truncates to 5 digits)

**Foreign Postal Codes**:
- Canadian: `T4L1Z3`, `T5K0P1` (with or without space)
- UK: `SW1A 1AA`, `M1 1AA`
- Detected by presence of letters (US codes are numeric only)
- Validated against country code if available

**Military/Overseas Codes**:
- 6-8 digit codes preserved as-is
- May be military APO/FPO codes
- Not normalized to 5 digits

### 7. City Name Edge Cases

**Duplicate City Names**:
- "WASHINGTON WASHINGTON, DC" → "WASHINGTON, DC"
- "NEW YORK NEW YORK, NY" → "NEW YORK, NY"
- Removes duplicate city name, preserves state

**Trailing Punctuation**:
- "CHICAGO," → "CHICAGO"
- "LOS ANGELES." → "LOS ANGELES"
- Removes trailing commas, periods, spaces

**State Suffix Handling**:
- "WASHINGTON, DC" → Preserves state suffix
- "NEW YORK, NY" → Preserves state suffix
- Only removes duplicate city name, not state

### 8. Medical Degree Splitting

**Detection Logic**:
- Looks for comma in Last Name
- Checks if text after comma is medical degree/title
- Handles multiple degrees: "LCSW, LPC"
- Works from end of comma-separated parts

**Edge Cases**:
- "Smith, Jr., MD" → Last Name: "Smith, Jr.", Medical Title: "MD"
- "Johnson, MD, PhD" → Last Name: "Johnson", Medical Title: "MD, PhD"
- "Smith" (no comma) → No split
- "Smith, Company" (not a degree) → No split

### 9. Context-Based Inference Edge Cases

**State Inference**:
- If city available → Use city-state mapping (highest priority)
- If address contains state name → Extract from address
- If postal code available → Use postal-state mapping (lowest priority)
- If multiple sources conflict → Use priority order

**City Inference**:
- Extract from address lines (capitalized words)
- Validate against state mapping if state available
- If no state → Return first reasonable candidate

**Missing Context**:
- If no context available → Cannot infer
- Recommendation: "Need Review - No suggested value available"
- Not added to fix recommendations CSV (unfixable)

### 10. Foreign Address Handling

**State Validation**:
- Checks country code from same row
- Validates state against `state_country_mapping.csv`
- If valid foreign state → Recommendation: "foreign state name"
- If invalid → Recommendation: "State column - requires manual review"

**Postal Code Validation**:
- Checks country code from same row
- Validates format against known foreign formats
- If valid foreign postal code → Recommendation: "validate foreign postal code"
- Preserves original value

**Country Code Requirements**:
- Must be 2 uppercase letters
- Non-US country codes trigger foreign validation
- US country code or missing → Standard US validation

### 11. Recommendations-Only Mode

**Behavior**:
- No data modification
- All recommendations written to sidecar CSV
- Validation uses recommended values for "AFTER FIX" metrics
- Recommendation columns populated in output dataset

**Validation**:
- Builds recommendations map from sidecar CSV
- Uses recommended_value for validation if available
- Falls back to original value if no recommendation
- Calculates projected conformity/completeness rates

### 12. Non-Destructive Mode

**Column Creation**:
- `<column>__auto_fixed`: Contains fixed values
- `<column>__fix_applied`: Boolean flag (True if fix applied)
- `<column>__fix_reason`: Reason for fix
- `<column>__recommendation`: Human-readable recommendation text

**Original Data**:
- Original columns never modified
- All fixes in separate columns
- Easy to compare before/after

### 13. Deduplication Logic

**Recommendations**:
- Prevents duplicate recommendations for same (column, issue_type) pair
- Tracks processed keys in `processed_keys` set
- Logs warning if duplicate detected

**Validation Results**:
- Prevents duplicate entries in validation summary
- Tracks validated columns in `validated_columns` set
- Ensures one entry per column

### 14. Chunked Processing Considerations

**Issue Detection**:
- Some issue types detected per-chunk
- Final comprehensive detection may differ
- Conditional completeness requires full dataset context

**Metrics Accumulation**:
- Metrics accumulated across chunks
- Final profile combines all chunk metrics
- May miss some cross-chunk patterns

---

## Reference Files

### 1. city_state_mapping.csv

**Purpose**: Maps city names to US state codes

**Format**:
```csv
city_name,state_code
CHICAGO,IL
LOS ANGELES,CA
NEW YORK,NY
```

**Usage**:
- State inference from city name
- City validation against state
- Context-based completeness fixes

**Priority**: High (more reliable than postal codes)

### 2. postal_state_mapping.csv

**Purpose**: Maps postal/ZIP codes to US state codes

**Format**:
```csv
postal_code,state_code
90210,CA
10001,NY
60601,IL
```

**Usage**:
- State inference from postal code
- Fallback when city mapping unavailable
- Lower priority due to format inconsistencies

**Normalization**:
- Tries full code first
- Falls back to first 5 digits
- Falls back to first 3 digits (ZIP3)

### 3. state_country_mapping.csv

**Purpose**: Maps state/province names to country codes for foreign validation

**Format**:
```csv
state_name,country_code
Alberta,CA
Ontario,CA
Baja California,MX
England,GB
```

**Usage**:
- Validates foreign state names
- Checks if state exists for given country
- Prevents false positives for foreign addresses

**Coverage**:
- Canada (provinces)
- Mexico (states)
- UK (countries)
- Australia (states)
- Germany, France, India, Brazil, China, Japan, South Korea, Italy, Spain

---

## Configuration

### Agent Configuration (config/agents.yaml)

#### Fix Executor Agent

```yaml
fix_executor:
  # Recommendations-only mode
  recommendations_only: true  # If true, no fixes applied, only recommendations
  
  # Backup and validation
  create_backup: true
  validate_after_fix: true
  
  # Reference mapping files
  city_state_mapping_file: "data/reference/city_state_mapping.csv"
  postal_state_mapping_file: "data/reference/postal_state_mapping.csv"
  state_country_mapping_file: "data/reference/state_country_mapping.csv"
  
  # Write mode
  write_mode: "new_file"  # "in_place" or "new_file"
  new_file_suffix: "_fixed"
  
  # Allowed fix types
  allowed_fix_types:
    - "conformity"
    # - "completeness"  # Disabled by default
  
  # Non-destructive mode
  add_fix_columns: true
  fix_column_suffix: "__auto_fixed"
  add_fix_flags: true
  add_recommendation_column: true
  
  # Sidecar output
  emit_recommendations_sidecar: "same_dir"
  
  # Reporting
  max_sample_details: 10
```

#### Profiler Agent

```yaml
profiler:
  # Processing mode
  use_chunked_processing: false  # true for large datasets
  chunk_size: 10000
  
  # Issue detection
  detect_completeness: true
  detect_conformity: true
  detect_uniqueness: true
```

### Validation Rules (config/validation_rules.yaml)

See [Validation Rules](#validation-rules) section for complete rule definitions.

### Field Categories

**Universal Required Fields**:
- NPI
- Entity Type Code
- Provider Enumeration Date
- Last Update Date

**Conditional Required Fields**:
- Entity Type Code = 1: Provider Last Name, Provider First Name
- Entity Type Code = 2: Provider Organization Name

---

## Summary

This system provides comprehensive data quality management for NPI datasets with:

1. **Multi-stage Pipeline**: Profiling → Anomaly Detection → Recommendation → Execution
2. **Comprehensive Validation**: Regex, Enum, Length, Phone, Date, Numeric rules
3. **Intelligent Fixes**: Context-based inference, format correction, data enhancement
4. **Safety Features**: Non-destructive mode, backups, validation, detailed logging
5. **Special Handling**: State/city/postal code normalization, medical degree splitting, foreign address validation
6. **Edge Case Coverage**: Null handling, case sensitivity, whitespace, float conversion, variations
7. **Reference Data**: City-state, postal-state, state-country mappings
8. **Flexible Configuration**: Recommendations-only mode, chunked processing, fix type filtering

The system is designed to handle real-world data quality issues while preserving data integrity and providing detailed audit trails.

---

**End of Documentation**
