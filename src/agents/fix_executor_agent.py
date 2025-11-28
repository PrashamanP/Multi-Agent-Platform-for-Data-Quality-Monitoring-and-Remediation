"""
Fix Executor Agent for automatically applying safe data quality fixes.

This agent takes actionable fix recommendations and applies them to the dataset,
with automatic backup creation and validation.
"""

import logging
import time
import uuid
import shutil
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from dataclasses import dataclass, field

import pandas as pd

from src.agents.base_agent import BaseAgent
from src.core.config_manager import ConfigManager
from src.core.data_models import (
    FixRecommendation,
    FixRecommendationResults,
    ValidationRule,
    is_value_null_for_completeness,
)
from src.core.validation_rule_loader import ValidationRuleLoader
from src.data_access.file_handler import FileHandler

logger = logging.getLogger(__name__)


@dataclass
class FixResult:
    """Result of applying a single fix."""
    recommendation_id: str
    column_name: str
    fix_strategy: str
    rows_fixed: int
    rows_failed: int
    fix_details: List[Dict[str, Any]] = field(default_factory=list)
    success: bool = True


@dataclass
class FixExecutionResults:
    """Results from fix execution."""
    dataset_name: str
    original_path: str
    fixed_path: Optional[str]
    backup_path: Optional[str]
    fixes_applied: List[FixResult] = field(default_factory=list)
    total_rows_fixed: int = 0
    execution_metadata: Dict[str, Any] = field(default_factory=dict)


