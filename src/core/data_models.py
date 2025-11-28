"""
Core data models for the agentic data quality system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Literal
import pandas as pd
import uuid


def is_value_null_for_completeness(value: Any, column_name: str) -> bool:
    """
    Determine if a value should be considered null for completeness analysis.
    
    Args:
        value: The value to check
        column_name: Name of the column (needed for EIN special case)
        
    Returns:
        bool: True if value should be considered null, False otherwise
    """
    # Handle pandas null values (NaN, None, etc.)
    if pd.isna(value):
        return True
    
    # Convert to string for analysis
    str_value = str(value).strip().lower()
    
    # Special case for EIN column: "<UNAVAIL>" is NOT null
    if column_name == "Employer Identification Number (EIN)":
        if str_value == "<unavail>":
            return False
        # For EIN, still check other null-like values
    
    # Values that should be treated as null for all columns (including EIN for other values)
    null_like_values = {
        "", " ", "-", "--", "---", "----",
        "na", "n/a", "n.a", "n.a.",
        "null", "none", "nan", "nil", "nill",
        "unavail", "unavailable", "<unavail>", "<null>", "<none>", "<na>",
        "unknown", "unk", "missing", "not applicable", "not available"
    }
    
    return str_value in null_like_values


def count_non_null_values(series: pd.Series, column_name: str) -> int:
    """
    Count non-null values in a series using the new completeness logic.
    
    Args:
        series: Pandas series to analyze
        column_name: Name of the column
        
    Returns:
        int: Count of non-null values
    """
    non_null_count = 0
    for value in series:
        if not is_value_null_for_completeness(value, column_name):
            non_null_count += 1
    return non_null_count


@dataclass
class ColumnMetrics:
    """Metrics for a single column."""
    column_name: str
    total_count: int
    non_null_count: int
    unique_count: int
    duplicate_count: int
    conforming_count: int
    non_conforming_count: int
    
    # Calculated percentages
    completeness_score: float
    conformity_score: float
    uniqueness_score: Optional[float] = None
    
    # Additional stats
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    avg_length: Optional[float] = None
    most_common_values: List[tuple] = field(default_factory=list)
    conformity_violations: List[str] = field(default_factory=list)
    
    @property
    def null_count(self) -> int:
        return self.total_count - self.non_null_count


@dataclass
class DatasetProfile:
    """Complete dataset profiling results."""
    dataset_name: str
    total_rows: int
    total_columns: int
    profiling_timestamp: datetime
    column_metrics: Dict[str, ColumnMetrics]
    
    # Dataset-level metrics
    overall_completeness: float
    overall_uniqueness: float
    overall_conformity: float
    
    # Processing metadata
    processing_time_seconds: float
    data_size_mb: float
        
    def get_critical_issues(self) -> List[str]:
        """Get columns with critical conformity issues."""
        critical_columns = []
        for col_name, metrics in self.column_metrics.items():
            if metrics.conformity_score < 95.0:  # Critical threshold
                critical_columns.append(col_name)
        return critical_columns


@dataclass
class ValidationRule:
    """Definition of a data validation rule."""
    rule_id: str
    column_name: str
    rule_type: Literal["regex", "enum", "length", "numeric", "date", "phone"]
    rule_config: Dict[str, Any]
    description: str
    
    def validate_value(self, value: Any) -> bool:
        """Validate a single value against this rule."""
        # Use the new null detection logic
        if is_value_null_for_completeness(value, self.column_name):
            return False
            
        str_value = str(value).strip()
        
        if self.rule_type == "regex":
            import re
            pattern = self.rule_config.get("pattern", "")
            return bool(re.match(pattern, str_value))
        
        elif self.rule_type == "enum":
            allowed_values = self.rule_config.get("values", [])
            # Handle float-to-string conversion for numeric enum values
            if self.column_name == "Entity Type Code":
                # Convert "1.0" -> "1", "2.0" -> "2" 
                try:
                    float_val = float(str_value)
                    if float_val.is_integer():
                        normalized_value = str(int(float_val))
                        return normalized_value in allowed_values
                except ValueError:
                    pass
            return str_value in allowed_values
        
        elif self.rule_type == "length":
            min_len = self.rule_config.get("min", 0)
            max_len = self.rule_config.get("max", float('inf'))
            return min_len <= len(str_value) <= max_len
        
        elif self.rule_type == "numeric":
            try:
                float(str_value)
                return True
            except ValueError:
                return False
        
        elif self.rule_type == "date":
            from datetime import datetime
            try:
                # Try multiple date formats to handle MM/dd/yyyy and YYYY-MM-DD
                date_formats = ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d"]
                parsed_date = None
                
                for fmt in date_formats:
                    try:
                        parsed_date = datetime.strptime(str_value, fmt)
                        break
                    except ValueError:
                        continue
                
                if parsed_date is None:
                    return False
                    
                if self.rule_config.get("not_future", False):
                    return parsed_date.date() <= datetime.now().date()
                return True
            except ValueError:
                return False
        
        elif self.rule_type == "phone":
            import re
            # Remove all non-digits
            digits_only = re.sub(r'\D', '', str_value)
            min_digits = self.rule_config.get("min_digits", 7)
            max_digits = self.rule_config.get("max_digits", 20)
            return min_digits <= len(digits_only) <= max_digits
        
        return False


@dataclass
class Issue:
    """Represents a data quality issue."""
    issue_id: str
    column_name: str
    issue_type: str
    count: int
    percentage: float
    description: str
    examples: List[Any] = field(default_factory=list)
    rule_violated: Optional[str] = None
    detected_at: datetime = field(default_factory=datetime.now)


@dataclass
class ProfileResults:
    """Results from profiling agent execution."""
    dataset_profile: DatasetProfile
    issues: List[Issue]
    execution_metadata: Dict[str, Any] = field(default_factory=dict)
    
    def summary_stats(self) -> Dict[str, Any]:
        """Get summary statistics."""
        return {
            "total_rows": self.dataset_profile.total_rows,
            "total_columns": self.dataset_profile.total_columns,
            "overall_completeness": self.dataset_profile.overall_completeness,
            "overall_conformity": self.dataset_profile.overall_conformity,
            "total_issues": len(self.issues)
        }


@dataclass
class Anomaly:
    """Represents a detected anomaly in data quality metrics."""
    dataset_name: str
    column_name: str
    metric: Literal["completeness", "uniqueness", "conformity"]
    current_value: float
    baseline_value: float
    delta: float
    z_score: float
    severity: Literal["low", "medium", "high"]
    detection_method: str
    context: Dict[str, Any] = field(default_factory=dict)
    anomaly_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    detected_at: datetime = field(default_factory=lambda: datetime.now())


@dataclass
class AnomalyDetectionResults:
    """Results produced by the anomaly detection agent."""
    dataset_name: str
    run_id: Optional[str]
    anomalies: List[Anomaly] = field(default_factory=list)
    execution_metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def has_anomalies(self) -> bool:
        return len(self.anomalies) > 0


@dataclass
class FixRecommendation:
    """Represents a recommended fix for a data quality issue."""
    recommendation_id: str
    issue_id: str
    dataset_name: str
    column_name: str
    issue_type: str
    fix_strategy: str  # e.g., "imputation", "format_correction", "remove_duplicates", "default_imputation", "context_imputation"
    fix_description: str
    confidence_score: float  # 0.0 to 1.0
    estimated_impact: str  # "low", "medium", "high"
    actionable: bool  # Whether this can be auto-applied or requires review
    suggested_value: Optional[Any] = None  # Specific suggested value if applicable
    rationale: str = ""
    prerequisites: List[str] = field(default_factory=list)
    risk_assessment: str = ""
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class FixRecommendationResults:
    """Results produced by the fix recommendation agent."""
    dataset_name: str
    run_id: Optional[str]
    recommendations: List[FixRecommendation] = field(default_factory=list)
    execution_metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def actionable_count(self) -> int:
        """Count of recommendations that can be auto-applied."""
        return sum(1 for rec in self.recommendations if rec.actionable)
    
    @property
    def requires_review_count(self) -> int:
        """Count of recommendations requiring human review."""
        return sum(1 for rec in self.recommendations if not rec.actionable)
