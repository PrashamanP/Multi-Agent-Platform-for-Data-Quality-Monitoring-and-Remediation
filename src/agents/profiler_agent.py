"""
Profiler Agent for computing data quality metrics on NPI datasets.
"""

import pandas as pd
import numpy as np
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.validator_agent import ValidatorAgent
from pathlib import Path
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing as mp
from tqdm import tqdm
import os

from src.core.data_models import (
    ColumnMetrics, DatasetProfile, Issue, ProfileResults, ValidationRule,
    is_value_null_for_completeness, count_non_null_values
)
from src.core.validation_rule_loader import ValidationRuleLoader
from src.agents.base_agent import BaseAgent
from src.core.config_manager import ConfigManager
from src.data_access.file_handler import FileHandler


logger = logging.getLogger(__name__)


class ProfilerAgent(BaseAgent):
    """
    Agent responsible for profiling datasets and computing core DQ metrics.
    
    Computes:
    - Completeness (percentage of non-null values)
    - Uniqueness (percentage of unique values, only applies to NPI column)  
    - Conformity (percentage of values matching format rules)
    """
    
    def __init__(self, validation_rules: Optional[Dict[str, ValidationRule]] = None, 
                 config: Optional[Dict[str, Any]] = None,
                 validation_rule_loader: Optional[ValidationRuleLoader] = None,
                 validator_agent: Optional["ValidatorAgent"] = None):
        """
        Initialize profiler agent.
        
        Args:
            validation_rules: Custom validation rules. If None, loads from config.
            config: Agent configuration. If None, loads from config manager.
            validation_rule_loader: ValidationRuleLoader instance. If None, creates new one.
            validator_agent: ValidatorAgent instance. If None, creates new one.
        """
        # Initialize base agent
        super().__init__("profiler", config)
        
        # Load configuration if not provided
        if config is None:
            config_manager = ConfigManager()
            self.config = config_manager.get_agent_config("profiler")
        
        # Initialize validator agent
        if validator_agent is None:
            # Import ValidatorAgent here to avoid circular imports
            from src.agents.validator_agent import ValidatorAgent
            # Initialize validation rule loader
            self.validation_rule_loader = validation_rule_loader or ValidationRuleLoader()
            # Create validator with validation rules
            if validation_rules is None:
                self.validator_agent = ValidatorAgent(validation_rule_loader=self.validation_rule_loader)
            else:
                self.validator_agent = ValidatorAgent(validation_rules=validation_rules)
        else:
            self.validator_agent = validator_agent
            
        # Keep reference to validation rules for backward compatibility
        self.validation_rules = self.validator_agent.validation_rules
            
        self.universal_required_fields = ["NPI", "Entity Type Code"]
        
        # Configuration settings
        self.enable_sampling = self.config.get('enable_sampling', False)
        self.sample_size = self.config.get('sample_size', 100000)
        self.max_violation_examples = self.config.get('max_violation_examples', 10)
        
        # Performance settings
        self.enable_chunked_processing = self.config.get('enable_chunked_processing', True)
        self.chunk_size = self.config.get('chunk_size', 50000)
        self.chunked_processing_threshold_mb = self.config.get('chunked_processing_threshold_mb', 1000)
        self.parallel_processing = self.config.get('parallel_processing', True)
        self.max_workers = self.config.get('max_workers', 4)
        self.memory_efficient_mode = self.config.get('memory_efficient_mode', True)
        self.optimize_dtypes = self.config.get('optimize_dtypes', True)
        
        # Progress tracking
        self.enable_progress_tracking = self.config.get('enable_progress_tracking', True)
        self.progress_update_interval = self.config.get('progress_update_interval', 10000)
        
        logger.info(f"ProfilerAgent initialized with {len(self.validation_rules)} validation rules")
    
    def reload_validation_rules(self) -> None:
        """Reload validation rules from configuration."""
        self.validation_rules = self.validation_rule_loader.reload_rules()
        logger.info(f"Reloaded {len(self.validation_rules)} validation rules from configuration")
        
    def execute(self, dataset_path: str, **kwargs) -> ProfileResults:
        """
        Execute profiling on the given dataset with optimized processing for large datasets.
        
        Args:
            dataset_path: Path to CSV dataset file
            
        Returns:
            ProfileResults containing comprehensive profiling data
        """
        logger.info(f"Starting profiling for dataset: {dataset_path}")
        start_time = time.time()
        
        try:
            # Check file size to determine processing strategy
            file_size_mb = FileHandler.get_file_size_mb(dataset_path)
            logger.info(f"Dataset size: {file_size_mb:.2f} MB")
            
            # Choose processing strategy based on file size
            if (self.enable_chunked_processing and 
                file_size_mb > self.chunked_processing_threshold_mb):
                logger.info(f"Using chunked processing for large dataset ({file_size_mb:.2f} MB)")
                results = self._execute_chunked(dataset_path)
            else:
                logger.info("Using standard processing for small dataset")
                results = self._execute_standard(dataset_path)
            
            # Store execution metadata
            execution_time = time.time() - start_time
            results.execution_metadata.update({
                "execution_time_seconds": execution_time,
                "agent_version": "2.0.0",
                "rules_applied": len(self.validation_rules),
                "timestamp": datetime.now().isoformat(),
                "file_size_mb": file_size_mb,
                "processing_mode": "chunked" if file_size_mb > self.chunked_processing_threshold_mb else "standard",
                "chunk_size": self.chunk_size,
                "parallel_processing": self.parallel_processing
            })
            
            logger.info(f"Profiling completed in {execution_time:.2f}s. "
                       f"Found {len(results.issues)} issues.")
            
            return results
            
        except Exception as e:
            logger.error(f"Profiling failed: {str(e)}")
            raise
    
    def _execute_standard(self, dataset_path: str) -> ProfileResults:
        """Execute standard profiling for smaller datasets."""
        # Load dataset using FileHandler
        df = FileHandler.load_dataset(dataset_path)
        
        # Apply sampling if enabled for large datasets
        if self.enable_sampling and len(df) > self.sample_size:
            logger.info(f"Sampling {self.sample_size} rows from {len(df)} total")
            df = df.sample(n=self.sample_size, random_state=42)
        
        # Generate dataset profile
        dataset_profile = self._profile_dataset(df, dataset_path)
        
        # Detect issues
        issues = self._detect_issues(df, dataset_profile)
        
        return ProfileResults(
            dataset_profile=dataset_profile,
            issues=issues,
            execution_metadata={}
        )
    
    def _execute_chunked(self, dataset_path: str) -> ProfileResults:
        """Execute chunked profiling for large datasets."""
        logger.info("Starting chunked processing")
        
        # Initialize accumulators
        column_metrics_accumulator = {}
        total_rows = 0
        all_issues = []
        
        # Get total rows for progress tracking
        if self.enable_progress_tracking:
            total_rows_estimate = self._estimate_total_rows(dataset_path)
            logger.info(f"Estimated total rows: {total_rows_estimate:,}")
        
        # Process dataset in chunks
        chunk_iterator = FileHandler.load_dataset_chunks(
            dataset_path, 
            chunk_size=self.chunk_size
        )
        
        progress_bar = None
        if self.enable_progress_tracking:
            # Initialize progress bar with total=None and manual mode
            progress_bar = tqdm(
                total=None,  # Will be updated dynamically
                desc="Processing chunks",
                unit="chunk",
                dynamic_ncols=True
            )
        
        try:
            for chunk_idx, chunk_df in enumerate(chunk_iterator):
                total_rows += len(chunk_df)
                
                # Update progress
                if self.enable_progress_tracking and progress_bar is not None:
                    progress_bar.update(1)
                    progress_bar.set_postfix({
                        'rows_processed': f"{total_rows:,}",
                        'chunk_size': len(chunk_df)
                    })
                
                # Process chunk
                chunk_metrics, chunk_issues = self._process_chunk(chunk_df, chunk_idx)
                
                # Accumulate metrics
                self._accumulate_chunk_metrics(column_metrics_accumulator, chunk_metrics)
                
                # Collect issues
                all_issues.extend(chunk_issues)
                
                # Log progress periodically
                if chunk_idx % 10 == 0:
                    logger.info(f"Processed {chunk_idx + 1} chunks, {total_rows:,} rows")
                    # Log sample accumulation status
                    sample_col = list(column_metrics_accumulator.keys())[0] if column_metrics_accumulator else None
                    if sample_col:
                        acc_data = column_metrics_accumulator[sample_col]
                        logger.info(f"Sample column '{sample_col}' accumulated: total_count={acc_data['total_count']}, "
                                  f"non_null_count={acc_data['non_null_count']}, value_counts_size={len(acc_data['value_counts'])}")
        
        finally:
            if self.enable_progress_tracking and progress_bar is not None:
                progress_bar.close()
        
        # Finalize metrics from accumulators
        logger.info("Finalizing metrics from accumulated data")
        logger.info(f"Total rows accumulated: {total_rows}")
        logger.info(f"Columns accumulated: {len(column_metrics_accumulator)}")
        
        final_metrics = self._finalize_column_metrics(column_metrics_accumulator, total_rows)
        
        # Create dataset profile
        dataset_profile = self._create_dataset_profile(final_metrics, total_rows, dataset_path)
        
        # Log final stats for verification
        logger.info(f"Final profile: {total_rows} rows, {dataset_profile.total_columns} columns")
        logger.info(f"Overall completeness: {dataset_profile.overall_completeness:.1f}%")
        logger.info(f"Overall conformity: {dataset_profile.overall_conformity:.1f}%")
        
        # Consolidate issues
        consolidated_issues = self._consolidate_issues(all_issues)
        
        return ProfileResults(
            dataset_profile=dataset_profile,
            issues=consolidated_issues,
            execution_metadata={}
        )
    
    def _profile_dataset(self, df: pd.DataFrame, dataset_path: str) -> DatasetProfile:
        """Generate comprehensive dataset profile."""
        logger.info("Computing dataset profile metrics")
        
        column_metrics = {}
        
        for column in df.columns:
            metrics = self._profile_column(df, column)
            column_metrics[column] = metrics
        
        # Calculate overall metrics
        overall_completeness = np.mean([m.completeness_score for m in column_metrics.values()])
        # Only calculate uniqueness for NPI column
        npi_metrics = column_metrics.get('NPI')
        overall_uniqueness = npi_metrics.uniqueness_score if npi_metrics and npi_metrics.uniqueness_score is not None else 100.0
        overall_conformity = np.mean([m.conformity_score for m in column_metrics.values()])
        
        # Dataset size calculation
        data_size_mb = df.memory_usage(deep=True).sum() / 1024 / 1024
        
        return DatasetProfile(
            dataset_name=Path(dataset_path).stem,
            total_rows=len(df),
            total_columns=len(df.columns),
            profiling_timestamp=datetime.now(),
            column_metrics=column_metrics,
            overall_completeness=overall_completeness,
            overall_uniqueness=overall_uniqueness,
            overall_conformity=overall_conformity,
            processing_time_seconds=0.0,  # Will be set by caller
            data_size_mb=data_size_mb
        )
    
    def _profile_column(self, df: pd.DataFrame, column: str) -> ColumnMetrics:
        """Profile a single column with conditional completeness."""
        series = df[column]
        
        # Basic counts
        total_count = len(series)
        non_null_count = count_non_null_values(series, column)
        unique_count = series.nunique()
        duplicate_count = total_count - unique_count
        
        # Calculate conformity
        conforming_count, non_conforming_count, violations = self._check_conformity(series, column)
        
        # Calculate conditional completeness
        completeness_score = self._calculate_conditional_completeness(df, column)
        
        # Calculate other percentages
        # Only calculate uniqueness for NPI column
        uniqueness_score = (unique_count / total_count * 100) if total_count > 0 and column == 'NPI' else None
        conformity_score = (conforming_count / non_null_count * 100) if non_null_count > 0 else 0
        
        # String length analysis for text columns
        min_length = max_length = avg_length = None
        if series.dtype == 'object':  # Text columns
            non_null_series = series.dropna()
            if len(non_null_series) > 0:
                lengths = non_null_series.astype(str).str.len()
                min_length = lengths.min()
                max_length = lengths.max()
                avg_length = lengths.mean()
        
        # Most common values
        most_common_values = []
        if non_null_count > 0:
            value_counts = series.value_counts().head(5)
            most_common_values = [(val, count) for val, count in value_counts.items()]
        
        return ColumnMetrics(
            column_name=column,
            total_count=total_count,
            non_null_count=non_null_count,
            unique_count=unique_count,
            duplicate_count=duplicate_count,
            conforming_count=conforming_count,
            non_conforming_count=non_conforming_count,
            completeness_score=completeness_score,
            uniqueness_score=uniqueness_score,
            conformity_score=conformity_score,
            min_length=min_length,
            max_length=max_length,
            avg_length=avg_length,
            most_common_values=most_common_values,
            conformity_violations=violations
        )
    
    def _check_conformity(self, series: pd.Series, column: str) -> Tuple[int, int, List[str]]:
        """Check conformity of column values against validation rules using ValidatorAgent."""
        return self.validator_agent.check_conformity(series, column)
    
    def calculate_completeness(self, df: pd.DataFrame, column: str) -> float:
        """Calculate completeness rate for a specific column.
        
        Args:
            df: DataFrame containing the data
            column: Column name to analyze
            
        Returns:
            Completeness rate as percentage (0-100)
        """
        return self._calculate_conditional_completeness(df, column)
    
    def analyze_completeness_issues(self, df: pd.DataFrame, column: str) -> List[Issue]:
        """Analyze completeness issues for a specific column.
        
        Args:
            df: DataFrame containing the data
            column: Column name to analyze
            
        Returns:
            List of completeness issues found
        """
        return self._analyze_conditional_completeness_issues(df, column)
    
    def _calculate_conditional_completeness(self, df: pd.DataFrame, column: str) -> float:
        """Calculate completeness based on conditional rules."""
        series = df[column]
        total_count = len(series)
        
        if total_count == 0:
            return 0.0
        
        # Check if this is a universal required field
        if column in self.universal_required_fields:
            non_null_count = count_non_null_values(series, column)
            return (non_null_count / total_count * 100)
        
        # Check if this is a conditional field
        if column in ['Provider Last Name (Legal Name)', 'Provider First Name', 'Provider Organization Name (Legal Business Name)']:
            # Calculate completeness based on Entity Type Code
            valid_count = 0
            
            for idx, (entity_type, value) in enumerate(zip(df['Entity Type Code'], series)):
                if is_value_null_for_completeness(entity_type, 'Entity Type Code'):
                    # If Entity Type Code is null, consider this row invalid
                    continue
                
                entity_type_str = str(entity_type).strip()
                
                # Handle decimal values (1.0 -> 1, 2.0 -> 2)
                try:
                    entity_type_int = int(float(entity_type_str))
                except (ValueError, TypeError):
                    # Invalid Entity Type Code - consider this row invalid
                    continue
                
                if entity_type_int == 1:  # Individual
                    if column in ['Provider Last Name (Legal Name)', 'Provider First Name']:
                        # These are required for individuals
                        if not is_value_null_for_completeness(value, column):
                            valid_count += 1
                    elif column == 'Provider Organization Name (Legal Business Name)':
                        # This is optional for individuals
                        valid_count += 1  # Always count as valid
                
                elif entity_type_int == 2:  # Organization
                    if column == 'Provider Organization Name (Legal Business Name)':
                        # This is required for organizations
                        if not is_value_null_for_completeness(value, column):
                            valid_count += 1
                    elif column in ['Provider Last Name (Legal Name)', 'Provider First Name']:
                        # These are optional for organizations
                        valid_count += 1  # Always count as valid
                
                else:
                    # Invalid Entity Type Code - consider this row invalid
                    continue
            
            return (valid_count / total_count * 100)
        
        # For all other fields, they should be not null
        non_null_count = count_non_null_values(series, column)
        return (non_null_count / total_count * 100)
    
    def _analyze_conditional_completeness_issues(self, df: pd.DataFrame, column: str) -> List[Issue]:
        """Analyze conditional completeness issues for a specific column."""
        issues = []
        
        if column not in ['Provider Last Name (Legal Name)', 'Provider First Name', 'Provider Organization Name (Legal Business Name)']:
            return issues
        
        # Count issues by Entity Type Code
        individual_issues = 0
        organization_issues = 0
        invalid_entity_issues = 0
        
        for idx, (entity_type, value) in enumerate(zip(df['Entity Type Code'], df[column])):
            if is_value_null_for_completeness(entity_type, 'Entity Type Code'):
                invalid_entity_issues += 1
                continue
            
            entity_type_str = str(entity_type).strip()
            
            # Handle decimal values (1.0 -> 1, 2.0 -> 2)
            try:
                entity_type_int = int(float(entity_type_str))
            except (ValueError, TypeError):
                invalid_entity_issues += 1
                continue
            
            if entity_type_int == 1:  # Individual
                if column in ['Provider Last Name (Legal Name)', 'Provider First Name']:
                    if is_value_null_for_completeness(value, column):
                        individual_issues += 1
            elif entity_type_int == 2:  # Organization
                if column == 'Provider Organization Name (Legal Business Name)':
                    if is_value_null_for_completeness(value, column):
                        organization_issues += 1
            else:
                invalid_entity_issues += 1
        
        # Create specific issues
        if individual_issues > 0:
            issues.append(Issue(
                issue_id=str(uuid.uuid4()),
                column_name=column,
                issue_type="completeness",
                count=individual_issues,
                percentage=(individual_issues / len(df) * 100),
                description=f"{column} is required for Entity Type Code = 1 (Individual) but has {individual_issues} null values",
                rule_violated=f"{column}_REQUIRED_FOR_INDIVIDUAL"
            ))
        
        if organization_issues > 0:
            issues.append(Issue(
                issue_id=str(uuid.uuid4()),
                column_name=column,
                issue_type="completeness",
                count=organization_issues,
                percentage=(organization_issues / len(df) * 100),
                description=f"{column} is required for Entity Type Code = 2 (Organization) but has {organization_issues} null values",
                rule_violated=f"{column}_REQUIRED_FOR_ORGANIZATION"
            ))
        
        return issues
    
    def _detect_issues(self, df: pd.DataFrame, profile: DatasetProfile) -> List[Issue]:
        """Detect data quality issues based on profile results."""
        issues = []
        
        for column, metrics in profile.column_metrics.items():
            # Completeness issues - create only one completeness issue per column
            if metrics.completeness_score < 100:
                # Universal required fields completeness issues
                if column in self.universal_required_fields:
                    issues.append(Issue(
                        issue_id=str(uuid.uuid4()),
                        column_name=column,
                        issue_type="completeness",
                        count=metrics.null_count,
                        percentage=100 - metrics.completeness_score,
                        description=f"Required field {column} has {metrics.null_count} null values",
                        rule_violated=f"{column}_REQUIRED"
                    ))
                
                # Conditional completeness issues for name fields
                elif column in ['Provider Last Name (Legal Name)', 'Provider First Name', 'Provider Organization Name (Legal Business Name)']:
                    entity_type_issues = self._analyze_conditional_completeness_issues(df, column)
                    issues.extend(entity_type_issues)
                
                # Other fields completeness issues (should always be not null)
                else:
                    issues.append(Issue(
                        issue_id=str(uuid.uuid4()),
                        column_name=column,
                        issue_type="completeness",
                        count=metrics.null_count,
                        percentage=100 - metrics.completeness_score,
                        description=f"Field {column} should not be null but has {metrics.null_count} null values",
                        rule_violated=f"{column}_NOT_NULL"
                    ))
            
            # Conformity issues
            if metrics.non_conforming_count > 0:
                issues.append(Issue(
                    issue_id=str(uuid.uuid4()),
                    column_name=column,
                    issue_type="conformity",
                    count=metrics.non_conforming_count,
                    percentage=(metrics.non_conforming_count / metrics.non_null_count * 100) if metrics.non_null_count > 0 else 0,
                    description=f"{column} has {metrics.non_conforming_count} values that don't match required format",
                    examples=metrics.conformity_violations[:5],  # First 5 violations as examples
                    rule_violated=self.validator_agent.get_validation_rule(column).rule_id if self.validator_agent.has_validation_rule(column) else None
                ))
            
            # Uniqueness issues for fields that should be unique
            if column == "NPI" and metrics.duplicate_count > 0:
                issues.append(Issue(
                    issue_id=str(uuid.uuid4()),
                    column_name=column,
                    issue_type="uniqueness",
                    count=metrics.duplicate_count,
                    percentage=(metrics.duplicate_count / metrics.total_count * 100),
                    description=f"NPI field has {metrics.duplicate_count} duplicate values",
                    rule_violated="NPI_UNIQUE"
                ))
            
            # Data quality thresholds (only for conformity, not completeness or uniqueness)
            if metrics.conformity_score < 95:
                issues.append(Issue(
                    issue_id=str(uuid.uuid4()),
                    column_name=column,
                    issue_type="conformity", 
                    count=metrics.non_conforming_count,
                    percentage=100 - metrics.conformity_score,
                    description=f"{column} conformity ({metrics.conformity_score:.1f}%) below threshold",
                    examples=metrics.conformity_violations[:5],
                    rule_violated=self.validator_agent.get_validation_rule(column).rule_id if self.validator_agent.has_validation_rule(column) else None
                ))
        
        # Dataset-level issues
        if profile.overall_completeness < 85:
            issues.append(Issue(
                issue_id=str(uuid.uuid4()),
                column_name="__DATASET__",
                issue_type="completeness",
                count=0,
                percentage=100 - profile.overall_completeness,
                description=f"Overall dataset completeness ({profile.overall_completeness:.1f}%) is below acceptable threshold"
            ))
        
        return issues
    
    def _estimate_total_rows(self, dataset_path: str) -> int:
        """Estimate total rows in dataset for progress tracking."""
        try:
            # Read first few lines to estimate
            with open(dataset_path, 'r') as f:
                first_line = f.readline()
                if not first_line:
                    return 0
                
                # Count lines in first chunk to estimate
                chunk_size = 10000
                lines_in_chunk = 0
                for _ in range(chunk_size):
                    line = f.readline()
                    if not line:
                        break
                    lines_in_chunk += 1
                
                if lines_in_chunk == 0:
                    return 0
                
                # Estimate based on file size and chunk density
                file_size_bytes = os.path.getsize(dataset_path)
                estimated_total = int((file_size_bytes / len(first_line.encode('utf-8'))) * 0.9)  # 0.9 factor for headers/overhead
                
                return max(estimated_total, lines_in_chunk)
                
        except Exception as e:
            logger.warning(f"Could not estimate total rows: {e}")
            return 0
    
    def _process_chunk(self, chunk_df: pd.DataFrame, chunk_idx: int) -> Tuple[Dict[str, Dict], List[Issue]]:
        """Process a single chunk and return metrics and issues."""
        chunk_metrics = {}
        chunk_issues = []
        
        # Optimize data types if enabled
        if self.optimize_dtypes:
            chunk_df = self._optimize_dtypes(chunk_df)
        
        # Process columns (always use sequential for proper accumulation)
        for column in chunk_df.columns:
            try:
                metrics = self._profile_column_optimized(chunk_df, column)
                # Convert ColumnMetrics to accumulation-friendly format
                chunk_metrics[column] = {
                    'total_count': metrics.total_count,
                    'non_null_count': metrics.non_null_count,
                    'unique_count': metrics.unique_count,
                    'duplicate_count': metrics.duplicate_count,
                    'conforming_count': metrics.conforming_count,
                    'non_conforming_count': metrics.non_conforming_count,
                    'completeness_score': metrics.completeness_score,
                    'uniqueness_score': metrics.uniqueness_score,
                    'conformity_score': metrics.conformity_score,
                    'min_length': metrics.min_length,
                    'max_length': metrics.max_length,
                    'avg_length': metrics.avg_length,
                    'most_common_values': metrics.most_common_values,
                    'conformity_violations': metrics.conformity_violations
                }
            except Exception as e:
                logger.error(f"Failed to profile column {column} in chunk: {str(e)}")
                # Create default metrics for failed columns
                chunk_metrics[column] = {
                    'total_count': len(chunk_df),
                    'non_null_count': 0,
                    'unique_count': 0,
                    'duplicate_count': len(chunk_df),
                    'conforming_count': 0,
                    'non_conforming_count': 0,
                    'completeness_score': 0.0,
                    'uniqueness_score': 0.0 if column == 'NPI' else None,
                    'conformity_score': 0.0,
                    'min_length': None,
                    'max_length': None,
                    'avg_length': None,
                    'most_common_values': [],
                    'conformity_violations': []
                }
        
        # Detect issues for this chunk
        chunk_issues = self._detect_chunk_issues(chunk_df, chunk_metrics)
        
        return chunk_metrics, chunk_issues
    
    def _optimize_dtypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimize DataFrame data types for memory efficiency."""
        optimized_df = df.copy()
        
        for col in optimized_df.columns:
            col_type = optimized_df[col].dtype
            
            if col_type != 'object':
                continue
                
            # Try to convert to numeric
            try:
                pd.to_numeric(optimized_df[col], errors='raise')
                optimized_df[col] = pd.to_numeric(optimized_df[col], errors='coerce')
            except (ValueError, TypeError):
                # Try to convert to datetime with common NPI date formats
                try:
                    # Common NPI date formats
                    date_formats = ['%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y', '%Y%m%d']
                    
                    # Try each format
                    converted = False
                    for fmt in date_formats:
                        try:
                            pd.to_datetime(optimized_df[col], format=fmt, errors='raise')
                            optimized_df[col] = pd.to_datetime(optimized_df[col], format=fmt, errors='coerce')
                            converted = True
                            break
                        except (ValueError, TypeError):
                            continue
                    
                    # If no format worked, try without format (with warnings suppressed)
                    if not converted:
                        import warnings
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            pd.to_datetime(optimized_df[col], errors='raise')
                            optimized_df[col] = pd.to_datetime(optimized_df[col], errors='coerce')
                
                except (ValueError, TypeError):
                    # Keep as object/string
                    pass
        
        return optimized_df
    
    def _profile_columns_parallel(self, df: pd.DataFrame) -> Dict[str, Dict]:
        """Profile columns in parallel for better performance."""
        columns = list(df.columns)
        max_workers = min(self.max_workers, len(columns), mp.cpu_count())
        
        column_metrics = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit column profiling tasks
            future_to_column = {
                executor.submit(self._profile_column_optimized, df, col): col 
                for col in columns
            }
            
            try:
                for future in as_completed(future_to_column, timeout=300):
                    column = future_to_column[future]
                    try:
                        metrics = future.result()
                        column_metrics[column] = {
                            'total_count': metrics.total_count,
                            'non_null_count': metrics.non_null_count,
                            'unique_count': metrics.unique_count,
                            'duplicate_count': metrics.duplicate_count,
                            'conforming_count': metrics.conforming_count,
                            'non_conforming_count': metrics.non_conforming_count,
                            'completeness_score': metrics.completeness_score,
                            'uniqueness_score': metrics.uniqueness_score,
                            'conformity_score': metrics.conformity_score,
                            'min_length': metrics.min_length,
                            'max_length': metrics.max_length,
                            'avg_length': metrics.avg_length,
                            'most_common_values': metrics.most_common_values,
                            'conformity_violations': metrics.conformity_violations
                        }
                    except Exception as e:
                        logger.error(f"Failed to profile column {column}: {e}")
                        # Create default metrics for failed columns
                        column_metrics[column] = self._create_default_metrics_dict(column, len(df))
            except KeyboardInterrupt:
                logger.warning("Profiling interrupted by user. Cancelling remaining tasks...")
                for future in future_to_column.keys():
                    if not future.done():
                        future.cancel()
                raise
        
        return column_metrics
    
    def _profile_column_optimized(self, df: pd.DataFrame, column: str) -> ColumnMetrics:
        """Optimized column profiling with vectorized operations."""
        series = df[column]
        total_count = len(series)
        
        # Use new null detection logic
        non_null_count = count_non_null_values(series, column)
        
        # Optimize uniqueness calculation
        if column == 'NPI':  # Only calculate for NPI
            unique_count = series.nunique()
        else:
            unique_count = None
        
        # Optimize conformity checking with vectorized operations
        if column in self.validation_rules:
            conforming_count, violations = self._check_conformity_vectorized(series, column)
            non_conforming_count = non_null_count - conforming_count
        else:
            conforming_count = non_null_count
            non_conforming_count = 0
            violations = []
        
        # Calculate conditional completeness
        completeness_score = self._calculate_conditional_completeness(df, column)
        
        # Calculate other percentages
        uniqueness_score = (unique_count / total_count * 100) if total_count > 0 and column == 'NPI' else None
        conformity_score = (conforming_count / non_null_count * 100) if non_null_count > 0 else 0
        
        # String length analysis for text columns (optimized)
        min_length = max_length = avg_length = None
        if series.dtype == 'object':  # Text columns
            non_null_series = series.dropna()
            if len(non_null_series) > 0:
                lengths = non_null_series.astype(str).str.len()
                min_length = lengths.min()
                max_length = lengths.max()
                avg_length = lengths.mean()
        
        # Most common values (limit to reduce memory usage)
        most_common_values = []
        if non_null_count > 0:
            value_counts = series.value_counts().head(5)
            most_common_values = [(val, count) for val, count in value_counts.items()]
        
        return ColumnMetrics(
            column_name=column,
            total_count=total_count,
            non_null_count=non_null_count,
            unique_count=unique_count,
            duplicate_count=total_count - (unique_count if unique_count else non_null_count),
            conforming_count=conforming_count,
            non_conforming_count=non_null_count - conforming_count,
            completeness_score=completeness_score,
            uniqueness_score=uniqueness_score,
            conformity_score=conformity_score,
            min_length=min_length,
            max_length=max_length,
            avg_length=avg_length,
            most_common_values=most_common_values,
            conformity_violations=violations
        )
    
    def _check_conformity_vectorized(self, series: pd.Series, column: str) -> Tuple[int, List[str]]:
        """Check conformity using vectorized operations where possible."""
        if column not in self.validation_rules:
            non_null_count = count_non_null_values(series, column)
            return non_null_count, []
        
        rule = self.validation_rules[column]
        
        # Filter out values that should be considered null using our new logic
        non_null_values = []
        for value in series:
            if not is_value_null_for_completeness(value, column):
                non_null_values.append(value)
        
        if len(non_null_values) == 0:
            return 0, []
        
        non_null_series = pd.Series(non_null_values)
        
        # Use vectorized operations for simple rules
        if rule.rule_type == "length":
            min_len = rule.rule_config.get("min", 0)
            max_len = rule.rule_config.get("max", float('inf'))
            lengths = non_null_series.astype(str).str.len()
            conforming_mask = (lengths >= min_len) & (lengths <= max_len)
            conforming_count = conforming_mask.sum()
            
            # Get sample violations
            violations = []
            if conforming_count < len(non_null_series):
                non_conforming_values = non_null_series[~conforming_mask]
                violations = [str(val) for val in non_conforming_values.head(self.max_violation_examples)]
            
            return int(conforming_count), violations
        
        elif rule.rule_type == "enum":
            allowed_values = rule.rule_config.get("values", [])
            conforming_mask = non_null_series.isin(allowed_values)
            conforming_count = conforming_mask.sum()
            
            # Get sample violations
            violations = []
            if conforming_count < len(non_null_series):
                non_conforming_values = non_null_series[~conforming_mask]
                violations = [str(val) for val in non_conforming_values.head(self.max_violation_examples)]
            
            return int(conforming_count), violations
        
        else:
            # Fall back to individual validation for complex rules
            conforming_count, non_conforming_count, violations = self._check_conformity(series, column)
            return conforming_count, violations
    
    def _create_default_metrics_dict(self, column: str, total_count: int) -> Dict:
        """Create default metrics dictionary for failed columns."""
        return {
            'total_count': total_count,
            'non_null_count': 0,
            'unique_count': 0,
            'duplicate_count': 0,
            'conforming_count': 0,
            'non_conforming_count': 0,
            'completeness_score': 0.0,
            'uniqueness_score': None,
            'conformity_score': 0.0,
            'min_length': None,
            'max_length': None,
            'avg_length': None,
            'most_common_values': [],
            'conformity_violations': []
        }
    
    def _detect_chunk_issues(self, chunk_df: pd.DataFrame, chunk_metrics: Dict[str, Dict]) -> List[Issue]:
        """Detect issues for a single chunk."""
        issues = []
        
        for column, metrics in chunk_metrics.items():
            # Completeness issues
            if metrics['completeness_score'] < 100:
                if column in self.universal_required_fields:
                    issues.append(Issue(
                        issue_id=str(uuid.uuid4()),
                        column_name=column,
                        issue_type="completeness",
                        count=metrics['total_count'] - metrics['non_null_count'],
                        percentage=100 - metrics['completeness_score'],
                        description=f"Required field {column} has null values",
                        rule_violated=f"{column}_REQUIRED"
                    ))
            
            # Conformity issues
            if metrics['non_conforming_count'] > 0:
                issues.append(Issue(
                    issue_id=str(uuid.uuid4()),
                    column_name=column,
                    issue_type="conformity",
                    count=metrics['non_conforming_count'],
                    percentage=(metrics['non_conforming_count'] / metrics['non_null_count'] * 100) if metrics['non_null_count'] > 0 else 0,
                    description=f"{column} has values that don't match required format",
                    examples=metrics['conformity_violations'][:5],
                    rule_violated=self.validator_agent.get_validation_rule(column).rule_id if self.validator_agent.has_validation_rule(column) else None
                ))
        
        return issues
    
    def _accumulate_chunk_metrics(self, accumulator: Dict[str, Dict], chunk_metrics: Dict[str, Dict]) -> None:
        """Accumulate metrics from a chunk into the accumulator."""
        for column, metrics in chunk_metrics.items():
            if column not in accumulator:
                accumulator[column] = {
                    'total_count': 0,
                    'non_null_count': 0,
                    'unique_values': set(),
                    'conforming_count': 0,
                    'non_conforming_count': 0,
                    'violations': [],
                    'value_counts': {},
                    'lengths': []
                }
            
            # Accumulate counts
            accumulator[column]['total_count'] += metrics['total_count']
            accumulator[column]['non_null_count'] += metrics['non_null_count']
            accumulator[column]['conforming_count'] += metrics['conforming_count']
            accumulator[column]['non_conforming_count'] += metrics['non_conforming_count']
            
            # Accumulate unique values (for uniqueness calculation)
            if metrics['unique_count'] is not None:
                # Note: This is an approximation for chunked processing
                # For exact uniqueness, we'd need to track all values across chunks
                pass
            
            # Accumulate violations
            accumulator[column]['violations'].extend(metrics['conformity_violations'])
            
            # Accumulate value counts
            if metrics.get('most_common_values'):
                for val, count in metrics['most_common_values']:
                    if val in accumulator[column]['value_counts']:
                        accumulator[column]['value_counts'][val] += count
                    else:
                        accumulator[column]['value_counts'][val] = count
    
    def _finalize_column_metrics(self, accumulator: Dict[str, Dict], total_rows: int) -> Dict[str, ColumnMetrics]:
        """Finalize column metrics from accumulated data."""
        final_metrics = {}
        
        for column, acc_data in accumulator.items():
            # Calculate final metrics
            total_count = acc_data['total_count']
            non_null_count = acc_data['non_null_count']
            conforming_count = acc_data['conforming_count']
            non_conforming_count = acc_data['non_conforming_count']
            
            # Calculate percentages - completeness_score uses the accumulated counts which already use new logic
            completeness_score = (non_null_count / total_count * 100) if total_count > 0 else 0
            conformity_score = (conforming_count / non_null_count * 100) if non_null_count > 0 else 0
            
            # Uniqueness is approximated in chunked processing
            uniqueness_score = None
            if column == 'NPI':
                # For NPI, we can't get exact uniqueness from chunks, so we'll estimate
                # This is a limitation of chunked processing
                uniqueness_score = 100.0  # Assume unique for now
            
            # Get top values
            sorted_values = sorted(acc_data['value_counts'].items(), key=lambda x: x[1], reverse=True)
            most_common_values = sorted_values[:5]
            
            # Get sample violations
            violations = acc_data['violations'][:self.max_violation_examples]
            
            final_metrics[column] = ColumnMetrics(
                column_name=column,
                total_count=total_count,
                non_null_count=non_null_count,
                unique_count=None,  # Not available in chunked processing
                duplicate_count=None,  # Not available in chunked processing
                conforming_count=conforming_count,
                non_conforming_count=non_conforming_count,
                completeness_score=completeness_score,
                uniqueness_score=uniqueness_score,
                conformity_score=conformity_score,
                min_length=None,  # Not calculated in chunked processing
                max_length=None,  # Not calculated in chunked processing
                avg_length=None,  # Not calculated in chunked processing
                most_common_values=most_common_values,
                conformity_violations=violations
            )
        
        return final_metrics
    
    def _create_dataset_profile(self, column_metrics: Dict[str, ColumnMetrics], total_rows: int, dataset_path: str) -> DatasetProfile:
        """Create dataset profile from finalized metrics."""
        # Calculate overall metrics from accumulated totals
        total_cells = sum(m.total_count for m in column_metrics.values() if m.total_count is not None)
        total_non_null = sum(m.non_null_count for m in column_metrics.values() if m.non_null_count is not None)
        total_conforming = sum(m.conforming_count for m in column_metrics.values() if m.conforming_count is not None)
        
        # Calculate weighted averages based on actual cell counts
        overall_completeness = (total_non_null / total_cells * 100) if total_cells > 0 else 0.0
        overall_conformity = (total_conforming / total_non_null * 100) if total_non_null > 0 else 0.0
        
        # Uniqueness is specific to NPI column
        npi_metrics = column_metrics.get('NPI')
        overall_uniqueness = npi_metrics.uniqueness_score if npi_metrics and npi_metrics.uniqueness_score is not None else 100.0
        
        # Estimate data size from total rows and columns
        estimated_data_size_mb = (total_rows * len(column_metrics) * 50) / (1024 * 1024)  # Rough estimate: 50 bytes per cell
        
        return DatasetProfile(
            dataset_name=Path(dataset_path).stem,
            total_rows=total_rows,
            total_columns=len(column_metrics),
            profiling_timestamp=datetime.now(),
            column_metrics=column_metrics,
            overall_completeness=overall_completeness,
            overall_uniqueness=overall_uniqueness,
            overall_conformity=overall_conformity,
            processing_time_seconds=0.0,  # Will be set by caller
            data_size_mb=estimated_data_size_mb
        )
    
    def _consolidate_issues(self, all_issues: List[Issue]) -> List[Issue]:
        """Consolidate issues from all chunks."""
        # Group issues by column and type
        issue_groups = {}
        
        for issue in all_issues:
            key = (issue.column_name, issue.issue_type)
            if key not in issue_groups:
                issue_groups[key] = []
            issue_groups[key].append(issue)
        
        # Consolidate grouped issues
        consolidated_issues = []
        
        for (column, issue_type), issues in issue_groups.items():
            if len(issues) == 1:
                consolidated_issues.append(issues[0])
            else:
                # Merge multiple issues of the same type
                total_count = sum(issue.count for issue in issues)
                total_percentage = sum(issue.percentage for issue in issues) / len(issues)
                all_examples = []
                for issue in issues:
                    all_examples.extend(issue.examples)
                
                consolidated_issues.append(Issue(
                    issue_id=str(uuid.uuid4()),
                    column_name=column,
                    issue_type=issue_type,
                    count=total_count,
                    percentage=total_percentage,
                    description=f"{column} has {total_count} {issue_type} issues across dataset",
                    examples=all_examples[:10],  # Limit examples
                    rule_violated=issues[0].rule_violated if issues else None
                ))
        
        return consolidated_issues