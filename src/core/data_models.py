"""
Core data models for the agentic data quality system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Literal
import pandas as pd


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
    
    # Validation logic has been moved to ValidatorAgent for better separation of concerns


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