class FixExecutorAgent(BaseAgent):
    """
    Agent responsible for automatically applying safe data quality fixes.
    
    Currently supports:
    - Conformity fixes: format corrections, type normalization, whitespace cleanup
    - Completeness fixes: conditional imputation (only for Entity Type Code with row-by-row logic)
    
    Safety features:
    - Automatic backup creation
    - Validation after fixes
    - Detailed logging of all changes
    
    IMPORTANT: This agent processes ALL rows in the dataset, not a sample.
    The max_sample_details setting only controls how many fix examples are
    stored in the results for reporting purposes - it does not limit which
    rows are checked or fixed.
    """
    
    def __init__(
        self,
        validation_rules: Optional[Dict[str, ValidationRule]] = None,
        config: Optional[Dict[str, Any]] = None,
        validation_rule_loader: Optional[ValidationRuleLoader] = None
    ):
        """
        Initialize fix executor agent.
        
        Args:
            validation_rules: Custom validation rules. If None, loads from config.
            config: Agent configuration. If None, loads from config manager.
            validation_rule_loader: ValidationRuleLoader instance. If None, creates new one.
        """
        super().__init__("fix_executor", config)
        
        # Load configuration if not provided
        if config is None:
            config_manager = ConfigManager()
            self.config = config_manager.get_agent_config("fix_executor") or {}
        
        # Initialize validation rule loader
        self.validation_rule_loader = validation_rule_loader or ValidationRuleLoader()
        
        # Load validation rules
        if validation_rules is None:
            self.validation_rules = self.validation_rule_loader.load_validation_rules()
        else:
            self.validation_rules = validation_rules
        
        # Configuration
        self.create_backup = self.config.get("create_backup", True)
        self.validate_after_fix = self.config.get("validate_after_fix", True)
        self.max_sample_details = self.config.get("max_sample_details", 10)
        # Recommendations-only mode: if True, only generate recommendations, don't apply any fixes
        self.recommendations_only = self.config.get("recommendations_only", False)
        # Non-destructive fix configuration
        self.add_fix_columns = self.config.get("add_fix_columns", True) if not self.recommendations_only else False
        self.fix_column_suffix = self.config.get("fix_column_suffix", "__auto_fixed")
        self.add_fix_flags = self.config.get("add_fix_flags", True) if not self.recommendations_only else False
        self.add_recommendation_column = self.config.get("add_recommendation_column", True)
        # Safety setting: if True, original columns are NEVER modified
        self.never_modify_original_columns = self.config.get("never_modify_original_columns", True)
        
        # Enforce non-destructive mode if never_modify_original_columns is True (unless recommendations_only)
        if self.never_modify_original_columns and not self.recommendations_only:
            self.add_fix_columns = True
            logger.info("never_modify_original_columns is enabled - original columns will never be modified")
        
        # Log recommendations-only mode
        if self.recommendations_only:
            logger.info("RECOMMENDATIONS-ONLY MODE: No fixes will be applied, only recommendations will be generated")
        self.emit_recommendations_sidecar = self.config.get("emit_recommendations_sidecar", "same_dir")
        # Write mode configuration
        self.write_mode = self.config.get("write_mode", "in_place")  # "in_place" or "new_file"
        self.new_file_suffix = self.config.get("new_file_suffix", "_fixed")
        # Allowed fix types
        self.allowed_fix_types = set(self.config.get("allowed_fix_types", ["conformity"]))
        
        # Reference mapping files
        self.city_state_mapping_file = self.config.get("city_state_mapping_file", "data/reference/city_state_mapping.csv")
        self.postal_state_mapping_file = self.config.get("postal_state_mapping_file", "data/reference/postal_state_mapping.csv")
        self.state_country_mapping_file = self.config.get("state_country_mapping_file", "data/reference/state_country_mapping.csv")
        # Caching for mapping files
        self._city_state_map: Optional[Dict[str, str]] = None
        self._postal_state_map: Optional[Dict[str, str]] = None
        self._state_country_map: Optional[Dict[Tuple[str, str], bool]] = None  # (state_name, country_code) -> bool
        
        logger.info("FixExecutorAgent initialized")
    
    def execute(
        self,
        recommendations: FixRecommendationResults,
        dataset_path: str,
        output_path: Optional[str] = None,
        apply_only_actionable: bool = True
    ) -> FixExecutionResults:
        """
        Execute fixes on a dataset based on recommendations.
        
        Args:
            recommendations: Fix recommendation results
            dataset_path: Path to the dataset file
            output_path: Path to save fixed dataset (if None, overwrites original)
            apply_only_actionable: Only apply fixes marked as actionable
            
        Returns:
            FixExecutionResults with details of applied fixes
        """
        start_time = time.time()
        
        # For conformity issues: process ALL recommendations (ignore actionable flag)
        # We'll try to fix any value that can be corrected by our fix rules
        conformity_recs = [
            r for r in recommendations.recommendations
            if r.issue_type == "conformity" and r.issue_type in self.allowed_fix_types
        ]
        
        # For completeness issues: collect them for reporting but don't process
        # (completeness fixes are disabled for now)
        completeness_recs_all = [
            r for r in recommendations.recommendations
            if r.issue_type == "completeness" 
        ]
        # Only include in processing if explicitly allowed
        completeness_recs = [
            r for r in completeness_recs_all
            if r.issue_type in self.allowed_fix_types
        ]
        
        # For other allowed types, still respect actionable flag if apply_only_actionable is True
        other_recs = [
            r for r in recommendations.recommendations
            if r.issue_type in self.allowed_fix_types 
            and r.issue_type not in ["conformity", "completeness"]
        ]
        if apply_only_actionable:
            other_recs = [r for r in other_recs if r.actionable]
        
        # Only process allowed fix types (completeness excluded if not in allowed_fix_types)
        all_recs = conformity_recs + completeness_recs + other_recs
        
        if not all_recs:
            logger.warning("No recommendations to process for allowed fix types")
            return FixExecutionResults(
                dataset_name=recommendations.dataset_name,
                original_path=dataset_path,
                fixed_path=None,
                backup_path=None,
                execution_metadata={
                    "execution_time_seconds": time.time() - start_time,
                    "timestamp": datetime.now().isoformat(),
                    "total_recommendations": len(recommendations.recommendations),
                    "conformity_recommendations": len(conformity_recs),
                    "completeness_recommendations": len(completeness_recs)
                }
            )
        
        # Log completeness issues separately if they exist but aren't being processed
        if completeness_recs_all and not completeness_recs:
            logger.info(f"Found {len(completeness_recs_all)} completeness recommendations (not processing - completeness fixes disabled)")
            # List the completeness issues for visibility
            for rec in completeness_recs_all[:10]:  # Show first 10
                logger.info(f"  - {rec.column_name}: {rec.fix_description} (confidence: {rec.confidence_score:.1%})")
            if len(completeness_recs_all) > 10:
                logger.info(f"  ... and {len(completeness_recs_all) - 10} more completeness issues")
        
        logger.info(f"Processing {len(all_recs)} recommendations ({len(conformity_recs)} conformity, {len(completeness_recs)} completeness) to {dataset_path}")
        
        # Load dataset
        try:
            df = FileHandler.load_dataset(dataset_path, focused_columns_only=False)
        except Exception as e:
            logger.error(f"Failed to load dataset: {e}")
            raise
        
        original_row_count = len(df)
        
        # Calculate baseline metrics BEFORE fixes (original state)
        baseline_metrics = None
        if self.validate_after_fix:
            baseline_metrics = self._calculate_baseline_metrics(df, all_recs)
        
        # Determine output path before backup logic
        output_path_resolved = output_path
        if output_path_resolved is None:
            if self.write_mode == "new_file":
                in_path = Path(dataset_path)
                output_path_resolved = str(in_path.parent / f"{in_path.stem}{self.new_file_suffix}{in_path.suffix}")
            else:
                output_path_resolved = dataset_path
        else:
            Path(output_path_resolved).parent.mkdir(parents=True, exist_ok=True)

        # Create backup only if overwriting in place
        backup_path = None
        if self.create_backup and output_path_resolved == dataset_path:
            backup_path = self._create_backup(dataset_path)
            logger.info(f"Backup created: {backup_path}")
        
        # Apply fixes
        fixes_applied = []
        total_rows_fixed = 0
        # Collector for per-row recommendations (full list, not truncated)
        row_recommendations: List[Dict[str, Any]] = []
        
        for rec in all_recs:
            try:
                # Count existing recommendations for this column before applying fix
                recommendations_before = len([r for r in row_recommendations if r.get("column") == rec.column_name])
                
                fix_result = self._apply_fix(df, rec, row_recommendations)
                fixes_applied.append(fix_result)
                total_rows_fixed += fix_result.rows_fixed
                
                # Count recommendations added for this column
                recommendations_after = len([r for r in row_recommendations if r.get("column") == rec.column_name])
                recommendations_added = recommendations_after - recommendations_before
                
                # Show recommendation details without "Fixed" or "Failed" language
                recommendation_text = ""
                
                if rec.issue_type == "conformity":
                    # Conformity fixes generate row-by-row recommendations
                    if recommendations_added > 0:
                        recommendation_text = f"Format correction recommendations available ({recommendations_added} rows with recommended values)"
                    else:
                        recommendation_text = "No recommendation value available"
                elif rec.issue_type == "completeness":
                    # Completeness fixes may have suggested_value or use context/conditional imputation
                    if rec.suggested_value is not None and str(rec.suggested_value).strip() != "":
                        recommendation_text = f"Recommended value: {rec.suggested_value}"
                    elif rec.fix_strategy == "context_imputation":
                        recommendation_text = "Context-based inference available"
                    elif rec.fix_strategy == "conditional_imputation":
                        recommendation_text = "Conditional imputation based on related fields"
                    elif recommendations_added > 0:
                        recommendation_text = f"Recommendations generated ({recommendations_added} rows)"
                    else:
                        recommendation_text = "No recommendation value available"
                else:
                    # Other issue types
                    if rec.suggested_value is not None and str(rec.suggested_value).strip() != "":
                        recommendation_text = f"Recommended value: {rec.suggested_value}"
                    elif recommendations_added > 0:
                        recommendation_text = f"Recommendations generated ({recommendations_added} rows)"
                    else:
                        recommendation_text = "No recommendation value available"
                
                    logger.info(
                    f"{rec.column_name}: {recommendation_text} "
                    f"({fix_result.rows_fixed + fix_result.rows_failed} rows with issues)"
                    )
            except Exception as e:
                logger.error(f"Error applying fix for {rec.column_name}: {e}")
                fixes_applied.append(FixResult(
                    recommendation_id=rec.recommendation_id,
                    column_name=rec.column_name,
                    fix_strategy=rec.fix_strategy,
                    rows_fixed=0,
                    rows_failed=0,
                    success=False,
                    fix_details=[{"error": str(e)}]
                ))

        # Save fixed dataset
        try:
            FileHandler.save_dataset(df, output_path_resolved)
            logger.info(f"Fixed dataset saved to: {output_path_resolved}")
        except Exception as e:
            logger.error(f"Failed to save fixed dataset: {e}")
            raise

        # Emit sidecar with per-row recommendations if enabled
        try:
            if self.emit_recommendations_sidecar == "same_dir":
                out_path = Path(output_path_resolved)
                sidecar_path = out_path.parent / f"{out_path.stem}_fix_recommendations.csv"
                sidecar_path.parent.mkdir(parents=True, exist_ok=True)
                if row_recommendations:
                    pd.DataFrame(row_recommendations).to_csv(sidecar_path, index=False)
                else:
                    # Always create the sidecar with headers so it appears in the sidebar
                    pd.DataFrame(
                        columns=[
                            "row_index","column","issue_type","original_value",
                            "recommended_value","strategy","confidence","reason"
                        ]
                    ).to_csv(sidecar_path, index=False)
                logger.info(f"Per-row fix recommendations saved to: {sidecar_path}")
        except Exception as e:
            logger.warning(f"Failed to write per-row fix recommendations sidecar: {e}")
        
        # Validate fixes if enabled
        validation_results = None
        if self.validate_after_fix:
            validation_results = self._validate_fixes(df, fixes_applied, row_recommendations)
            formatted_results = self._format_validation_results(
                baseline_metrics, validation_results
            )
            logger.info(f"\n{formatted_results}")
        
        execution_time = time.time() - start_time
        
        metadata = {
            "execution_time_seconds": execution_time,
            "timestamp": datetime.now().isoformat(),
            "original_row_count": original_row_count,
            "final_row_count": len(df),
            "total_recommendations": len(recommendations.recommendations),
            "conformity_recommendations_processed": len(conformity_recs),
            "completeness_recommendations_processed": len(completeness_recs),
            "other_recommendations_processed": len(other_recs),
            "fixes_applied": len(fixes_applied),
            "total_rows_fixed": total_rows_fixed,
            "validation_results": validation_results,
            "backup_created": backup_path is not None,
            "backup_path": str(backup_path) if backup_path else None
        }
        
        logger.info(
            f"Fix execution completed: {len(fixes_applied)} fixes applied, "
            f"{total_rows_fixed} rows fixed in {execution_time:.2f}s"
        )
        
        return FixExecutionResults(
            dataset_name=recommendations.dataset_name,
            original_path=dataset_path,
            fixed_path=output_path_resolved,
            backup_path=backup_path,
            fixes_applied=fixes_applied,
            total_rows_fixed=total_rows_fixed,
            execution_metadata=metadata
        )
    
    def _create_backup(self, dataset_path: str) -> str:
        """Create a backup of the original dataset."""
        path = Path(dataset_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.parent / f"{path.stem}_backup_{timestamp}{path.suffix}"
        
        shutil.copy2(dataset_path, backup_path)
        return str(backup_path)
    
    def _apply_fix(self, df: pd.DataFrame, recommendation: FixRecommendation, row_recommendations: Optional[List[Dict[str, Any]]] = None) -> FixResult:
        """
        Apply a single fix to the dataset.
        
        Args:
            df: DataFrame to fix
            recommendation: Fix recommendation to apply
            
        Returns:
            FixResult with details of what was fixed
        """
        column_name = recommendation.column_name
        
        # Find the actual column name (handle case sensitivity)
        actual_column = self._find_column(df, column_name)
        if not actual_column:
            return FixResult(
                recommendation_id=recommendation.recommendation_id,
                column_name=column_name,
                fix_strategy=recommendation.fix_strategy,
                rows_fixed=0,
                rows_failed=0,
                success=False,
                fix_details=[{"error": f"Column '{column_name}' not found in dataset"}]
            )
        
        rows_fixed = 0
        rows_failed = 0
        fix_details = []
        
        # Route to appropriate fix method
        if recommendation.issue_type == "conformity":
            rows_fixed, rows_failed, fix_details = self._fix_conformity(
                df, actual_column, recommendation, row_recommendations
            )
        elif recommendation.issue_type == "completeness":
            rows_fixed, rows_failed, fix_details = self._fix_completeness(
                df, actual_column, recommendation, row_recommendations
            )
        else:
            # Not an automatically implemented fix type; add Need Review suggestions if allowed
            if recommendation.issue_type in self.allowed_fix_types:
                self._add_need_review_suggestions(df, recommendation, row_recommendations)
            return FixResult(
                recommendation_id=recommendation.recommendation_id,
                column_name=column_name,
                fix_strategy=recommendation.fix_strategy,
                rows_fixed=0,
                rows_failed=0,
                success=False,
                fix_details=[{"info": "Marked rows as Need Review in sidecar"}]
            )
        
        return FixResult(
            recommendation_id=recommendation.recommendation_id,
            column_name=column_name,
            fix_strategy=recommendation.fix_strategy,
            rows_fixed=rows_fixed,
            rows_failed=rows_failed,
            success=rows_failed == 0,
            fix_details=fix_details[:self.max_sample_details]
        )
    
    def _is_medical_degree_or_title(self, text: str) -> bool:
        """
        Check if a text string is a medical degree or title.
        
        Args:
            text: The text to check (typically the part after a comma in a name)
            
        Returns:
            True if the text appears to be a medical degree/title, False otherwise
        """
        if not text:
            return False
        
        text_upper = text.strip().upper()
        
        # Common medical degrees and titles
        medical_degrees = {
            # Medical degrees
            "MD", "DO", "MBBS", "MBCHB", "MBBCH",
            # Doctoral degrees
            "PHD", "PH.D", "PH.D.", "EDD", "ED.D", "ED.D.",
            # Legal degrees
            "JD", "J.D", "J.D.", "LLM", "LL.M", "LL.M.",
            # Nursing degrees
            "RN", "R.N", "R.N.", "LPN", "L.P.N", "L.P.N.", "NP", "N.P", "N.P.",
            "APRN", "A.P.R.N", "A.P.R.N.", "CNM", "C.N.M", "C.N.M.", "CRNA", "C.R.N.A", "C.R.N.A.",
            # Allied health degrees
            "PA", "P.A", "P.A.", "PA-C", "P.A.-C", "P.A.-C.",
            "PT", "P.T", "P.T.", "DPT", "D.P.T", "D.P.T.",
            "OT", "O.T", "O.T.", "OTR", "O.T.R", "O.T.R.",
            "RT", "R.T", "R.T.", "RRT", "R.R.T", "R.R.T.",
            "RD", "R.D", "R.D.", "RDN", "R.D.N", "R.D.N.",
            # Mental health licenses
            "LCSW", "L.C.S.W", "L.C.S.W.", "LMSW", "L.M.S.W", "L.M.S.W.",
            "LPC", "L.P.C", "L.P.C.", "LPCC", "L.P.C.C", "L.P.C.C.",
            "LMFT", "L.M.F.T", "L.M.F.T.", "LMHC", "L.M.H.C", "L.M.H.C.",
            "LISW", "L.I.S.W", "L.I.S.W.", "LICSW", "L.I.C.S.W", "L.I.C.S.W.",
            # Pharmacy degrees
            "PHARMD", "PHARM.D", "PHARM.D.", "RPH", "R.P.H", "R.P.H.",
            # Dental degrees
            "DDS", "D.D.S", "D.D.S.", "DMD", "D.M.D", "D.M.D.",
            # Veterinary degrees
            "DVM", "D.V.M", "D.V.M.", "VMD", "V.M.D", "V.M.D.",
            # Other healthcare titles
            "DC", "D.C", "D.C.",  # Chiropractor
            "OD", "O.D", "O.D.",  # Optometrist
            "AUD", "A.U.D", "A.U.D.",  # Audiologist
            "CNS", "C.N.S", "C.N.S.",  # Clinical Nurse Specialist
        }
        
        # Check exact match
        if text_upper in medical_degrees:
            return True
        
        # Check if it's a pattern like "MD, PhD" or "LCSW, LPC"
        # Split by comma and check each part
        parts = [p.strip().upper() for p in text_upper.split(',')]
        if all(part in medical_degrees for part in parts if part):
            return True
        
        # Check if it starts with a medical degree (e.g., "MD, something")
        first_part = parts[0] if parts else ""
        if first_part in medical_degrees:
            return True
        
        return False
    
    def _split_name_and_degree(self, full_name: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Split a name that contains a medical degree/title.
        
        Args:
            full_name: The full name string (e.g., "Smith, MD" or "Johnson, LCSW, LPC")
            
        Returns:
            Tuple of (name_part, degree_part) or (None, None) if no degree detected
        """
        if not full_name or ',' not in full_name:
            return None, None
        
        # Split by comma
        parts = [p.strip() for p in full_name.split(',')]
        
        if len(parts) < 2:
            return None, None
        
        # Check if the last part(s) are medical degrees
        # Try from the end: check if last part is a degree, then last two parts, etc.
        for i in range(1, len(parts) + 1):
            degree_part = ', '.join(parts[-i:])
            if self._is_medical_degree_or_title(degree_part):
                name_part = ', '.join(parts[:-i]).strip()
                if name_part:  # Make sure we have a name part
                    return name_part, degree_part
        
        return None, None
    
    def _fix_conformity(
        self,
        df: pd.DataFrame,
        column: str,
        recommendation: FixRecommendation,
        row_recommendations: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[int, int, List[Dict[str, Any]]]:
        """
        Fix conformity issues in a column.
        
        Returns:
            Tuple of (rows_fixed, rows_failed, fix_details)
        """
        rows_fixed = 0
        rows_failed = 0
        fix_details = []
        
        rule = self.validation_rules.get(recommendation.column_name)
        if not rule:
            return 0, 0, [{"error": f"No validation rule found for {recommendation.column_name}"}]
        
        # Special handling for Provider Last Name: split medical degrees/titles
        is_last_name_column = recommendation.column_name == "Provider Last Name (Legal Name)"
        medical_title_col = None
        if is_last_name_column:
            # Create a new column for medical title/degree if it doesn't exist
            medical_title_col = "Provider Medical Title/Degree"
            if medical_title_col not in df.columns:
                df[medical_title_col] = pd.Series([pd.NA] * len(df))
        
        # Apply fixes based on rule type
        # NOTE: This processes ALL rows in the dataset, not a sample.
        # max_sample_details only limits how many fix examples are stored in the results.
        # Prepare non-destructive columns if configured
        # Use new columns if add_fix_columns is True OR never_modify_original_columns is True
        use_new_columns = self.add_fix_columns or self.never_modify_original_columns
        fixed_col = f"{column}{self.fix_column_suffix}" if use_new_columns else None
        fix_applied_col = f"{column}__fix_applied" if use_new_columns and self.add_fix_flags else None
        fix_reason_col = f"{column}__fix_reason" if use_new_columns and self.add_fix_flags else None
        recommendation_col = f"{column}__recommendation" if self.add_recommendation_column else None
        if fixed_col and fixed_col not in df.columns:
            df[fixed_col] = pd.Series([pd.NA] * len(df))
        if fix_applied_col and fix_applied_col not in df.columns:
            df[fix_applied_col] = pd.Series([False] * len(df))
        if fix_reason_col and fix_reason_col not in df.columns:
            df[fix_reason_col] = pd.Series([pd.NA] * len(df))
        if recommendation_col and recommendation_col not in df.columns:
            df[recommendation_col] = pd.Series([pd.NA] * len(df))

        reason_map = {
            "enum": "enum normalization",
            "regex": "regex cleanup",
            "length": "length truncation",
            "phone": "phone cleanup",
            "date": "date reformat",
            "numeric": "numeric parse"
        }
        
        # Check if this is a state column for special handling
        is_state_column = "State" in recommendation.column_name and "Address" in recommendation.column_name
        # Check if this is a postal code column for special handling
        is_postal_code_column = "Postal Code" in recommendation.column_name and "Address" in recommendation.column_name
        # Military state codes
        military_codes = {"AE", "AP", "AA"}

        for idx in df.index:
            original_value = df.at[idx, column]
            
            # Skip null values
            if is_value_null_for_completeness(original_value, recommendation.column_name):
                continue
            
            # Special handling for Provider Last Name: split medical degrees/titles
            name_value = original_value
            degree_value = None
            if is_last_name_column and medical_title_col:
                name_part, degree_part = self._split_name_and_degree(str(original_value))
                if name_part and degree_part:
                    # We found a medical degree - split it
                    name_value = name_part
                    degree_value = degree_part
            
            # For state columns: try to convert full names to abbreviations before validation
            # This ensures "New York" -> "NY" even if it somehow passes initial validation
            state_was_converted = False
            if is_state_column and not rule.validate_value(name_value):
                # Try converting full state name to abbreviation
                converted_state = self._convert_state_to_abbreviation(str(name_value))
                if converted_state and rule.validate_value(converted_state):
                    # Use the converted abbreviation
                    name_value = converted_state
                    state_was_converted = True
            
            # Check if value is already conforming (use name_value after potential split)
            if rule.validate_value(name_value):
                # If we converted a state name, we should still apply the fix to save the converted value
                if state_was_converted:
                    # Apply the converted value
                    if not self.recommendations_only:
                        if self.add_fix_columns or self.never_modify_original_columns:
                            df.at[idx, fixed_col] = name_value
                            if self.add_fix_flags:
                                df.at[idx, fix_applied_col] = True
                                df.at[idx, fix_reason_col] = "converted full state name to abbreviation"
                            if self.add_recommendation_column and recommendation_col:
                                df.at[idx, recommendation_col] = f"Converted '{original_value}' to '{name_value}'"
                        elif not self.never_modify_original_columns:
                            df.at[idx, column] = name_value
                    else:
                        # Recommendations-only mode
                        if self.add_recommendation_column and recommendation_col:
                            df.at[idx, recommendation_col] = f"Convert '{original_value}' to '{name_value}'"
                    
                    if row_recommendations is not None:
                        row_recommendations.append({
                            "row_index": int(idx),
                            "column": recommendation.column_name,
                            "issue_type": recommendation.issue_type,
                            "original_value": original_value,
                            "recommended_value": name_value,
                            "strategy": recommendation.fix_strategy,
                            "confidence": recommendation.confidence_score,
                            "reason": "converted full state name to abbreviation"
                        })
                    rows_fixed += 1
                    continue
                # If we split a degree from a conforming name, still process it
                if degree_value and medical_title_col:
                    # Store the degree in the new column
                    if not self.recommendations_only:
                        df.at[idx, medical_title_col] = degree_value
                    # Add to recommendations
                    if self.add_recommendation_column and recommendation_col:
                        rec_text = f"Medical degree/title '{degree_value}' moved to separate column"
                        df.at[idx, recommendation_col] = rec_text
                    if row_recommendations is not None:
                        row_recommendations.append({
                            "row_index": int(idx),
                            "column": recommendation.column_name,
                            "issue_type": "data_enhancement",
                            "original_value": original_value,
                            "recommended_value": name_value,
                            "strategy": "split_from_last_name",
                            "confidence": 1.0,
                            "reason": f"Split medical degree/title: {degree_value}"
                        })
                        # Also add entry for the medical title column
                        row_recommendations.append({
                            "row_index": int(idx),
                            "column": medical_title_col,
                            "issue_type": "data_enhancement",
                            "original_value": None,
                            "recommended_value": degree_value,
                            "strategy": "split_from_last_name",
                            "confidence": 1.0,
                            "reason": f"Extracted from Provider Last Name: {original_value}"
                        })
                    # Apply the name part to fixed column if needed
                    if not self.recommendations_only:
                        if self.add_fix_columns or self.never_modify_original_columns:
                            df.at[idx, fixed_col] = name_value
                            if self.add_fix_flags:
                                df.at[idx, fix_applied_col] = True
                                df.at[idx, fix_reason_col] = f"split medical degree/title: {degree_value}"
                    rows_fixed += 1
                continue
            
                # If it's a military state code, add recommendation note
                if is_state_column and str(original_value).strip().upper() in military_codes:
                    if self.add_recommendation_column and recommendation_col:
                        df.at[idx, recommendation_col] = "Military Mail Locations"
                    if row_recommendations is not None:
                        row_recommendations.append({
                            "row_index": int(idx),
                            "column": recommendation.column_name,
                            "issue_type": recommendation.issue_type,
                            "original_value": original_value,
                            "recommended_value": original_value,  # Keep the same value
                            "strategy": recommendation.fix_strategy,
                            "confidence": recommendation.confidence_score,
                            "reason": "Military Mail Locations"
                        })
                continue
            
            # For state columns: check if it's a foreign state
            if is_state_column:
                # Get country code from the same row to check if it's a foreign address
                country_code = None
                if "Mailing" in recommendation.column_name:
                    country_col = self._find_column(df, "Provider Business Mailing Address Country Code (If outside U.S.)")
                elif "Practice Location" in recommendation.column_name:
                    country_col = self._find_column(df, "Provider Business Practice Location Address Country Code (If outside U.S.)")
                else:
                    country_col = None
                
                if country_col and country_col in df.columns:
                    country_code = df.at[idx, country_col]
                
                # Check if it's a valid foreign state (has non-US country code and state is valid for that country)
                is_foreign_state = False
                if country_code and not is_value_null_for_completeness(country_code, "Country Code"):
                    # Validate that the state actually exists for this country
                    is_foreign_state = self._is_valid_foreign_state(original_value, country_code)
                
                if is_foreign_state:
                    # Valid foreign state - add recommendation
                    recommendation_text = "foreign state name"
                    if self.add_recommendation_column and recommendation_col:
                        df.at[idx, recommendation_col] = recommendation_text
                    if row_recommendations is not None:
                        row_recommendations.append({
                            "row_index": int(idx),
                            "column": recommendation.column_name,
                            "issue_type": recommendation.issue_type,
                            "original_value": original_value,
                            "recommended_value": original_value,  # Keep the same value
                            "strategy": recommendation.fix_strategy,
                            "confidence": recommendation.confidence_score,
                            "reason": recommendation_text
                        })
                    continue  # Skip further processing for foreign states
                
                # Not a foreign state - add to CSV but mark as needing review
                rows_failed += 1
                if self.add_recommendation_column and recommendation_col:
                    df.at[idx, recommendation_col] = self._generate_recommendation_text(
                        recommendation, original_value, None, rule, fixed=False
                    )
                if row_recommendations is not None:
                    row_recommendations.append({
                        "row_index": int(idx),
                        "column": recommendation.column_name,
                        "issue_type": recommendation.issue_type,
                        "original_value": original_value,
                        "recommended_value": None,
                        "strategy": recommendation.fix_strategy,
                        "confidence": recommendation.confidence_score,
                        "reason": "State column - requires manual review or context-based inference"
                    })
                continue
            
            # For postal code columns: check if it's a valid foreign postal code
            if is_postal_code_column:
                # Get country code from the same row to check if it's a foreign address
                country_code = None
                if "Mailing" in recommendation.column_name:
                    country_col = self._find_column(df, "Provider Business Mailing Address Country Code (If outside U.S.)")
                elif "Practice Location" in recommendation.column_name:
                    country_col = self._find_column(df, "Provider Business Practice Location Address Country Code (If outside U.S.)")
                else:
                    country_col = None
                
                if country_col and country_col in df.columns:
                    country_code = df.at[idx, country_col]
                
                # Check if it's a valid foreign postal code
                is_foreign_postal = self._is_valid_foreign_postal_code(str(original_value), country_code)
                
                if is_foreign_postal:
                    # Valid foreign postal code - add recommendation
                    recommendation_text = "validate foreign postal code"
                    if self.add_recommendation_column and recommendation_col:
                        df.at[idx, recommendation_col] = recommendation_text
                    if row_recommendations is not None:
                        row_recommendations.append({
                            "row_index": int(idx),
                            "column": recommendation.column_name,
                            "issue_type": recommendation.issue_type,
                            "original_value": original_value,
                            "recommended_value": original_value,  # Keep the same value
                            "strategy": recommendation.fix_strategy,
                            "confidence": recommendation.confidence_score,
                            "reason": recommendation_text
                        })
                    continue  # Skip further processing for foreign postal codes
            
            # Try to fix the value (use name_value if we split a degree)
            value_to_fix = name_value if degree_value else original_value
            fixed_value = self._fix_value(value_to_fix, rule, recommendation.column_name)
            
            # For state columns, try context-based inference if standard fix failed
            inference_method = None
            if fixed_value is None and is_state_column:
                fixed_value = self._infer_state_from_row_context(
                    df, idx, original_value, recommendation.column_name, rule
                )
                if fixed_value is not None:
                    inference_method = "state inference from context"
            
            # If we split a degree, the fixed value should be the name part
            if degree_value and fixed_value is None:
                # If the name part is already valid, use it as the fixed value
                if rule.validate_value(name_value):
                    fixed_value = name_value
                    inference_method = "split medical degree/title"
            
            if fixed_value is not None and rule.validate_value(fixed_value):
                # If we split a degree, make sure it's stored
                if degree_value and medical_title_col:
                    if not self.recommendations_only:
                        df.at[idx, medical_title_col] = degree_value
                
                # Apply the fix (only if not in recommendations-only mode)
                if not self.recommendations_only:
                    # Always write to new column if add_fix_columns is True or never_modify_original_columns is True
                    if self.add_fix_columns or self.never_modify_original_columns:
                        df.at[idx, fixed_col] = fixed_value
                        if self.add_fix_flags:
                            reason = inference_method if inference_method else reason_map.get(rule.rule_type, "conformity fix")
                            if degree_value:
                                reason = f"split medical degree/title: {degree_value}"
                            df.at[idx, fix_applied_col] = True
                            df.at[idx, fix_reason_col] = reason
                        if self.add_recommendation_column:
                            rec_text = self._generate_recommendation_text(
                                recommendation, original_value, fixed_value, rule, fixed=True
                            )
                            if degree_value:
                                rec_text = f"{rec_text} (Medical degree/title '{degree_value}' moved to separate column)"
                            df.at[idx, recommendation_col] = rec_text
                elif not self.never_modify_original_columns:
                    # Only modify original column if explicitly allowed
                    df.at[idx, column] = fixed_value
                else:
                    # Recommendations-only mode: only populate recommendation column
                    if self.add_recommendation_column and recommendation_col:
                        reason = inference_method if inference_method else reason_map.get(rule.rule_type, "conformity fix")
                        rec_text = self._generate_recommendation_text(
                            recommendation, original_value, fixed_value, rule, fixed=False
                        )
                        if degree_value:
                            rec_text = f"{rec_text} (Medical degree/title '{degree_value}' should be moved to separate column)"
                        df.at[idx, recommendation_col] = rec_text
                rows_fixed += 1
                
                if len(fix_details) < self.max_sample_details:
                    fix_details.append({
                        "row_index": int(idx),
                        "original": str(original_value)[:50],  # Truncate for display
                        "fixed": str(fixed_value)[:50] + (f" (degree: {degree_value})" if degree_value else "")
                    })
                if row_recommendations is not None:
                    reason_text = inference_method if inference_method else reason_map.get(rule.rule_type, "conformity fix")
                    if degree_value:
                        reason_text = f"split medical degree/title: {degree_value}"
                    row_recommendations.append({
                        "row_index": int(idx),
                        "column": recommendation.column_name,
                        "issue_type": recommendation.issue_type,
                        "original_value": original_value,
                        "recommended_value": fixed_value,
                        "strategy": recommendation.fix_strategy,
                        "confidence": recommendation.confidence_score,
                        "reason": reason_text
                    })
                    # Also add an entry for the medical title column if we split a degree
                    if degree_value and medical_title_col:
                        row_recommendations.append({
                            "row_index": int(idx),
                            "column": medical_title_col,
                            "issue_type": "data_enhancement",
                            "original_value": None,
                            "recommended_value": degree_value,
                            "strategy": "split_from_last_name",
                            "confidence": 1.0,
                            "reason": f"Extracted from Provider Last Name: {original_value}"
                    })
            else:
                rows_failed += 1
                if self.add_recommendation_column and recommendation_col:
                    df.at[idx, recommendation_col] = self._generate_recommendation_text(
                        recommendation, original_value, None, rule, fixed=False
                    )
                if row_recommendations is not None:
                    row_recommendations.append({
                        "row_index": int(idx),
                        "column": recommendation.column_name,
                        "issue_type": recommendation.issue_type,
                        "original_value": original_value,
                        "recommended_value": None,
                        "strategy": recommendation.fix_strategy,
                        "confidence": recommendation.confidence_score,
                        "reason": "Need Review"
                    })
        
        return rows_fixed, rows_failed, fix_details
    
    def _fix_value(
        self,
        value: Any,
        rule: ValidationRule,
        column_name: str
    ) -> Optional[Any]:
        """
        Attempt to fix a single value based on its validation rule.
        
        Returns:
            Fixed value if fixable, None otherwise
        """
        if pd.isna(value):
            return None
        
        str_value = str(value)
        
        # Step 1: Always trim whitespace first
        str_value = str_value.strip()
        
        try:
            if rule.rule_type == "enum":
                return self._fix_enum_value(str_value, rule, column_name)
            
            elif rule.rule_type == "regex":
                return self._fix_regex_value(str_value, rule, column_name)
            
            elif rule.rule_type == "length":
                return self._fix_length_value(str_value, rule)
            
            elif rule.rule_type == "phone":
                return self._fix_phone_value(str_value, rule)
            
            elif rule.rule_type == "date":
                return self._fix_date_value(str_value, rule)
            
            elif rule.rule_type == "numeric":
                return self._fix_numeric_value(str_value)
            
        except Exception as e:
            logger.debug(f"Error fixing value '{str_value}': {e}")
            return None
        
        return None
    
    def _fix_enum_value(self, value: str, rule: ValidationRule, column_name: str) -> Optional[str]:
        """Fix enum values (normalize case, handle type conversions)."""
        allowed_values = rule.rule_config.get("values", [])
        
        # Normalize value
        normalized = value.strip()
        
        # Special handling for Entity Type Code: normalize "1.0" -> "1", "2.0" -> "2"
        if column_name == "Entity Type Code":
            try:
                float_val = float(normalized)
                if float_val.is_integer():
                    normalized = str(int(float_val))
            except (ValueError, TypeError):
                pass
        
        # Special handling for state columns: convert full names to abbreviations
        if "State Name" in column_name and "Address" in column_name:
            abbrev = self._convert_state_to_abbreviation(normalized)
            if abbrev and abbrev in allowed_values:
                return abbrev
        
        # Try exact match
        if normalized in allowed_values:
            return normalized
        
        # Try case-insensitive match
        for allowed in allowed_values:
            if normalized.lower() == allowed.lower():
                return allowed
        
        # Try uppercase/lowercase variants
        if normalized.upper() in allowed_values:
            return normalized.upper()
        if normalized.lower() in allowed_values:
            return normalized.lower()
        
        return None
    
    def _fix_regex_value(self, value: str, rule: ValidationRule, column_name: str) -> Optional[str]:
        """Fix regex pattern violations where possible."""
        pattern = rule.rule_config.get("pattern", "")
        if not pattern:
            return None
        
        fixed = value.strip()
        
        # Common fixes:
        # 1. Remove extra whitespace
        fixed = re.sub(r'\s+', ' ', fixed)

        # 1a. For city name columns, extract state abbreviation if present
        if "City Name" in column_name:
            # Extract state abbreviation from city name (e.g., "WASHINGTON WASHINGTON, DC" -> "DC")
            extracted_state = self._extract_state_from_city_name(fixed)
            if extracted_state:
                # Return just the state abbreviation
                return extracted_state
            # Strip trailing punctuation (commas, periods)
            fixed = fixed.rstrip(' ,.')
        
        # 1b. For state name columns, convert full names to abbreviations
        if "State Name" in column_name and "Address" in column_name:
            abbrev = self._convert_state_to_abbreviation(fixed)
            if abbrev:
                fixed = abbrev
        
        # 2. For NPI (10 digits): remove non-digits, pad/truncate
        if column_name == "NPI" and pattern == r"^\d{10}$":
            digits = re.sub(r'\D', '', fixed)
            if len(digits) == 10:
                return digits
            # Try to fix if close
            if len(digits) > 10:
                return digits[:10]  # Truncate
            if len(digits) < 10 and len(digits) > 0:
                # Pad with zeros (risky, but sometimes acceptable)
                return digits.zfill(10)
        
        # 3. For EIN (9 digits or <UNAVAIL>)
        if column_name == "Employer Identification Number (EIN)":
            if fixed.upper() == "<UNAVAIL>":
                return "<UNAVAIL>"
            digits = re.sub(r'\D', '', fixed)
            if len(digits) == 9:
                return digits
            if len(digits) > 9:
                return digits[:9]
        
        # 4. For postal codes: cleanup format
        if "Postal Code" in column_name:
            # Remove non-digit/hyphen characters
            fixed = re.sub(r'[^\d-]', '', fixed)
            # Try to match pattern
            if re.match(pattern, fixed):
                return fixed
        
        # 5. For taxonomy codes: uppercase alphanumeric
        if "Taxonomy Code" in column_name:
            fixed = re.sub(r'[^A-Za-z0-9]', '', fixed).upper()
            if len(fixed) == 10:
                return fixed
        
        # 6. For license numbers: alphanumeric only
        if "License Number" in column_name:
            fixed = re.sub(r'[^A-Za-z0-9]', '', fixed)
            if 1 <= len(fixed) <= 25:
                return fixed
        
        # 7. Check if fixed value now matches pattern
        if re.match(pattern, fixed):
            return fixed
        
        return None
    
    def _extract_state_from_city_name(self, value: str) -> Optional[str]:
        """
        Extract state abbreviation from city name if present.
        
        If city name contains a comma followed by a valid state abbreviation,
        extract and return just the state abbreviation.
        
        Examples:
        - "WASHINGTON WASHINGTON, DC" -> "DC"
        - "NEW YORK, NY" -> "NY"
        - "LOS ANGELES, CA" -> "CA"
        - "CHICAGO" -> None (no state abbreviation)
        
        Args:
            value: City name value that may contain state abbreviation
            
        Returns:
            State abbreviation if found and valid, None otherwise
        """
        if not value or not value.strip():
            return None
        
        # Get valid state abbreviations from validation rules
        valid_states = set()
        for col_name, rule in self.validation_rules.items():
            if "State Name" in col_name and "Address" in col_name and rule.rule_type == "enum":
                valid_states.update(rule.rule_config.get("values", []))
        
        if not valid_states:
            return None
        
        # Split by comma to check for state abbreviation
        parts = [p.strip() for p in value.split(',')]
        
        if len(parts) >= 2:
            # Check if the last part is a valid state abbreviation
            potential_state = parts[-1].strip().upper()
            if potential_state in valid_states:
                return potential_state
        
        return None
    
    def _is_valid_foreign_postal_code(self, postal_code: str, country_code: Optional[str] = None) -> bool:
        """
        Check if a postal code is a valid foreign postal code format.
        
        Args:
            postal_code: The postal code to validate
            country_code: Optional country code to validate against specific country formats
            
        Returns:
            True if the postal code matches a known foreign format, False otherwise
        """
        if not postal_code:
            return False
        
        postal_str = str(postal_code).strip().upper()
        
        # Canadian postal code format: A1A 1A1 (letter-digit-letter space digit-letter-digit)
        # Also accepts without space: A1A1A1
        canadian_pattern = re.compile(r'^[A-Z]\d[A-Z]\s?\d[A-Z]\d$')
        if canadian_pattern.match(postal_str):
            # If country code is provided, verify it matches
            if country_code:
                country_upper = str(country_code).strip().upper()
                if country_upper == "CA":
                    return True
                # If country is not CA, don't validate as Canadian
                return False
            # No country code provided, but format matches Canadian - likely valid
            return True
        
        # UK postal code format: Various patterns like SW1A 1AA, M1 1AA, etc.
        # Basic pattern: 1-2 letters, 1-2 digits, optional space, 1 digit, 2 letters
        uk_pattern = re.compile(r'^[A-Z]{1,2}\d{1,2}\s?\d[A-Z]{2}$')
        if uk_pattern.match(postal_str):
            if country_code:
                country_upper = str(country_code).strip().upper()
                if country_upper == "GB":
                    return True
                return False
            return True
        
        # Australian postal code format: 4 digits
        # But this could conflict with US ZIP+4, so only validate if country code is AU
        if country_code:
            country_upper = str(country_code).strip().upper()
            if country_upper == "AU" and re.match(r'^\d{4}$', postal_str):
                return True
        
        # German postal code format: 5 digits
        # But this could conflict with US ZIP, so only validate if country code is DE
        if country_code:
            country_upper = str(country_code).strip().upper()
            if country_upper == "DE" and re.match(r'^\d{5}$', postal_str):
                return True
        
        # For other countries, if country code is provided and postal code contains letters,
        # it's likely a foreign postal code (US postal codes are numeric only)
        if country_code:
            country_upper = str(country_code).strip().upper()
            if country_upper != "US" and re.search(r'[A-Z]', postal_str):
                # Has letters and country is not US - likely foreign postal code
                return True
        
        # If no country code provided but postal code contains letters,
        # it's likely a foreign postal code (US postal codes are numeric only)
        # This catches cases like Canadian postal codes even when country code is missing
        if not country_code and re.search(r'[A-Z]', postal_str):
            # Has letters and no country code - likely foreign postal code
            return True
        
        return False
    
    def _convert_state_to_abbreviation(self, value: str) -> Optional[str]:
        """
        Convert full state names to abbreviations (e.g., "New York" -> "NY")
        
        Args:
            value: State full name or abbreviation
            
        Returns:
            State abbreviation if full name found, None otherwise
        """
        # US state full names to abbreviations mapping
        state_name_to_abbrev = {
            "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
            "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
            "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
            "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
            "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
            "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
            "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
            "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
            "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
            "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
            "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
            "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
            "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
            "AMERICAN SAMOA": "AS", "GUAM": "GU", "NORTHERN MARIANA ISLANDS": "MP",
            "PUERTO RICO": "PR", "U.S. VIRGIN ISLANDS": "VI", "US VIRGIN ISLANDS": "VI",
            "VIRGIN ISLANDS": "VI", "FEDERATED STATES OF MICRONESIA": "FM",
            "MARSHALL ISLANDS": "MH", "PALAU": "PW"
        }
        
        value_upper = value.strip().upper()
        
        # Check if it's already an abbreviation (return as-is if valid)
        valid_abbreviations = set(state_name_to_abbrev.values())
        if value_upper in valid_abbreviations:
            return value_upper
        
        # Check if it's a full name (case-insensitive)
        if value_upper in state_name_to_abbrev:
            return state_name_to_abbrev[value_upper]
        
        # Try partial matches for common variations
        # Handle "New York" vs "NewYork" (no space)
        value_no_spaces = value_upper.replace(" ", "")
        if value_no_spaces in state_name_to_abbrev:
            return state_name_to_abbrev[value_no_spaces]
        
        # Try matching against keys (handle variations)
        for full_name, abbrev in state_name_to_abbrev.items():
            # Exact match (already checked above)
            if value_upper == full_name:
                return abbrev
            # Match without spaces
            if value_no_spaces == full_name.replace(" ", ""):
                return abbrev
            # Match with common variations
            if value_upper.replace(".", "") == full_name.replace(".", ""):
                return abbrev
        
        return None
    
    def _fix_length_value(self, value: str, rule: ValidationRule) -> Optional[str]:
        """Fix length violations by truncating if too long."""
        min_len = rule.rule_config.get("min", 0)
        max_len = rule.rule_config.get("max", float('inf'))
        
        fixed = value.strip()
        current_len = len(fixed)
        
        # If too short, can't fix (would require padding with unknown data)
        if current_len < min_len:
            return None
        
        # If too long, truncate
        if current_len > max_len:
            return fixed[:int(max_len)]
        
        return fixed
    
    def _fix_phone_value(self, value: str, rule: ValidationRule) -> Optional[str]:
        """Fix phone number format issues."""
        min_digits = rule.rule_config.get("min_digits", 7)
        max_digits = rule.rule_config.get("max_digits", 20)
        
        # Remove all non-digit characters
        digits = re.sub(r'\D', '', value)
        
        if min_digits <= len(digits) <= max_digits:
            return digits
        
        # If close to valid, try to fix
        if len(digits) > max_digits:
            return digits[:max_digits]
        
        return None
    
    def _fix_date_value(self, value: str, rule: ValidationRule) -> Optional[str]:
        """Fix date format issues."""
        from datetime import datetime
        
        str_value = value.strip()
        
        # Try to parse with multiple formats and convert to standard format
        date_formats = [
            ("%m/%d/%Y", "%m/%d/%Y"),
            ("%Y-%m-%d", "%Y-%m-%d"),
            ("%m-%d-%Y", "%m/%d/%Y"),
            ("%Y/%m/%d", "%m/%d/%Y"),
            ("%d/%m/%Y", "%m/%d/%Y"),  # Common European format
            ("%m.%d.%Y", "%m/%d/%Y"),
            ("%Y.%m.%d", "%m/%d/%Y"),
        ]
        
        for parse_fmt, output_fmt in date_formats:
            try:
                parsed_date = datetime.strptime(str_value, parse_fmt)
                
                # Check not_future constraint
                if rule.rule_config.get("not_future", False):
                    if parsed_date.date() > datetime.now().date():
                        return None  # Can't fix future dates
                
                return parsed_date.strftime(output_fmt)
            except ValueError:
                continue
        
        return None
    
    def _fix_numeric_value(self, value: str) -> Optional[str]:
        """Fix numeric format issues."""
        try:
            # Try to parse as float, then return as string
            float_val = float(value.replace(',', ''))  # Remove thousand separators
            return str(float_val)
        except ValueError:
            return None
    
    def _fix_completeness(
        self,
        df: pd.DataFrame,
        column: str,
        recommendation: FixRecommendation,
        row_recommendations: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[int, int, List[Dict[str, Any]]]:
        """
        Fix completeness issues using mode imputation, default imputation, or row-by-row logic.
        
        Returns:
            Tuple of (rows_fixed, rows_failed, fix_details)
        """
        rows_fixed = 0
        rows_failed = 0
        fix_details = []
        
        # Prepare non-destructive columns if configured
        # Use new columns if add_fix_columns is True OR never_modify_original_columns is True
        use_new_columns = self.add_fix_columns or self.never_modify_original_columns
        fixed_col = f"{column}{self.fix_column_suffix}" if use_new_columns else None
        fix_applied_col = f"{column}__fix_applied" if use_new_columns and self.add_fix_flags else None
        fix_reason_col = f"{column}__fix_reason" if use_new_columns and self.add_fix_flags else None
        recommendation_col = f"{column}__recommendation" if self.add_recommendation_column else None
        if fixed_col and fixed_col not in df.columns:
            df[fixed_col] = pd.Series([pd.NA] * len(df))
        if fix_applied_col and fix_applied_col not in df.columns:
            df[fix_applied_col] = pd.Series([False] * len(df))
        if fix_reason_col and fix_reason_col not in df.columns:
            df[fix_reason_col] = pd.Series([pd.NA] * len(df))
        if recommendation_col and recommendation_col not in df.columns:
            df[recommendation_col] = pd.Series([pd.NA] * len(df))
        
        # Special handling for Entity Type Code - use row-by-row conditional logic
        if recommendation.column_name == "Entity Type Code" and "Row-specific" in str(recommendation.suggested_value):
            # Find related columns
            org_name_col = self._find_column(df, "Provider Organization Name (Legal Business Name)")
            last_name_col = self._find_column(df, "Provider Last Name (Legal Name)")
            first_name_col = self._find_column(df, "Provider First Name")
            
            if not all([org_name_col, last_name_col, first_name_col]):
                return 0, 0, [{"error": "Required columns for Entity Type Code fix not found"}]
            
            # Apply row-by-row logic
            # NOTE: This processes ALL rows in the dataset, not a sample.
            # max_sample_details only limits how many fix examples are stored in the results.
            for idx in df.index:
                entity_type = df.at[idx, column]
                
                # Skip if already has value
                if not is_value_null_for_completeness(entity_type, column):
                    continue
                
                # Check related fields
                org_name = df.at[idx, org_name_col]
                last_name = df.at[idx, last_name_col]
                first_name = df.at[idx, first_name_col]
                
                has_org_name = not is_value_null_for_completeness(org_name, org_name_col)
                has_individual_name = (
                    not is_value_null_for_completeness(last_name, last_name_col) or
                    not is_value_null_for_completeness(first_name, first_name_col)
                )
                
                # Determine entity type
                if has_org_name:
                    # Apply the fix (only if not in recommendations-only mode)
                    if not self.recommendations_only:
                        # Always write to new column if add_fix_columns is True or never_modify_original_columns is True
                        if self.add_fix_columns or self.never_modify_original_columns:
                            df.at[idx, fixed_col] = "2"
                            if self.add_fix_flags:
                                df.at[idx, fix_applied_col] = True
                                df.at[idx, fix_reason_col] = "Organization Name present"
                            if self.add_recommendation_column and recommendation_col:
                                df.at[idx, recommendation_col] = self._generate_recommendation_text(
                                    recommendation, None, "2", None, fixed=True
                                )
                        elif not self.never_modify_original_columns:
                            # Only modify original column if explicitly allowed
                            df.at[idx, column] = "2"  # Organization
                    else:
                        # Recommendations-only mode: only populate recommendation column
                        if self.add_recommendation_column and recommendation_col:
                            df.at[idx, recommendation_col] = self._generate_recommendation_text(
                                recommendation, None, "2", None, fixed=False
                            )
                    rows_fixed += 1
                    
                    if len(fix_details) < self.max_sample_details:
                        fix_details.append({
                            "row_index": int(idx),
                            "original": "NULL",
                            "fixed": "2",
                            "reason": "Organization Name present"
                        })
                    if row_recommendations is not None:
                        row_recommendations.append({
                            "row_index": int(idx),
                            "column": recommendation.column_name,
                            "issue_type": recommendation.issue_type,
                            "original_value": None,
                            "recommended_value": "2",
                            "strategy": recommendation.fix_strategy,
                            "confidence": recommendation.confidence_score,
                            "reason": "Organization Name present"
                        })
                elif has_individual_name:
                    # Apply the fix (only if not in recommendations-only mode)
                    if not self.recommendations_only:
                        # Always write to new column if add_fix_columns is True or never_modify_original_columns is True
                        if self.add_fix_columns or self.never_modify_original_columns:
                            df.at[idx, fixed_col] = "1"
                            if self.add_fix_flags:
                                df.at[idx, fix_applied_col] = True
                                df.at[idx, fix_reason_col] = "Individual Name present"
                            if self.add_recommendation_column and recommendation_col:
                                df.at[idx, recommendation_col] = self._generate_recommendation_text(
                                    recommendation, None, "1", None, fixed=True
                                )
                        elif not self.never_modify_original_columns:
                            # Only modify original column if explicitly allowed
                            df.at[idx, column] = "1"  # Individual
                    else:
                        # Recommendations-only mode: only populate recommendation column
                        if self.add_recommendation_column and recommendation_col:
                            df.at[idx, recommendation_col] = self._generate_recommendation_text(
                                recommendation, None, "1", None, fixed=False
                            )
                    rows_fixed += 1
                    
                    if len(fix_details) < self.max_sample_details:
                        fix_details.append({
                            "row_index": int(idx),
                            "original": "NULL",
                            "fixed": "1",
                            "reason": "Individual Name present"
                        })
                    if row_recommendations is not None:
                        row_recommendations.append({
                            "row_index": int(idx),
                            "column": recommendation.column_name,
                            "issue_type": recommendation.issue_type,
                            "original_value": None,
                            "recommended_value": "1",
                            "strategy": recommendation.fix_strategy,
                            "confidence": recommendation.confidence_score,
                            "reason": "Individual Name present"
                        })
                else:
                    rows_failed += 1
                    if self.add_recommendation_column and recommendation_col:
                        df.at[idx, recommendation_col] = self._generate_recommendation_text(
                            recommendation, None, None, None, fixed=False
                        )
                    # Don't add to row_recommendations if we can't fix it - only include fixable completeness issues
            
            return rows_fixed, rows_failed, fix_details
        
        # Special handling for conditional name fields (Provider Last Name, First Name, Organization Name)
        is_name_field = recommendation.column_name in [
            "Provider Last Name (Legal Name)",
            "Provider First Name",
            "Provider Organization Name (Legal Business Name)"
        ]
        if is_name_field and recommendation.fix_strategy == "conditional_imputation":
            # Name fields are conditionally required based on Entity Type Code
            # We cannot auto-fill these as they require actual name data
            # However, we can identify which rows need these fields based on Entity Type Code
            entity_type_col = self._find_column(df, "Entity Type Code")
            
            if entity_type_col:
                rows_needing_review = 0
                for idx in df.index:
                    name_value = df.at[idx, column]
                    
                    # Skip if already has value
                    if not is_value_null_for_completeness(name_value, column):
                        continue
                    
                    # Check Entity Type Code to see if this name field is required
                    entity_type = df.at[idx, entity_type_col]
                    
                    # Determine if this name field is required for this entity type
                    is_required = False
                    if entity_type == 1 or str(entity_type) == "1":  # Individual
                        is_required = recommendation.column_name in [
                            "Provider Last Name (Legal Name)",
                            "Provider First Name"
                        ]
                    elif entity_type == 2 or str(entity_type) == "2":  # Organization
                        is_required = recommendation.column_name == "Provider Organization Name (Legal Business Name)"
                    
                    if is_required:
                        rows_needing_review += 1
                        # Add recommendation column text even though it can't be auto-filled
                        if self.add_recommendation_column and recommendation_col:
                            df.at[idx, recommendation_col] = self._generate_recommendation_text(
                                recommendation, None, None, None, fixed=False
                            )
                        # Do NOT add to row_recommendations - name fields cannot be auto-filled
                        # They will appear in the summary but not in the CSV (only fixable issues go to CSV)
                
                # Return without fixing (name fields cannot be auto-filled)
                # Do not add to row_recommendations since they're not fixable
                return 0, rows_needing_review, [{"info": f"{rows_needing_review} rows need {recommendation.column_name} but cannot be auto-filled - requires manual input"}]
            else:
                # Entity Type Code column not found - cannot determine which rows need this field
                return 0, 0, [{"info": f"Entity Type Code column not found - cannot determine conditional requirement for {recommendation.column_name}"}]
        
        # Special handling for City/State columns - infer from context (address, postal, country)
        is_city_column = "City Name" in recommendation.column_name and "Address" in recommendation.column_name
        is_state_column = "State Name" in recommendation.column_name and "Address" in recommendation.column_name
        
        if (is_city_column or is_state_column) and recommendation.fix_strategy == "context_imputation":
            # Find related context columns
            address1_col = None
            address2_col = None
            postal_col = None
            country_col = None
            city_col = None
            state_col = None
            
            # Determine which address type (Mailing vs Practice Location)
            if "Mailing" in recommendation.column_name:
                address1_col = self._find_column(df, "Provider First Line Business Mailing Address")
                address2_col = self._find_column(df, "Provider Second Line Business Mailing Address")
                postal_col = self._find_column(df, "Provider Business Mailing Address Postal Code")
                country_col = self._find_column(df, "Provider Business Mailing Address Country Code (If outside U.S.)")
                if is_city_column:
                    state_col = self._find_column(df, "Provider Business Mailing Address State Name")
                else:
                    city_col = self._find_column(df, "Provider Business Mailing Address City Name")
            elif "Practice Location" in recommendation.column_name:
                address1_col = self._find_column(df, "Provider First Line Business Practice Location Address")
                address2_col = self._find_column(df, "Provider Second Line Business Practice Location Address")
                postal_col = self._find_column(df, "Provider Business Practice Location Address Postal Code")
                country_col = self._find_column(df, "Provider Business Practice Location Address Country Code (If outside U.S.)")
                if is_city_column:
                    state_col = self._find_column(df, "Provider Business Practice Location Address State Name")
                else:
                    city_col = self._find_column(df, "Provider Business Practice Location Address City Name")
            
            # Apply row-by-row context-based inference
            for idx in df.index:
                current_value = df.at[idx, column]
                
                # Skip if already has value
                if not is_value_null_for_completeness(current_value, column):
                    continue
                
                # Extract context from row
                address_line1 = df.at[idx, address1_col] if address1_col else None
                address_line2 = df.at[idx, address2_col] if address2_col else None
                postal_code = df.at[idx, postal_col] if postal_col else None
                country_code = df.at[idx, country_col] if country_col else None
                city_name = df.at[idx, city_col] if city_col else None
                state_name = df.at[idx, state_col] if state_col else None
                
                # Try to infer missing value
                inferred_value = None
                inference_method = None
                
                if is_state_column:
                    # Infer state from context
                    # Get validation rule for state column
                    rule = None
                    if hasattr(self, 'validation_rules') and self.validation_rules:
                        rule = self.validation_rules.get(recommendation.column_name)
                    inferred_value = self._infer_state_code(
                        invalid_state="",  # Empty since we're inferring missing value
                        city_name=city_name,
                        postal_code=postal_code,
                        country_code=country_code,
                        address_line1=address_line1,
                        address_line2=address_line2,
                        rule=rule
                    )
                    if inferred_value:
                        inference_method = "state inference from context"
                elif is_city_column:
                    # Infer city from context
                    inferred_value = self._infer_city_name(
                        state_name=state_name,
                        postal_code=postal_code,
                        address_line1=address_line1,
                        address_line2=address_line2,
                        country_code=country_code
                    )
                    if inferred_value:
                        inference_method = "city inference from context"
                
                if inferred_value:
                    # Apply the fix (only if not in recommendations-only mode)
                    if not self.recommendations_only:
                        # Always write to new column if add_fix_columns is True or never_modify_original_columns is True
                        if self.add_fix_columns or self.never_modify_original_columns:
                            df.at[idx, fixed_col] = inferred_value
                            if self.add_fix_flags:
                                df.at[idx, fix_applied_col] = True
                                df.at[idx, fix_reason_col] = inference_method
                            if self.add_recommendation_column and recommendation_col:
                                df.at[idx, recommendation_col] = self._generate_recommendation_text(
                                    recommendation, None, inferred_value, None, fixed=True
                                )
                        elif not self.never_modify_original_columns:
                            # Only modify original column if explicitly allowed
                            df.at[idx, column] = inferred_value
                    else:
                        # Recommendations-only mode: only populate recommendation column
                        if self.add_recommendation_column and recommendation_col:
                            df.at[idx, recommendation_col] = self._generate_recommendation_text(
                                recommendation, None, inferred_value, None, fixed=False
                            )
                    
                    rows_fixed += 1
                    
                    if len(fix_details) < self.max_sample_details:
                        fix_details.append({
                            "row_index": int(idx),
                            "original": "NULL",
                            "fixed": inferred_value,
                            "reason": inference_method
                        })
                    if row_recommendations is not None:
                        row_recommendations.append({
                            "row_index": int(idx),
                            "column": recommendation.column_name,
                            "issue_type": recommendation.issue_type,
                            "original_value": None,
                            "recommended_value": inferred_value,
                            "strategy": recommendation.fix_strategy,
                            "confidence": recommendation.confidence_score,
                            "reason": inference_method
                        })
                else:
                    rows_failed += 1
                    if self.add_recommendation_column and recommendation_col:
                        df.at[idx, recommendation_col] = self._generate_recommendation_text(
                            recommendation, None, None, None, fixed=False
                        )
                    # Add to row_recommendations even when inference fails - for visibility and manual review
                    if row_recommendations is not None:
                        row_recommendations.append({
                            "row_index": int(idx),
                            "column": recommendation.column_name,
                            "issue_type": recommendation.issue_type,
                            "original_value": None,
                            "recommended_value": None,
                            "strategy": recommendation.fix_strategy,
                            "confidence": recommendation.confidence_score,
                            "reason": "Inference failed - insufficient context or not found in reference files"
                        })
            
            return rows_fixed, rows_failed, fix_details
        
        # General completeness fix: use suggested_value from recommendation
        # NOTE: Default imputation is disabled - if no suggested_value, treat as no recommendation
        suggested_value = recommendation.suggested_value
        if suggested_value is None:
            # No recommendation available - don't add to CSV, don't populate recommendation column
            # Just return without processing
            return 0, 0, [{"info": f"No recommendation available for {recommendation.column_name} - no suggested value"}]
        
        strategy_reason = {
            "default_imputation": "Default imputation (standard value)",
            "mode_imputation": "Mode imputation (most common value)",
            "context_imputation": "Context-based inference",
            "conditional_imputation": "Conditional imputation"
        }
        reason = strategy_reason.get(recommendation.fix_strategy, recommendation.fix_strategy)
        
        # Apply imputation to all null values
        for idx in df.index:
            value = df.at[idx, column]
            
            # Skip if already has value
            if not is_value_null_for_completeness(value, recommendation.column_name):
                continue
            
            # Apply the suggested value (only if not in recommendations-only mode)
            if not self.recommendations_only:
                # Always write to new column if add_fix_columns is True or never_modify_original_columns is True
                if self.add_fix_columns or self.never_modify_original_columns:
                    df.at[idx, fixed_col] = suggested_value
                    if self.add_fix_flags:
                        df.at[idx, fix_applied_col] = True
                        df.at[idx, fix_reason_col] = reason
                    if self.add_recommendation_column and recommendation_col:
                        df.at[idx, recommendation_col] = self._generate_recommendation_text(
                            recommendation, None, suggested_value, None, fixed=True
                        )
                elif not self.never_modify_original_columns:
                    # Only modify original column if explicitly allowed
                    df.at[idx, column] = suggested_value
            else:
                # Recommendations-only mode: only populate recommendation column
                if self.add_recommendation_column and recommendation_col:
                    df.at[idx, recommendation_col] = self._generate_recommendation_text(
                        recommendation, None, suggested_value, None, fixed=False
                    )
            
            rows_fixed += 1
            
            if len(fix_details) < self.max_sample_details:
                fix_details.append({
                    "row_index": int(idx),
                    "original": "NULL",
                    "fixed": str(suggested_value),
                    "reason": reason
                })
            
            if row_recommendations is not None:
                row_recommendations.append({
                    "row_index": int(idx),
                    "column": recommendation.column_name,
                    "issue_type": recommendation.issue_type,
                    "original_value": None,
                    "recommended_value": suggested_value,
                    "strategy": recommendation.fix_strategy,
                    "confidence": recommendation.confidence_score,
                    "reason": reason
                })
        
        return rows_fixed, rows_failed, fix_details

    def _add_need_review_suggestions(
        self,
        df: pd.DataFrame,
        recommendation: FixRecommendation,
        row_recommendations: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Add sidecar entries that mark violating rows as Need Review for non-actionable issues."""
        if row_recommendations is None:
            return
        # Respect allowed types (e.g., exclude completeness entirely when only conformity is enabled)
        if recommendation.issue_type not in self.allowed_fix_types:
            return
        column_name = recommendation.column_name
        actual_column = self._find_column(df, column_name)
        if not actual_column:
            return
        rule = self.validation_rules.get(column_name)
        # Conformity: mark values that do not validate
        if recommendation.issue_type == "conformity" and rule is not None:
            for idx in df.index:
                value = df.at[idx, actual_column]
                if is_value_null_for_completeness(value, column_name):
                    continue
                if not rule.validate_value(value):
                    row_recommendations.append({
                        "row_index": int(idx),
                        "column": column_name,
                        "issue_type": recommendation.issue_type,
                        "original_value": value,
                        "recommended_value": None,
                        "strategy": recommendation.fix_strategy,
                        "confidence": recommendation.confidence_score,
                        "reason": "Need Review"
                    })
        # Other types: we cannot evaluate row-by-row reliably
        else:
            return
    
    def _find_column(self, df: pd.DataFrame, column_name: str) -> Optional[str]:
        """Find column in dataframe (case-insensitive match)."""
        if column_name in df.columns:
            return column_name
        
        # Try case-insensitive match
        for col in df.columns:
            if col.lower() == column_name.lower():
                return col
        
        return None
    
    def _infer_state_from_row_context(
        self,
        df: pd.DataFrame,
        row_idx: int,
        invalid_state: Any,
        state_column_name: str,
        rule: ValidationRule
    ) -> Optional[str]:
        """
        Infer correct state code from row context (city, postal code, address, country).
        
        Args:
            df: DataFrame containing the row
            row_idx: Index of the row
            invalid_state: Current invalid state value
            state_column_name: Name of the state column
            rule: Validation rule for state
            
        Returns:
            Valid USPS state code if inference successful, None otherwise
        """
        # Determine address type (Mailing or Practice Location)
        is_practice_location = "Practice Location" in state_column_name
        address_prefix = "Provider Business Practice Location Address" if is_practice_location else "Provider Business Mailing Address"
        
        # Get related columns
        city_col = self._find_column(df, f"{address_prefix} City Name")
        postal_col = self._find_column(df, f"{address_prefix} Postal Code")
        country_col = self._find_column(df, f"{address_prefix} Country Code (If outside U.S.)")
        
        # Address line columns
        if is_practice_location:
            addr1_col = self._find_column(df, "Provider First Line Business Practice Location Address")
            addr2_col = self._find_column(df, "Provider Second Line Business Practice Location Address")
        else:
            addr1_col = self._find_column(df, "Provider First Line Business Mailing Address")
            addr2_col = self._find_column(df, "Provider Second Line Business Mailing Address")
        
        # Extract values from row
        city_name = df.at[row_idx, city_col] if city_col else None
        postal_code = df.at[row_idx, postal_col] if postal_col else None
        country_code = df.at[row_idx, country_col] if country_col else None
        address_line1 = df.at[row_idx, addr1_col] if addr1_col else None
        address_line2 = df.at[row_idx, addr2_col] if addr2_col else None
        
        # Use state inference logic
        return self._infer_state_code(
            invalid_state, city_name, postal_code, country_code, address_line1, address_line2, rule
        )
    
    def _load_city_state_mapping(self) -> Dict[str, str]:
        """
        Load city name to state mapping from CSV file.
        
        Returns:
            Dictionary mapping city names (normalized, uppercase) to state codes
        """
        if self._city_state_map is not None:
            return self._city_state_map
        
        self._city_state_map = {}
        mapping_path = Path(self.city_state_mapping_file)
        
        if not mapping_path.exists():
            logger.debug(f"City state mapping file not found: {mapping_path}. Skipping city-based inference.")
            return {}
        
        try:
            import pandas as pd
            df = pd.read_csv(mapping_path)
            
            # Try common column name variations
            city_col = None
            state_col = None
            
            for col in df.columns:
                col_lower = col.lower()
                if 'city' in col_lower or 'name' in col_lower:
                    city_col = col
                if 'state' in col_lower:
                    state_col = col
            
            if not city_col or not state_col:
                logger.warning(f"Could not find city and state columns in {mapping_path}")
                return {}
            
            # Build mapping: normalize city names (uppercase, strip)
            for _, row in df.iterrows():
                city = str(row[city_col]).strip().upper()
                state = str(row[state_col]).strip().upper()
                
                if city and state:
                    self._city_state_map[city] = state
            
            logger.info(f"Loaded {len(self._city_state_map)} city to state mappings from {mapping_path}")
            
        except Exception as e:
            logger.error(f"Failed to load city state mapping: {e}")
            self._city_state_map = {}
        
        return self._city_state_map
    
    def _load_postal_state_mapping(self) -> Dict[str, str]:
        """
        Load postal code to state mapping from CSV file.
        
        Returns:
            Dictionary mapping postal codes (as strings) to state codes
        """
        if self._postal_state_map is not None:
            return self._postal_state_map
        
        self._postal_state_map = {}
        mapping_path = Path(self.postal_state_mapping_file)
        
        if not mapping_path.exists():
            logger.warning(f"Postal state mapping file not found: {mapping_path}. Using limited ZIP3 mapping.")
            return {}
        
        try:
            import pandas as pd
            df = pd.read_csv(mapping_path)
            
            # Try common column name variations
            postal_col = None
            state_col = None
            
            for col in df.columns:
                col_lower = col.lower()
                if 'postal' in col_lower or 'zip' in col_lower or 'code' in col_lower:
                    postal_col = col
                if 'state' in col_lower:
                    state_col = col
            
            if not postal_col or not state_col:
                logger.warning(f"Could not find postal_code and state_code columns in {mapping_path}")
                return {}
            
            # Build mapping: normalize postal codes using the same normalization function
            for _, row in df.iterrows():
                postal_raw = row[postal_col]
                state = str(row[state_col]).strip().upper()
                
                if not state:
                    continue
                
                # Normalize postal code using the same method as lookup
                postal_normalized = self._normalize_postal_code(postal_raw)
                
                if postal_normalized:
                    # Map normalized postal code (5-digit standard)
                    self._postal_state_map[postal_normalized] = state
                    
                    # Also map first 3 digits (ZIP3) for fallback lookup
                    if len(postal_normalized) >= 3:
                        zip3 = postal_normalized[:3]
                        self._postal_state_map[zip3] = state
            
            logger.info(f"Loaded {len(self._postal_state_map)} postal code to state mappings from {mapping_path}")
            
        except Exception as e:
            logger.error(f"Failed to load postal state mapping: {e}")
            self._postal_state_map = {}
        
        return self._postal_state_map
    
    def _load_state_country_mapping(self) -> Dict[Tuple[str, str], bool]:
        """
        Load state/province to country mapping from CSV file.
        
        Returns:
            Dictionary mapping (state_name_upper, country_code_upper) tuples to True
        """
        if self._state_country_map is not None:
            return self._state_country_map
        
        self._state_country_map = {}
        mapping_path = Path(self.state_country_mapping_file)
        
        if not mapping_path.exists():
            logger.warning(f"State-country mapping file not found: {mapping_path}. Foreign state validation will be limited.")
            return {}
        
        try:
            import pandas as pd
            df = pd.read_csv(mapping_path)
            
            # Try common column name variations
            state_col = None
            country_col = None
            
            for col in df.columns:
                col_lower = col.lower()
                if 'state' in col_lower or 'province' in col_lower:
                    state_col = col
                if 'country' in col_lower:
                    country_col = col
            
            if not state_col or not country_col:
                logger.warning(f"Could not find state and country columns in {mapping_path}")
                return {}
            
            # Build mapping: normalize state names and country codes (uppercase)
            for _, row in df.iterrows():
                state = str(row[state_col]).strip().upper()
                country = str(row[country_col]).strip().upper()
                
                if state and country:
                    self._state_country_map[(state, country)] = True
            
            logger.info(f"Loaded {len(self._state_country_map)} state-country mappings from {mapping_path}")
            
        except Exception as e:
            logger.error(f"Failed to load state-country mapping: {e}")
            self._state_country_map = {}
        
        return self._state_country_map
    
    def _is_valid_foreign_state(self, state_value: str, country_code: Optional[str] = None) -> bool:
        """
        Check if a state value is a valid foreign state/province for the given country.
        
        Args:
            state_value: The state/province value to check
            country_code: Country code to validate against
            
        Returns:
            True if the state is valid for the country, False otherwise
        """
        if not state_value or pd.isna(state_value):
            return False
        
        state_str = str(state_value).strip()
        if not state_str:
            return False
        
        if not country_code:
            return False
        
        country_code_str = str(country_code).strip().upper()
        if not country_code_str or country_code_str == "US":
            return False
        
        # Load state-country mapping
        state_country_map = self._load_state_country_mapping()
        
        # Check if this state exists for this country
        state_upper = state_str.upper()
        return (state_upper, country_code_str) in state_country_map
    
    def _infer_state_code(
        self,
        invalid_state: str,
        city_name: Optional[str] = None,
        postal_code: Optional[str] = None,
        country_code: Optional[str] = None,
        address_line1: Optional[str] = None,
        address_line2: Optional[str] = None,
        rule: Optional[ValidationRule] = None
    ) -> Optional[str]:
        """
        Infer correct USPS state code from context.
        Uses multi-strategy approach with confidence scoring.
        
        Priority order:
        1. Full state name mapping (most reliable)
        2. City name lookup (if mapping file available)
        3. Address line parsing
        4. Postal code lookup (last resort due to format inconsistencies)
        
        Returns:
            Valid USPS state code if inference successful, None otherwise
        """
        # Strategy 1: Direct full state name mapping (HIGHEST PRIORITY - most reliable)
        state_name_to_code = {
            "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
            "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
            "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
            "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
            "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
            "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
            "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
            "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
            "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
            "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
            "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
            "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
            "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
            "PUERTO RICO": "PR", "GUAM": "GU", "VIRGIN ISLANDS": "VI",
            "AMERICAN SAMOA": "AS", "NORTHERN MARIANA ISLANDS": "MP",
            "FEDERATED STATES OF MICRONESIA": "FM", "MARSHALL ISLANDS": "MH", "PALAU": "PW"
        }
        
        invalid_state_upper = str(invalid_state).strip().upper()
        valid_states = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", 
                       "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", 
                       "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", 
                       "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", 
                       "WI", "WY", "DC", "AS", "GU", "MP", "PR", "VI", "FM", "MH", "PW"]
        
        # Military codes are valid USPS codes - do NOT try to fix them
        military_codes = ["AE", "AP", "AA"]
        if invalid_state_upper in military_codes:
            # Return None to indicate no fix needed (these are already correct)
            return None
        
        if invalid_state_upper in state_name_to_code:
            return state_name_to_code[invalid_state_upper]
        
        # Strategy 2: Check if already valid (case-insensitive)
        if invalid_state_upper in [s.upper() for s in valid_states]:
            return invalid_state_upper
        
        # Strategy 3: City name lookup (RECOMMENDED - more reliable than postal codes)
        if city_name:
            city_map = self._load_city_state_mapping()
            if city_map:
                city_normalized = str(city_name).strip().upper()
                if city_normalized in city_map:
                    return city_map[city_normalized]
        
        # Strategy 4: Check address lines for state names
        address_text = ""
        if address_line1:
            address_text += " " + str(address_line1).upper()
        if address_line2:
            address_text += " " + str(address_line2).upper()
        
        for state_name, state_code in state_name_to_code.items():
            if state_name in address_text:
                return state_code
        
        # Strategy 5: Postal code lookup (LAST RESORT - use only if other methods fail)
        # Note: Postal codes have format inconsistencies, so this is less reliable
        if postal_code:
            postal_map = self._load_postal_state_mapping()
            if postal_map:
                # Comprehensive postal code normalization
                zip_str = self._normalize_postal_code(postal_code)
                
                if not zip_str:
                    return None
                
                # Try full normalized postal code first
                if zip_str in postal_map:
                    return postal_map[zip_str]
                
                # Try first 5 digits (standard ZIP) - most reliable postal format
                if len(zip_str) >= 5 and zip_str[:5].isdigit():
                    zip5 = zip_str[:5]
                    if zip5 in postal_map:
                        return postal_map[zip5]
                
                # Try first 3 digits (ZIP3) - less reliable but sometimes works
                if len(zip_str) >= 3 and zip_str[:3].isdigit():
                    zip3 = zip_str[:3]
                    if zip3 in postal_map:
                        return postal_map[zip3]
        
        # Strategy 6: Fallback to limited ZIP3 mapping if postal mapping file not available
        if postal_code and not self._postal_state_map:
            zip_str = self._normalize_postal_code(postal_code)
            if zip_str and len(zip_str) >= 3:
                zip3 = zip_str[:3]
                # Limited ZIP3 to state mappings (fallback only)
                zip3_to_state = {
                    "100": "NY", "200": "DC", "300": "GA", "600": "IL", "700": "LA",
                    "750": "TX", "800": "CO", "900": "CA", "021": "MA", "303": "GA",
                    "331": "FL", "606": "IL", "770": "TX", "850": "AZ", "902": "CA",
                    "917": "CA", "918": "CA", "919": "CA", "920": "CA", "921": "CA",
                    "922": "CA", "923": "CA", "924": "CA", "925": "CA", "926": "CA",
                    "927": "CA", "928": "CA", "930": "CA", "931": "CA", "932": "CA",
                    "933": "CA", "934": "CA", "935": "CA", "936": "CA", "937": "CA",
                    "938": "CA", "939": "CA", "940": "CA", "941": "CA", "942": "CA",
                    "943": "CA", "944": "CA", "945": "CA", "946": "CA", "947": "CA",
                    "948": "CA", "949": "CA", "950": "CA", "951": "CA", "952": "CA",
                    "953": "CA", "954": "CA", "955": "CA", "956": "CA", "957": "CA",
                    "958": "CA", "959": "CA", "960": "CA", "961": "CA", "962": "CA",
                    "963": "CA", "964": "CA", "965": "CA", "966": "CA", "967": "CA",
                    "968": "HI", "969": "GU"
                }
                if zip3 in zip3_to_state:
                    return zip3_to_state[zip3]
        
        # Strategy 6: Special cases for military/overseas codes (already handled in Strategy 2.5)
        # This is a fallback if somehow we get here
        if invalid_state_upper in ["APO AE", "APO-AE", "FPO AE"]:
            return "AE"
        if invalid_state_upper in ["APO AP", "APO-AP", "FPO AP"]:
            return "AP"
        if invalid_state_upper in ["APO AA", "APO-AA", "FPO AA"]:
            return "AA"
        
        return None
    
    def _normalize_postal_code(self, postal_code: Any) -> Optional[str]:
        """
        Normalize postal code to standard format for lookup.
        
        Handles various formats:
        - 917.0 -> 00917 (remove .0 suffix, pad to 5 digits)
        - 9180 -> 09180 (pad to 5 digits)
        - 91803 -> 91803 (keep as-is, perfect 5-digit ZIP)
        - 91803-1100 -> 91803 (ZIP+4 format, use first 5 digits)
        - 91803100 -> 91803100 (8-digit codes: keep full code for military/overseas)
        - 963501200 -> 96350 (9+ digits: extract first 5 digits)
        
        Note: 6-8 digit codes are kept as-is because they may be:
        - Military postal codes (APO/FPO) that need full code
        - International postal codes
        - Special format codes
        
        Returns:
            Normalized postal code string, or None if invalid
        """
        if postal_code is None:
            return None
        
        # Convert to string and normalize
        zip_str = str(postal_code).strip()
        
        # Remove common suffixes and formatting
        zip_str = zip_str.replace(".0", "")  # Remove .0 suffix
        zip_str = zip_str.replace(".", "")    # Remove other dots
        zip_str = zip_str.replace("-", "")    # Remove hyphens
        zip_str = zip_str.replace(" ", "")    # Remove spaces
        zip_str = zip_str.replace("_", "")    # Remove underscores
        
        # Extract only digits
        digits_only = re.sub(r'\D', '', zip_str)
        
        if not digits_only:
            return None
        
        # Handle different length cases
        if len(digits_only) == 0:
            return None
        elif len(digits_only) <= 4:
            # Short codes (3-4 digits): pad to 5 with leading zeros
            # e.g., 917 -> 00917, 7922 -> 07922
            return digits_only.zfill(5)
        elif len(digits_only) == 5:
            # Perfect 5-digit ZIP
            return digits_only
        elif len(digits_only) >= 6 and len(digits_only) <= 8:
            # 6-8 digit codes: keep full code (may be military/overseas codes)
            # e.g., 91803100 (military), 96350120 (overseas)
            # The lookup will try full code first, then fall back to first 5, then first 3
            return digits_only
        elif len(digits_only) == 9:
            # ZIP+4 format: use first 5 digits (standard US format)
            return digits_only[:5]
        elif len(digits_only) > 9:
            # Very long codes (>9 digits): take first 5 digits (most reliable)
            return digits_only[:5]
        else:
            # Should not reach here, but fallback to first 5
            return digits_only[:5]
    
    def _infer_city_name(
        self,
        state_name: Optional[str] = None,
        postal_code: Optional[str] = None,
        address_line1: Optional[str] = None,
        address_line2: Optional[str] = None,
        country_code: Optional[str] = None
    ) -> Optional[str]:
        """
        Infer city name from context (state, postal code, address lines).
        
        Strategies (in priority order):
        1. Extract from address lines (look for city-like patterns)
        2. Validate extracted city against city_state_mapping if state available
        
        Returns:
            Inferred city name (normalized, uppercase) if successful, None otherwise
        """
        # Strategy 1: Extract city from address lines
        # Look for capitalized words/phrases that could be city names
        address_text = ""
        if address_line1:
            address_text += " " + str(address_line1)
        if address_line2:
            address_text += " " + str(address_line2)
        
        address_text = address_text.strip()
        if not address_text:
            return None
        
        # Common address patterns: "123 MAIN ST, CITYNAME, STATE" or "CITYNAME, STATE"
        # Try to extract city-like words (2+ words, capitalized, not numbers)
        import re
        
        # Remove common address prefixes/suffixes
        address_clean = re.sub(r'\b(STREET|ST|AVENUE|AVE|ROAD|RD|BOULEVARD|BLVD|DRIVE|DR|LANE|LN|CIRCLE|CIR|COURT|CT|WAY|PLACE|PL)\b', '', address_text, flags=re.IGNORECASE)
        address_clean = re.sub(r'\b(SUITE|STE|UNIT|APT|APARTMENT|FLOOR|FL|BUILDING|BLDG|BLD)\b.*', '', address_clean, flags=re.IGNORECASE)
        
        # Split by common delimiters (comma, newline, etc.)
        parts = re.split(r'[,;\n]', address_clean)
        
        # Look for city-like patterns in each part
        # City names are typically: 1-3 words, alphabetic, capitalized
        city_candidates = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # Check if this part looks like a city name
            # City names: alphabetic, 1-40 chars, may have spaces/hyphens/apostrophes
            if re.match(r'^[A-Za-z][A-Za-z\s\-\'\.]{0,39}$', part):
                words = part.split()
                # City names are typically 1-3 words
                if 1 <= len(words) <= 3:
                    # Check if words are capitalized (city-like)
                    if all(word[0].isupper() if word else False for word in words):
                        city_candidates.append(part.upper())
        
        # Strategy 2: If we have state, validate candidates against city_state_mapping
        if state_name and city_candidates:
            city_map = self._load_city_state_mapping()
            if city_map:
                state_upper = str(state_name).strip().upper()
                # Check each candidate against the mapping
                for candidate in city_candidates:
                    candidate_normalized = candidate.strip().upper()
                    # Check if this city exists in mapping and matches the state
                    if candidate_normalized in city_map:
                        if city_map[candidate_normalized] == state_upper:
                            return candidate_normalized
        
        # Strategy 3: If no state validation, return first reasonable candidate
        # (less reliable, but better than nothing)
        if city_candidates:
            # Prefer longer city names (more specific)
            city_candidates.sort(key=len, reverse=True)
            return city_candidates[0].strip().upper()
        
        return None
    
    def _generate_recommendation_text(
        self,
        recommendation: FixRecommendation,
        original_value: Any,
        fixed_value: Optional[Any],
        rule: Optional[ValidationRule],
        fixed: bool
    ) -> str:
        """
        Generate human-readable recommendation text for industry use.
        
        Args:
            recommendation: The fix recommendation
            original_value: Original value that had the issue
            fixed_value: Fixed value (if fixable) or None
            rule: Validation rule that was violated (None for completeness issues without rules)
            fixed: Whether the value was successfully fixed
            
        Returns:
            Human-readable recommendation text
        """
        column_name = recommendation.column_name
        rule_type = rule.rule_type if rule else None
        
        # Handle completeness issues first (they don't have validation rules)
        if recommendation.issue_type == "completeness":
            if fixed and fixed_value is not None:
                strategy_text = {
                    "default_imputation": "Default imputation",
                    "context_imputation": "Context-based inference"
                }
                strategy_desc = strategy_text.get(recommendation.fix_strategy, recommendation.fix_strategy)
                return f"Missing value imputed: NULL → '{fixed_value}' ({strategy_desc}). Confidence: {recommendation.confidence_score:.0%}. Action: Auto-fix applied."
            else:
                return f"Missing value detected. Recommendation: Review and fill manually. Issue: Completeness violation. Impact: {recommendation.estimated_impact}. Requires: Manual intervention."
        
        if fixed and fixed_value is not None:
            # Successfully fixed
            if rule_type == "enum":
                # Check if this is a state column that was inferred
                is_state_col = "State" in recommendation.column_name and "Address" in recommendation.column_name
                if is_state_col and str(original_value).upper() not in ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "AS", "GU", "MP", "PR", "VI", "FM", "MH", "PW"]:
                    return f"State inferred from context: '{original_value}' → '{fixed_value}' (inferred from city/postal code/address). Confidence: {recommendation.confidence_score:.0%}. Action: Auto-fix applied."
                return f"Format corrected: '{original_value}' → '{fixed_value}' (normalized to valid enum value). Confidence: {recommendation.confidence_score:.0%}. Action: Auto-fix applied."
            elif rule_type == "regex":
                return f"Format corrected: '{original_value}' → '{fixed_value}' (cleaned to match required pattern). Confidence: {recommendation.confidence_score:.0%}. Action: Auto-fix applied."
            elif rule_type == "length":
                return f"Length corrected: '{original_value}' → '{fixed_value}' (truncated to valid length). Confidence: {recommendation.confidence_score:.0%}. Action: Auto-fix applied."
            elif rule_type == "phone":
                return f"Phone format corrected: '{original_value}' → '{fixed_value}' (normalized to digits only). Confidence: {recommendation.confidence_score:.0%}. Action: Auto-fix applied."
            elif rule_type == "date":
                return f"Date format corrected: '{original_value}' → '{fixed_value}' (reformatted to standard format). Confidence: {recommendation.confidence_score:.0%}. Action: Auto-fix applied."
            elif rule_type == "numeric":
                return f"Numeric format corrected: '{original_value}' → '{fixed_value}' (parsed and normalized). Confidence: {recommendation.confidence_score:.0%}. Action: Auto-fix applied."
            else:
                return f"Format corrected: '{original_value}' → '{fixed_value}'. Confidence: {recommendation.confidence_score:.0%}. Action: Auto-fix applied."
        else:
            # Cannot be auto-fixed - needs review
            if rule_type == "enum":
                return f"Value '{original_value}' does not match allowed values. Recommendation: Review and correct manually. Issue: Invalid enum value. Impact: {recommendation.estimated_impact}. Requires: Manual intervention."
            elif rule_type == "regex":
                return f"Value '{original_value}' does not match required pattern. Recommendation: Review format and correct manually. Issue: Pattern violation. Impact: {recommendation.estimated_impact}. Requires: Manual intervention."
            elif rule_type == "length":
                return f"Value '{original_value}' has invalid length. Recommendation: Review and adjust length manually. Issue: Length violation. Impact: {recommendation.estimated_impact}. Requires: Manual intervention."
            elif rule_type == "phone":
                return f"Value '{original_value}' is not a valid phone format. Recommendation: Review and correct phone number manually. Issue: Phone format violation. Impact: {recommendation.estimated_impact}. Requires: Manual intervention."
            elif rule_type == "date":
                return f"Value '{original_value}' is not a valid date format. Recommendation: Review and correct date manually. Issue: Date format violation. Impact: {recommendation.estimated_impact}. Requires: Manual intervention."
            elif rule_type == "numeric":
                return f"Value '{original_value}' is not a valid numeric format. Recommendation: Review and correct numeric value manually. Issue: Numeric format violation. Impact: {recommendation.estimated_impact}. Requires: Manual intervention."
            else:
                return f"Value '{original_value}' violates validation rules. Recommendation: Review and correct manually. Issue: {recommendation.issue_type}. Impact: {recommendation.estimated_impact}. Requires: Manual intervention."
    
    def _validate_fixes(
        self,
        df: pd.DataFrame,
        fixes_applied: List[FixResult],
        row_recommendations: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Validate that fixes were applied correctly."""
        validation_results = {
            "columns_validated": 0,
            "details": [],
            "completeness_details": []
        }
        
        # Build a map of row_index -> column -> recommended_value for quick lookup
        recommendations_map = {}
        if row_recommendations:
            for rec in row_recommendations:
                row_idx = rec.get("row_index")
                col_name = rec.get("column")
                recommended_val = rec.get("recommended_value")
                # Only include recommendations with actual values (not None, not empty string)
                if row_idx is not None and col_name and recommended_val is not None and str(recommended_val).strip() != "":
                    if row_idx not in recommendations_map:
                        recommendations_map[row_idx] = {}
                    recommendations_map[row_idx][col_name] = recommended_val
        
        # Track which columns we've already validated to avoid duplicates
        validated_columns = set()
        
        for fix_result in fixes_applied:
            # Skip if we've already validated this column
            if fix_result.column_name in validated_columns:
                continue
            
            # Process all fixes, regardless of success status
            # Check if there's a fixed column (when using non-destructive mode)
            fixed_column = self._find_column(df, f"{fix_result.column_name}{self.fix_column_suffix}")
            original_column = self._find_column(df, fix_result.column_name)
            
            if not original_column:
                continue
            
            rule = self.validation_rules.get(fix_result.column_name)
            if not rule:
                continue
            
            # Mark this column as validated
            validated_columns.add(fix_result.column_name)
            validation_results["columns_validated"] += 1
            
            # Check conformity rate for this column
            # Priority: fixed_column > recommended_value (from row_recommendations) > original
            non_null_count = 0
            conforming_count = 0
            
            for idx in df.index:
                # Determine which value to use for validation
                value = None
                if fixed_column and pd.notna(df.at[idx, fixed_column]):
                    # Use fixed value if available
                    value = df.at[idx, fixed_column]
                elif self.recommendations_only and idx in recommendations_map:
                    # In recommendations-only mode, use recommended value if available
                    recommended_val = recommendations_map[idx].get(fix_result.column_name)
                    if recommended_val is not None:
                        value = recommended_val
                    else:
                        value = df.at[idx, original_column]
                else:
                    # Use original value
                    value = df.at[idx, original_column]
                
                if not is_value_null_for_completeness(value, fix_result.column_name):
                    non_null_count += 1
                    if rule.validate_value(value):
                        conforming_count += 1
            
            conformity_rate = (conforming_count / non_null_count * 100) if non_null_count > 0 else 0
            
            validation_results["details"].append({
                "column": fix_result.column_name,
                "conformity_rate": conformity_rate
            })
            
            # Calculate completeness for this column (after fixes)
            total_count = len(df)
            non_null_count = 0
            
            for idx in df.index:
                # Determine which value to use for completeness
                value = None
                if fixed_column and pd.notna(df.at[idx, fixed_column]):
                    value = df.at[idx, fixed_column]
                elif self.recommendations_only and idx in recommendations_map:
                    recommended_val = recommendations_map[idx].get(fix_result.column_name)
                    if recommended_val is not None:
                        value = recommended_val
                    else:
                        value = df.at[idx, original_column]
                else:
                    value = df.at[idx, original_column]
                
                if not is_value_null_for_completeness(value, fix_result.column_name):
                    non_null_count += 1
            
            completeness_rate = (non_null_count / total_count * 100) if total_count > 0 else 0
            validation_results["completeness_details"].append({
                "column": fix_result.column_name,
                "completeness_rate": completeness_rate
            })
        
        return validation_results
    
    def _calculate_baseline_metrics(
        self,
        df: pd.DataFrame,
        recommendations: List[FixRecommendation]
    ) -> Dict[str, Any]:
        """Calculate baseline metrics (before fixes) for columns that will be fixed."""
        baseline = {
            "columns_analyzed": 0,
            "conformity_details": [],
            "completeness_details": []
        }
        
        # Get unique columns from recommendations
        columns_to_analyze = set()
        for rec in recommendations:
            columns_to_analyze.add(rec.column_name)
        
        # Also include all columns that have validation rules (for comprehensive baseline)
        # This ensures columns that are already 100% conforming but will be validated
        # after fixes are included in the baseline
        all_columns_with_rules = set(self.validation_rules.keys())
        columns_to_analyze.update(all_columns_with_rules)
        
        for column_name in columns_to_analyze:
            column = self._find_column(df, column_name)
            if not column:
                continue
            
            # Calculate conformity (only if there's a validation rule)
            rule = self.validation_rules.get(column_name)
            if rule:
                non_null_count = 0
                conforming_count = 0
                
                for value in df[column]:
                    if not is_value_null_for_completeness(value, column_name):
                        non_null_count += 1
                        if rule.validate_value(value):
                            conforming_count += 1
                
                conformity_rate = (conforming_count / non_null_count * 100) if non_null_count > 0 else 0
                baseline["conformity_details"].append({
                    "column": column_name,
                    "conformity_rate": conformity_rate
                })
            
            # Calculate completeness (for all columns)
            total_count = len(df)
            non_null_count = 0
            for value in df[column]:
                if not is_value_null_for_completeness(value, column_name):
                    non_null_count += 1
            
            completeness_rate = (non_null_count / total_count * 100) if total_count > 0 else 0
            baseline["completeness_details"].append({
                "column": column_name,
                "completeness_rate": completeness_rate
            })
            
            baseline["columns_analyzed"] += 1
        
        return baseline
    
    def _format_validation_results(
        self,
        baseline_metrics: Optional[Dict[str, Any]],
        validation_results: Dict[str, Any]
    ) -> str:
        """Format validation results with before/after comparison."""
        output_lines = []
        
        # ========== BEFORE FIX SUMMARY ==========
        output_lines.append("=" * 80)
        output_lines.append("BEFORE FIX - ORIGINAL DATA QUALITY")
        output_lines.append("=" * 80)
        output_lines.append("")
        
        if baseline_metrics:
            # Combine conformity and completeness into one table
            conformity_details = baseline_metrics.get("conformity_details", [])
            completeness_details = baseline_metrics.get("completeness_details", [])
            
            # Create a combined dictionary
            combined_stats = {}
            
            # Add conformity data
            for detail in conformity_details:
                col_name = detail.get("column", "Unknown")
                combined_stats[col_name] = {
                    "conformity_rate": detail.get("conformity_rate", 0.0)
                }
            
            # Add completeness data
            for detail in completeness_details:
                col_name = detail.get("column", "Unknown")
                if col_name not in combined_stats:
                    combined_stats[col_name] = {}
                combined_stats[col_name]["completeness_rate"] = detail.get("completeness_rate", 0.0)
            
            # Summary statistics
            total_cols = len(combined_stats)
            
            output_lines.append(f"Total Columns: {total_cols}")
            output_lines.append("")
            
            # Sort by lowest conformity rate first
            sorted_columns = sorted(
                combined_stats.items(),
                key=lambda x: x[1].get("conformity_rate", 0.0)  # Lower rates first
            )
            
            output_lines.append("Column Details:")
            output_lines.append("-" * 80)
            output_lines.append(f"{'Conformity':<12} | {'Completeness':<12} | Column Name")
            output_lines.append("-" * 80)
            
            for col_name, stats in sorted_columns:
                conf_rate = stats.get("conformity_rate", 0.0)
                compl_rate = stats.get("completeness_rate", 0.0)
                
                output_lines.append(
                    f"{conf_rate:>10.2f}% | {compl_rate:>10.2f}% | {col_name}"
                )
        else:
            output_lines.append("Baseline metrics not available.")
        
        output_lines.append("")
        output_lines.append("")
        
        # ========== AFTER FIX SUMMARY ==========
        output_lines.append("=" * 80)
        output_lines.append("AFTER FIX - FINAL DATA QUALITY")
        output_lines.append("=" * 80)
        output_lines.append("")
        
        if validation_results:
            # Combine conformity and completeness into one table
            details = validation_results.get("details", [])
            completeness_details = validation_results.get("completeness_details", [])
            
            # Create combined dictionary
            combined_stats = {}
            
            # Add conformity data (no duplicates expected, but handle gracefully)
            for detail in details:
                col_name = detail.get("column", "Unknown")
                rate = detail.get("conformity_rate", 0.0)
                
                if col_name not in combined_stats:
                    combined_stats[col_name] = {
                        "conformity_rate": rate
                    }
                else:
                    # If duplicate found (shouldn't happen), keep worst rate
                    if rate < combined_stats[col_name]["conformity_rate"]:
                        combined_stats[col_name]["conformity_rate"] = rate
            
            # Add completeness data
            for detail in completeness_details:
                col_name = detail.get("column", "Unknown")
                if col_name not in combined_stats:
                    combined_stats[col_name] = {}
                combined_stats[col_name]["completeness_rate"] = detail.get("completeness_rate", 0.0)
            
            # Summary statistics
            total = validation_results.get("columns_validated", 0)
            
            output_lines.append(f"Total Columns Validated: {total}")
            output_lines.append("")
            
            # Sort by lowest conformity rate first
            sorted_columns = sorted(
                combined_stats.items(),
                key=lambda x: x[1].get("conformity_rate", 0.0)  # Lower rates first
            )
            
            output_lines.append("Column Details:")
            output_lines.append("-" * 80)
            output_lines.append(f"{'Conformity':<12} | {'Completeness':<12} | Column Name")
            output_lines.append("-" * 80)
            
            for col_name, stats in sorted_columns:
                conf_rate = stats.get("conformity_rate", 0.0)
                compl_rate = stats.get("completeness_rate", 0.0)
                
                output_lines.append(
                    f"{conf_rate:>10.2f}% | {compl_rate:>10.2f}% | {col_name}"
                )
        else:
            output_lines.append("Validation results not available.")
        
        output_lines.append("")
        output_lines.append("")
        
        # ========== CONFORMITY SUMMARY TABLE ==========
        output_lines.append("=" * 80)
        output_lines.append("CONFORMITY SUMMARY - BEFORE vs AFTER FIX")
        output_lines.append("=" * 80)
        output_lines.append("")
        
        if baseline_metrics and validation_results:
            # Get conformity data from both before and after
            before_conformity = {}
            after_conformity = {}
            
            # Extract before conformity rates
            for detail in baseline_metrics.get("conformity_details", []):
                col_name = detail.get("column", "Unknown")
                before_conformity[col_name] = detail.get("conformity_rate", 0.0)
            
            # Extract after conformity rates
            for detail in validation_results.get("details", []):
                col_name = detail.get("column", "Unknown")
                rate = detail.get("conformity_rate", 0.0)
                # Handle duplicates by keeping the worst rate
                if col_name not in after_conformity or rate < after_conformity[col_name]:
                    after_conformity[col_name] = rate
            
            # Combine all columns (union of before and after)
            all_columns = set(before_conformity.keys()) | set(after_conformity.keys())
            
            if all_columns:
                # Sort by column name for consistency
                sorted_columns = sorted(all_columns)
                
                output_lines.append(f"{'Column Name':<60} | {'Conformity Before':<18} | {'Conformity After':<18}")
                output_lines.append("-" * 100)
                
                for col_name in sorted_columns:
                    before_rate = before_conformity.get(col_name, None)
                    after_rate = after_conformity.get(col_name, None)
                    
                    before_str = f"{before_rate:.2f}%" if before_rate is not None else "N/A"
                    after_str = f"{after_rate:.2f}%" if after_rate is not None else "N/A"
                    
                    # Truncate long column names
                    display_name = col_name[:58] if len(col_name) > 58 else col_name
                    
                    output_lines.append(
                        f"{display_name:<60} | {before_str:>18} | {after_str:>18}"
                    )
                
                # Add summary statistics
                output_lines.append("-" * 100)
                
                # Calculate averages
                before_rates = [r for r in before_conformity.values() if r is not None]
                after_rates = [r for r in after_conformity.values() if r is not None]
                
                # Build average line
                avg_line = f"{'Average Conformity':<60} | "
                if before_rates:
                    avg_before = sum(before_rates) / len(before_rates)
                    avg_line += f"{avg_before:>17.2f}%"
                else:
                    avg_line += f"{'N/A':>18}"
                
                avg_line += " | "
                if after_rates:
                    avg_after = sum(after_rates) / len(after_rates)
                    avg_line += f"{avg_after:>17.2f}%"
                else:
                    avg_line += f"{'N/A':>18}"
                
                output_lines.append(avg_line)
                
                # Calculate improvement
                if before_rates and after_rates:
                    common_cols = set(before_conformity.keys()) & set(after_conformity.keys())
                    if common_cols:
                        improvements = []
                        for col in common_cols:
                            before = before_conformity[col]
                            after = after_conformity[col]
                            if before is not None and after is not None:
                                improvements.append(after - before)
                        
                        if improvements:
                            avg_improvement = sum(improvements) / len(improvements)
                            output_lines.append(f"{'Average Improvement':<60} | {'':>18} | {avg_improvement:>+17.2f}%")
            else:
                output_lines.append("No conformity data available for comparison.")
        else:
            output_lines.append("Baseline or validation results not available for comparison.")
        
        output_lines.append("")
        output_lines.append("=" * 80)
        
        return "\n".join(output_lines)
