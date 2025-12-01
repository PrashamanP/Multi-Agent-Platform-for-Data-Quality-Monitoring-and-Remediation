"""
Validator Agent for checking data conformity against validation rules.
"""

import pandas as pd
import logging
import time
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing as mp
from tqdm import tqdm
from typing import Dict, List, Tuple, Any, Optional, Union
from src.core.data_models import ValidationRule, is_value_null_for_completeness, count_non_null_values
from src.core.validation_rule_loader import ValidationRuleLoader
from src.agents.base_agent import BaseAgent
from src.core.config_manager import ConfigManager
from src.data_access.file_handler import FileHandler

logger = logging.getLogger(__name__)


class ValidatorAgent(BaseAgent):
    """
    Agent responsible for validating data against conformity rules.
    
    This agent handles:
    - Loading and managing validation rules
    - Validating individual values against rules
    - Bulk validation of data series
    - Vectorized validation for performance
    """
    
    def __init__(self, validation_rules: Optional[Dict[str, ValidationRule]] = None,
                 config: Optional[Dict[str, Any]] = None,
                 validation_rule_loader: Optional[ValidationRuleLoader] = None):
        """
        Initialize validator agent.
        
        Args:
            validation_rules: Custom validation rules. If None, loads from config.
            config: Agent configuration. If None, loads from config manager.
            validation_rule_loader: ValidationRuleLoader instance. If None, creates new one.
        """
        # Initialize base agent
        super().__init__("validator", config)
        
        # Load configuration if not provided
        if config is None:
            config_manager = ConfigManager()
            self.config = config_manager.get_agent_config("validator")
        
        # Initialize validation rule loader
        self.validation_rule_loader = validation_rule_loader or ValidationRuleLoader()
        
        # Load validation rules
        if validation_rules is None:
            self.validation_rules = self.validation_rule_loader.load_validation_rules()
        else:
            self.validation_rules = validation_rules
        
        # Configuration settings
        self.max_violation_examples = self.config.get('max_violation_examples', 10)
        
        # Performance settings - same as ProfilerAgent
        self.enable_chunked_processing = self.config.get('enable_chunked_processing', True)
        self.chunk_size = self.config.get('chunk_size', 50000)
        self.chunked_processing_threshold_mb = self.config.get('chunked_processing_threshold_mb', 1000)
        self.parallel_processing = self.config.get('parallel_processing', True)
        self.max_workers = self.config.get('max_workers', 4)
        self.memory_efficient_mode = self.config.get('memory_efficient_mode', True)
        self.optimize_dtypes = self.config.get('optimize_dtypes', True)
        
        # Sampling settings
        self.enable_sampling = self.config.get('enable_sampling', False)
        self.sample_size = self.config.get('sample_size', 100000)
        
        # Progress tracking
        self.enable_progress_tracking = self.config.get('enable_progress_tracking', True)
        self.progress_update_interval = self.config.get('progress_update_interval', 10000)
        
        logger.info(f"ValidatorAgent initialized with {len(self.validation_rules)} validation rules")
    
    def execute(self, data: Union[pd.Series, str], column: str = None, **kwargs) -> Dict[str, Any]:
        """
        Execute validation on data series or bulk dataset validation on file.
        
        Args:
            data: Pandas Series to validate OR file path for bulk validation
            column: Column name for Series validation (required for Series, ignored for file path)
            **kwargs: Additional validation parameters
            
        Returns:
            Dictionary containing validation results
        """
        if isinstance(data, str):
            # Bulk dataset validation
            return self.execute_bulk_validation(data, **kwargs)
        else:
            # Single series validation (existing functionality)
            if column is None:
                raise ValueError("Column name is required for Series validation")
            
            # Single series validation
            
            conforming_count, non_conforming_count, violations = self.check_conformity(data, column)
            non_null_count = count_non_null_values(data, column)
            conformity_score = (conforming_count / non_null_count * 100) if non_null_count > 0 else 0
            
            return {
                'conforming_count': conforming_count,
                'non_conforming_count': non_conforming_count,
                'conformity_score': conformity_score,
                'violations': violations,
                'rule_applied': self.validation_rules.get(column, {}).rule_id if column in self.validation_rules else None
            }
    
    def execute_bulk_validation(self, dataset_path: str, **kwargs) -> Dict[str, Any]:
        """
        Execute bulk validation on entire dataset with optimized processing.
        
        Args:
            dataset_path: Path to CSV dataset file
            **kwargs: Additional validation parameters
            
        Returns:
            Dictionary containing comprehensive validation results for all columns
        """
        logger.info(f"Starting bulk validation for dataset: {dataset_path}")
        start_time = time.time()
        
        try:
            # Check file size to determine processing strategy
            file_size_mb = FileHandler.get_file_size_mb(dataset_path)
            logger.info(f"Dataset size: {file_size_mb:.2f} MB")
            
            # Choose processing strategy based on file size
            if (self.enable_chunked_processing and 
                file_size_mb > self.chunked_processing_threshold_mb):
                logger.info(f"Using chunked validation for large dataset ({file_size_mb:.2f} MB)")
                results = self._execute_bulk_chunked(dataset_path)
            else:
                logger.info("Using standard validation for small dataset")
                results = self._execute_bulk_standard(dataset_path)
            
            # Store execution metadata
            execution_time = time.time() - start_time
            results.update({
                "execution_metadata": {
                    "execution_time_seconds": execution_time,
                    "agent_version": "2.0.0",
                    "rules_applied": len(self.validation_rules),
                    "timestamp": time.time(),
                    "file_size_mb": file_size_mb,
                    "processing_mode": "chunked" if file_size_mb > self.chunked_processing_threshold_mb else "standard",
                    "chunk_size": self.chunk_size,
                    "parallel_processing": self.parallel_processing
                }
            })
            
            logger.info(f"Bulk validation completed in {execution_time:.2f}s.")
            
            return results
            
        except Exception as e:
            logger.error(f"Bulk validation failed: {str(e)}")
            raise
    
    def reload_validation_rules(self) -> None:
        """Reload validation rules from configuration."""
        self.validation_rules = self.validation_rule_loader.load_validation_rules()
        logger.info(f"Reloaded {len(self.validation_rules)} validation rules from configuration")
    
    def validate_value(self, value: Any, column: str) -> bool:
        """
        Validate a single value against the rule for the specified column.
        
        Args:
            value: Value to validate
            column: Column name to get the validation rule for
            
        Returns:
            True if value conforms to the rule, False otherwise
        """
        if column not in self.validation_rules:
            # If no rule defined, consider non-null values as conforming
            return not is_value_null_for_completeness(value, column)
        
        rule = self.validation_rules[column]
        return self._validate_value_with_rule(value, rule, column)
    
    def _validate_value_with_rule(self, value: Any, rule: ValidationRule, column: str) -> bool:
        """
        Validate a single value against a specific validation rule.
        
        Args:
            value: Value to validate
            rule: ValidationRule to apply
            column: Column name for context
            
        Returns:
            True if value conforms to the rule, False otherwise
        """
        # Use the new null detection logic
        if is_value_null_for_completeness(value, column):
            return False
            
        str_value = str(value).strip()
        
        if rule.rule_type == "regex":
            import re
            pattern = rule.rule_config.get("pattern", "")
            return bool(re.match(pattern, str_value))
        
        elif rule.rule_type == "enum":
            allowed_values = rule.rule_config.get("values", [])
            # Handle float-to-string conversion for numeric enum values
            if column == "Entity Type Code":
                # Convert "1.0" -> "1", "2.0" -> "2" 
                try:
                    float_val = float(str_value)
                    if float_val.is_integer():
                        normalized_value = str(int(float_val))
                        return normalized_value in allowed_values
                except ValueError:
                    pass
            return str_value in allowed_values
        
        elif rule.rule_type == "length":
            min_len = rule.rule_config.get("min", 0)
            max_len = rule.rule_config.get("max", float('inf'))
            return min_len <= len(str_value) <= max_len
        
        elif rule.rule_type == "numeric":
            try:
                float(str_value)
                return True
            except ValueError:
                return False
        
        elif rule.rule_type == "date":
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
                    
                if rule.rule_config.get("not_future", False):
                    return parsed_date.date() <= datetime.now().date()
                return True
            except ValueError:
                return False
        
        elif rule.rule_type == "phone":
            import re
            # Remove all non-digits
            digits_only = re.sub(r'\D', '', str_value)
            min_digits = rule.rule_config.get("min_digits", 7)
            max_digits = rule.rule_config.get("max_digits", 20)
            return min_digits <= len(digits_only) <= max_digits
        
        return False
    
    def check_conformity(self, series: pd.Series, column: str) -> Tuple[int, int, List[str]]:
        """
        Check conformity of column values against validation rules.
        
        Args:
            series: Pandas Series to validate
            column: Column name
            
        Returns:
            Tuple of (conforming_count, non_conforming_count, violations)
        """
        if column not in self.validation_rules:
            # If no rule defined, consider all non-null values as conforming
            non_null_count = count_non_null_values(series, column)
            return non_null_count, 0, []
        
        rule = self.validation_rules[column]
        conforming_count = 0
        violations = []
        
        # Check each value that is not null according to our new logic
        for value in series:
            # Skip values that should be considered null for completeness
            if is_value_null_for_completeness(value, column):
                continue
                
            if self._validate_value_with_rule(value, rule, column):
                conforming_count += 1
            else:
                # Collect sample violations (limited by config)
                if len(violations) < self.max_violation_examples:
                    violations.append(str(value))
        
        # Calculate non-conforming count using our new null detection logic
        non_null_count = count_non_null_values(series, column)
        non_conforming_count = non_null_count - conforming_count
        
        return conforming_count, non_conforming_count, violations
    
    def check_conformity_vectorized(self, series: pd.Series, column: str) -> Tuple[int, List[str]]:
        """
        Check conformity using vectorized operations where possible.
        
        Args:
            series: Pandas Series to validate
            column: Column name
            
        Returns:
            Tuple of (conforming_count, violations)
        """
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
            conforming_count, non_conforming_count, violations = self.check_conformity(series, column)
            return conforming_count, violations
    
    def get_validation_rule(self, column: str) -> Optional[ValidationRule]:
        """
        Get the validation rule for a specific column.
        
        Args:
            column: Column name
            
        Returns:
            ValidationRule if exists, None otherwise
        """
        return self.validation_rules.get(column)
    
    def has_validation_rule(self, column: str) -> bool:
        """
        Check if a validation rule exists for the specified column.
        
        Args:
            column: Column name
            
        Returns:
            True if validation rule exists, False otherwise
        """
        return column in self.validation_rules
    
    def get_all_columns_with_rules(self) -> List[str]:
        """
        Get list of all columns that have validation rules.
        
        Returns:
            List of column names
        """
        return list(self.validation_rules.keys())
    
    def _execute_bulk_standard(self, dataset_path: str) -> Dict[str, Any]:
        """Execute standard bulk validation for smaller datasets."""
        # Load dataset using FileHandler
        df = FileHandler.load_dataset(dataset_path)
        
        # Apply sampling if enabled for large datasets
        if self.enable_sampling and len(df) > self.sample_size:
            logger.info(f"Sampling {self.sample_size} rows from {len(df)} total")
            df = df.sample(n=self.sample_size, random_state=42)
        
        # Optimize data types if enabled
        if self.optimize_dtypes:
            df = self._optimize_dtypes(df)
        
        # Validate dataset
        if self.parallel_processing:
            validation_results = self._validate_dataset_parallel(df)
        else:
            validation_results = self._validate_dataset_sequential(df)
        
        # Calculate overall statistics
        overall_stats = self._calculate_overall_stats(validation_results, len(df))
        
        return {
            "dataset_name": Path(dataset_path).stem,
            "total_rows": len(df),
            "validation_results": validation_results,  # For compatibility with export methods
            "column_results": validation_results,     # New structure
            "overall_stats": overall_stats,
            "execution_metadata": {
                "processing_mode": "standard",
                "file_size_mb": FileHandler.get_file_size_mb(dataset_path),
                "columns_in_dataset": len(df.columns),
                "columns_validated": len(validation_results)
            }
        }
    
    def _execute_bulk_chunked(self, dataset_path: str) -> Dict[str, Any]:
        """Execute chunked bulk validation for large datasets."""
        # Starting chunked validation processing
        
        # Initialize accumulators
        validation_accumulator = {}
        total_rows = 0
        
        # Get total rows for progress tracking
        if self.enable_progress_tracking:
            total_rows_estimate = self._estimate_total_rows(dataset_path)
        
        # Process dataset in chunks
        chunk_iterator = FileHandler.load_dataset_chunks(
            dataset_path, 
            chunk_size=self.chunk_size
        )
        
        progress_bar = None
        if self.enable_progress_tracking:
            progress_bar = tqdm(
                total=None,
                desc="Validating chunks",
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
                chunk_results = self._validate_chunk(chunk_df, chunk_idx)
                
                # Accumulate results
                self._accumulate_validation_results(validation_accumulator, chunk_results)
                
                # Log progress periodically (minimal logging)
                if chunk_idx % 50 == 0 and chunk_idx > 0:  # Log less frequently
                    logger.debug(f"Processed {chunk_idx + 1} chunks, {total_rows:,} rows")
        
        finally:
            if self.enable_progress_tracking and progress_bar is not None:
                progress_bar.close()
        
        # Finalize results from accumulators
        
        final_results = self._finalize_validation_results(validation_accumulator, total_rows)
        overall_stats = self._calculate_overall_stats(final_results, total_rows)
        
        return {
            "dataset_name": Path(dataset_path).stem,
            "total_rows": total_rows,
            "validation_results": final_results,  # For compatibility with export methods
            "column_results": final_results,     # New structure
            "overall_stats": overall_stats,
            "execution_metadata": {
                "processing_mode": "chunked",
                "file_size_mb": FileHandler.get_file_size_mb(dataset_path),
                "columns_in_dataset": len(validation_accumulator),
                "columns_validated": len(final_results),
                "chunk_size": self.chunk_size
            }
        }
    
    def _validate_dataset_sequential(self, df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
        """Validate all columns in dataset sequentially."""
        results = {}
        
        # Validate ALL columns in the dataset, not just those with rules
        for column in df.columns:
            series = df[column]
            non_null_count = count_non_null_values(series, column)
            
            if self.has_validation_rule(column):
                # Column has validation rule - check conformity
                conforming, non_conforming, violations = self.check_conformity(series, column)
                rule_id = self.get_validation_rule(column).rule_id
            else:
                # Column has no validation rule - all non-null values are conforming
                conforming = non_null_count
                non_conforming = 0
                violations = []
                rule_id = None
            
            # Calculate conformity rate only (completeness is handled by ProfilerAgent)
            conformity_rate = (conforming / non_null_count * 100) if non_null_count > 0 else 0
            
            results[column] = {
                'total_count': len(series),
                'non_null_count': non_null_count,
                'null_count': len(series) - non_null_count,
                'conforming_count': conforming,
                'non_conforming_count': non_conforming,
                'conformity_rate': conformity_rate,
                'violations_count': non_conforming,
                'sample_violations': violations[:3],  # First 3 as samples
                'total_checked': non_null_count,
                'rule_type': self.get_validation_rule(column).rule_type if self.has_validation_rule(column) else 'N/A',
                'rule_applied': rule_id
            }
        
        return results
    
    def _validate_dataset_parallel(self, df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
        """Validate columns in parallel for better performance."""
        all_columns = list(df.columns)
        max_workers = min(self.max_workers, len(all_columns), mp.cpu_count())
        
        results = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit validation tasks for ALL columns
            future_to_column = {
                executor.submit(self._validate_column_optimized, df[col], col): col 
                for col in all_columns
            }
            
            try:
                for future in as_completed(future_to_column, timeout=300):
                    column = future_to_column[future]
                    try:
                        result = future.result()
                        results[column] = result
                    except Exception as e:
                        logger.error(f"Failed to validate column {column}: {e}")
                        # Create default result for failed columns
                        results[column] = self._create_default_validation_result(column, len(df))
            except KeyboardInterrupt:
                logger.warning("Validation interrupted by user. Cancelling remaining tasks...")
                for future in future_to_column.keys():
                    if not future.done():
                        future.cancel()
                raise
        
        return results
    
    def _validate_column_optimized(self, series: pd.Series, column: str) -> Dict[str, Any]:
        """Optimized validation of a single column."""
        non_null_count = count_non_null_values(series, column)
        
        # Use vectorized validation where possible
        if self.has_validation_rule(column):
            rule = self.get_validation_rule(column)
            if rule.rule_type in ['length', 'enum']:
                conforming, violations = self.check_conformity_vectorized(series, column)
                non_conforming = non_null_count - conforming
            else:
                conforming, non_conforming, violations = self.check_conformity(series, column)
        else:
            # No validation rule - all non-null values are conforming
            conforming = non_null_count
            non_conforming = 0
            violations = []
        
        # Calculate conformity rate only (completeness is handled by ProfilerAgent)
        conformity_rate = (conforming / non_null_count * 100) if non_null_count > 0 else 0
        
        return {
            'total_count': len(series),
            'non_null_count': non_null_count,
            'null_count': len(series) - non_null_count,
            'conforming_count': conforming,
            'non_conforming_count': non_conforming,
            'conformity_rate': conformity_rate,
            'violations_count': non_conforming,
            'sample_violations': violations[:3],  # First 3 as samples
            'total_checked': non_null_count,
            'rule_type': self.get_validation_rule(column).rule_type if self.has_validation_rule(column) else 'N/A',
            'rule_applied': self.get_validation_rule(column).rule_id if self.has_validation_rule(column) else None
        }
    
    def _validate_chunk(self, chunk_df: pd.DataFrame, chunk_idx: int) -> Dict[str, Dict[str, Any]]:
        """Validate a single chunk and return results."""
        chunk_results = {}
        
        # Optimize data types if enabled
        if self.optimize_dtypes:
            chunk_df = self._optimize_dtypes(chunk_df)
        
        # Process ALL columns in the chunk
        for column in chunk_df.columns:
            try:
                result = self._validate_column_optimized(chunk_df[column], column)
                chunk_results[column] = result
            except Exception as e:
                logger.error(f"Failed to validate column {column} in chunk: {str(e)}")
                chunk_results[column] = self._create_default_validation_result(column, len(chunk_df))
        
        return chunk_results
    
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
                # Try to convert to datetime with common formats
                try:
                    # Common date formats
                    date_formats = ['%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y', '%Y%m%d']
                    
                    converted = False
                    for fmt in date_formats:
                        try:
                            pd.to_datetime(optimized_df[col], format=fmt, errors='raise')
                            optimized_df[col] = pd.to_datetime(optimized_df[col], format=fmt, errors='coerce')
                            converted = True
                            break
                        except (ValueError, TypeError):
                            continue
                    
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
    
    def _estimate_total_rows(self, dataset_path: str) -> int:
        """Estimate total rows in dataset for progress tracking."""
        try:
            # Read first few lines to estimate
            with open(dataset_path, 'r') as f:
                first_line = f.readline()
                if not first_line:
                    return 0
                
                # Estimate based on file size and line length
                file_size_bytes = os.path.getsize(dataset_path)
                estimated_total = int((file_size_bytes / len(first_line.encode('utf-8'))) * 0.9)
                
                return max(estimated_total, 1000)  # Minimum estimate
                
        except Exception as e:
            logger.warning(f"Could not estimate total rows: {e}")
            return 0
    
    def _accumulate_validation_results(self, accumulator: Dict[str, Dict], chunk_results: Dict[str, Dict]) -> None:
        """Accumulate validation results from a chunk."""
        for column, results in chunk_results.items():
            if column not in accumulator:
                accumulator[column] = {
                    'total_count': 0,
                    'non_null_count': 0,
                    'null_count': 0,
                    'conforming_count': 0,
                    'non_conforming_count': 0,
                    'violations': [],
                    'rule_type': results.get('rule_type'),
                    'rule_applied': results.get('rule_applied')
                }
            
            # Accumulate counts
            accumulator[column]['total_count'] += results['total_count']
            accumulator[column]['non_null_count'] += results['non_null_count']
            accumulator[column]['null_count'] += results['null_count']
            accumulator[column]['conforming_count'] += results['conforming_count']
            accumulator[column]['non_conforming_count'] += results['non_conforming_count']
            
            # Accumulate violations (limited)
            existing_violations = len(accumulator[column]['violations'])
            if existing_violations < self.max_violation_examples:
                new_violations = results['sample_violations'][:self.max_violation_examples - existing_violations]
                accumulator[column]['violations'].extend(new_violations)
    
    def _finalize_validation_results(self, accumulator: Dict[str, Dict], total_rows: int) -> Dict[str, Dict[str, Any]]:
        """Finalize validation results from accumulated data."""
        final_results = {}
        
        for column, acc_data in accumulator.items():
            total_count = acc_data['total_count']
            non_null_count = acc_data['non_null_count']
            conforming_count = acc_data['conforming_count']
            
            # Calculate conformity rate only (completeness is handled by ProfilerAgent)
            conformity_rate = (conforming_count / non_null_count * 100) if non_null_count > 0 else 0
            
            final_results[column] = {
                'total_count': total_count,
                'non_null_count': non_null_count,
                'null_count': acc_data['null_count'],
                'conforming_count': conforming_count,
                'non_conforming_count': acc_data['non_conforming_count'],
                'conformity_rate': conformity_rate,
                'violations_count': acc_data['non_conforming_count'],
                'sample_violations': acc_data['violations'][:3],  # First 3 as samples
                'total_checked': non_null_count,
                'rule_type': acc_data['rule_type'],
                'rule_applied': acc_data['rule_applied']
            }
        
        return final_results
    
    def _calculate_overall_stats(self, validation_results: Dict[str, Dict], total_rows: int) -> Dict[str, Any]:
        """Calculate overall validation statistics (conformity only)."""
        if not validation_results:
            return {
                'overall_conformity': 100.0,
                'avg_conformity_rate': 100.0,
                'columns_validated': 0,
                'total_violations': 0
            }
        
        total_conforming = sum(r['conforming_count'] for r in validation_results.values())
        total_non_null = sum(r['non_null_count'] for r in validation_results.values())
        total_violations = sum(r['non_conforming_count'] for r in validation_results.values())
        
        overall_conformity = (total_conforming / total_non_null * 100) if total_non_null > 0 else 100.0
        avg_conformity_rate = sum(r['conformity_rate'] for r in validation_results.values()) / len(validation_results)
        
        return {
            'overall_conformity': overall_conformity,
            'avg_conformity_rate': avg_conformity_rate,
            'columns_validated': len(validation_results),
            'total_violations': total_violations,
            'total_cells_validated': total_non_null
        }
    
    def check_conformity_vectorized(self, series: pd.Series, column: str) -> Tuple[int, List[str]]:
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
            conforming_count, non_conforming_count, violations = self.check_conformity(series, column)
            return conforming_count, violations
    
    def _create_default_validation_result(self, column: str, total_count: int) -> Dict[str, Any]:
        """Create default validation result for failed columns."""
        return {
            'total_count': total_count,
            'non_null_count': 0,
            'conforming_count': 0,
            'non_conforming_count': 0,
            'conformity_score': 0.0,
            'violations': [],
            'rule_applied': None
        }
    
    def export_validation_results_to_csv(self, validation_results: Dict[str, Any], 
                                       dataset_name: str, 
                                       output_path: str) -> None:
        """Export detailed validation results to CSV file."""
        import csv
        from pathlib import Path
        
        validation_data = validation_results.get('column_results', {})
        
        # Sort columns by conformity score (worst first) for better visibility
        columns_to_export = sorted(
            validation_data.items(),
            key=lambda x: x[1].get('conformity_rate', 0)
        )
        
        with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['Column Name', 'Conformity %', 'Violations Count', 'Total Checked', 'Rule Type', 'Sample Violations']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            writer.writeheader()
            for col_name, metrics in columns_to_export:
                # Format sample violations for CSV
                sample_violations = metrics.get('sample_violations', [])
                violations_str = '; '.join(str(v) for v in sample_violations[:3]) if sample_violations else ""
                
                writer.writerow({
                    'Column Name': col_name,
                    'Conformity %': f"{metrics.get('conformity_rate', 0):.2f}%",
                    'Violations Count': metrics.get('violations_count', 0),
                    'Total Checked': metrics.get('total_checked', 0),
                    'Rule Type': metrics.get('rule_type', 'N/A'),
                    'Sample Violations': violations_str
                })
        
        logger.info(f"Validation results exported to: {output_path}")
        logger.info(f"Total columns exported: {len(columns_to_export)}")
    
    def export_validation_results_to_json(self, validation_results: Dict[str, Any], 
                                        dataset_name: str, 
                                        output_path: str) -> None:
        """Export validation results to JSON for further analysis."""
        import json
        from datetime import datetime
        
        def serialize_datetime(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object {obj} is not JSON serializable")
        
        overall_stats = validation_results.get('overall_stats', {})
        
        # Create export data structure
        export_data = {
            'dataset_name': dataset_name,
            'validation_summary': {
                'total_columns_validated': len(validation_results.get('column_results', {})),
                'overall_conformity': overall_stats.get('avg_conformity_rate', 0),
                'total_violations': overall_stats.get('total_violations', 0)
            },
            'column_results': validation_results.get('column_results', {}),
            'processing_info': validation_results.get('execution_metadata', {}),
            'validated_at': datetime.now().isoformat()
        }
        
        with open(output_path, 'w') as f:
            json.dump(export_data, f, indent=2, default=serialize_datetime)
        
        logger.info(f"Validation results exported to JSON: {output_path}")
    
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
