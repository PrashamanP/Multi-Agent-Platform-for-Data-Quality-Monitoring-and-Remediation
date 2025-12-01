"""
Fix Recommendation Agent for generating intelligent suggestions to resolve data quality issues.
"""

import logging
import time
import uuid
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

from src.agents.base_agent import BaseAgent
from src.core.config_manager import ConfigManager
from src.core.data_models import (
    FixRecommendation,
    FixRecommendationResults,
    Issue,
    Anomaly,
    ProfileResults,
    AnomalyDetectionResults,
    ColumnMetrics,
)
from src.core.validation_rule_loader import ValidationRuleLoader
from src.data_access.file_handler import FileHandler
from src.core.data_models import is_value_null_for_completeness

logger = logging.getLogger(__name__)


class FixRecommendationAgent(BaseAgent):
    """
    Agent responsible for generating intelligent fix recommendations for data quality issues.
    
    Analyzes issues and anomalies to suggest:
    - Completeness: imputation strategies based on patterns
    - Conformity: format corrections based on validation rules
    - Uniqueness: duplicate removal strategies
    - Anomalies: remediation based on historical patterns
    """
    
    # Safe auto-fix thresholds based on configuration
    SAFE_IMPUTATION_CONFIDENCE = 0.85
    SAFE_FORMAT_CORRECTION_CONFIDENCE = 0.90
    SAFE_DUPLICATE_REMOVAL_CONFIDENCE = 0.95
    
    def __init__(
        self,
        validation_rules: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        validation_rule_loader: Optional[ValidationRuleLoader] = None
    ):
        """
        Initialize fix recommendation agent.
        
        Args:
            validation_rules: Custom validation rules. If None, loads from config.
            config: Agent configuration. If None, loads from config manager.
            validation_rule_loader: ValidationRuleLoader instance. If None, creates new one.
        """
        super().__init__("fix_recommendation", config)
        
        # Load configuration if not provided
        if config is None:
            config_manager = ConfigManager()
            self.config = config_manager.get_agent_config("fix_recommendation")
        
        # Initialize validation rule loader
        self.validation_rule_loader = validation_rule_loader or ValidationRuleLoader()
        
        # Load validation rules
        if validation_rules is None:
            self.validation_rules = self.validation_rule_loader.load_validation_rules()
        else:
            self.validation_rules = validation_rules
        
        # Agent configuration
        self.confidence_threshold = self.config.get("confidence_threshold", 0.75)
        self.enable_auto_suggestions = self.config.get("enable_auto_suggestions", True)
        self.enable_llm_fixes = self.config.get("enable_llm_fixes", False)
        self.llm_config = self.config.get("llm_config", {})
        
        logger.info("FixRecommendationAgent initialized")
    
    def execute(
        self,
        profile_results: Optional[ProfileResults] = None,
        anomaly_results: Optional[AnomalyDetectionResults] = None,
        issues: Optional[List[Issue]] = None,
        anomalies: Optional[List[Anomaly]] = None,
        dataset_path: Optional[str] = None,
        **kwargs
    ) -> FixRecommendationResults:
        """
        Generate fix recommendations for detected issues and anomalies.
        
        Args:
            profile_results: Profile results containing issues
            anomaly_results: Anomaly detection results
            issues: Direct list of issues (alternative to profile_results)
            anomalies: Direct list of anomalies (alternative to anomaly_results)
            dataset_path: Path to the dataset file (optional, used for row-by-row analysis)
            
        Returns:
            FixRecommendationResults containing all recommendations
        """
        start_time = time.time()
        
        # Collect issues and anomalies from various sources
        all_issues = []
        all_anomalies = []
        
        if profile_results and profile_results.issues:
            all_issues.extend(profile_results.issues)
            dataset_name = profile_results.dataset_profile.dataset_name
            run_id = profile_results.execution_metadata.get("run_id")
        elif issues:
            all_issues = issues
            dataset_name = kwargs.get("dataset_name", "unknown")
            run_id = kwargs.get("run_id")
        else:
            dataset_name = kwargs.get("dataset_name", "unknown")
            run_id = kwargs.get("run_id")
        
        if anomaly_results and anomaly_results.anomalies:
            all_anomalies.extend(anomaly_results.anomalies)
        elif anomalies:
            all_anomalies = anomalies
        
        if not all_issues and not all_anomalies:
            logger.warning("No issues or anomalies provided for recommendation generation")
            return FixRecommendationResults(
                dataset_name=dataset_name or "unknown",
                run_id=run_id,
                recommendations=[],
                execution_metadata={
                    "execution_time_seconds": time.time() - start_time,
                    "agent_version": "1.0.0",
                    "timestamp": datetime.now().isoformat(),
                    "issues_analyzed": 0,
                    "anomalies_analyzed": 0,
                    "total_recommendations": 0,
                    "actionable_recommendations": 0
                }
            )
        
        logger.info(
            f"Generating fix recommendations for {len(all_issues)} issues "
            f"and {len(all_anomalies)} anomalies"
        )
        
        recommendations = []
        
        # Generate recommendations for issues
        if profile_results:
            recommendations.extend(
                self._recommend_for_issues(all_issues, profile_results.dataset_profile, dataset_path)
            )
        else:
            recommendations.extend(self._recommend_for_issues_simple(all_issues))
        
        # Generate recommendations for anomalies
        recommendations.extend(self._recommend_for_anomalies(all_anomalies))
        
        execution_time = time.time() - start_time
        
        metadata = {
            "execution_time_seconds": execution_time,
            "agent_version": "1.0.0",
            "timestamp": datetime.now().isoformat(),
            "issues_analyzed": len(all_issues),
            "anomalies_analyzed": len(all_anomalies),
            "total_recommendations": len(recommendations),
            "actionable_recommendations": sum(1 for r in recommendations if r.actionable)
        }
        
        logger.info(
            f"Generated {len(recommendations)} recommendations "
            f"({sum(1 for r in recommendations if r.actionable)} actionable) "
            f"in {execution_time:.2f}s"
        )
        
        return FixRecommendationResults(
            dataset_name=dataset_name,
            run_id=run_id,
            recommendations=recommendations,
            execution_metadata=metadata
        )
    
    def _recommend_for_issues(
        self, 
        issues: List[Issue], 
        dataset_profile,
        dataset_path: Optional[str] = None
    ) -> List[FixRecommendation]:
        """Generate recommendations for a list of issues with full profile context."""
        recommendations = []
        # Track which (column, issue_type) combinations we've already processed to avoid duplicates
        processed_keys = set()
        
        for issue in issues:
            # Create a unique key for this issue
            key = (issue.column_name, issue.issue_type)
            
            # Skip if we've already processed this combination
            if key in processed_keys:
                logger.warning(f"Skipping duplicate issue: {issue.column_name} ({issue.issue_type})")
                continue
            
            rec = self._recommend_for_issue(issue, dataset_profile, dataset_path)
            if rec:
                recommendations.append(rec)
                processed_keys.add(key)
        
        return recommendations
    
    def _recommend_for_issues_simple(self, issues: List[Issue]) -> List[FixRecommendation]:
        """Generate recommendations for a list of issues without full profile context."""
        recommendations = []
        # Track which (column, issue_type) combinations we've already processed to avoid duplicates
        processed_keys = set()
        
        for issue in issues:
            # Create a unique key for this issue
            key = (issue.column_name, issue.issue_type)
            
            # Skip if we've already processed this combination
            if key in processed_keys:
                logger.warning(f"Skipping duplicate issue: {issue.column_name} ({issue.issue_type})")
                continue
            
            rec = self._recommend_for_issue_simple(issue)
            if rec:
                recommendations.append(rec)
                processed_keys.add(key)
        
        return recommendations
    
    def _recommend_for_issue(
        self, 
        issue: Issue, 
        dataset_profile,
        dataset_path: Optional[str] = None
    ) -> Optional[FixRecommendation]:
        """Generate a recommendation for a single issue."""
        column_metrics = dataset_profile.column_metrics.get(issue.column_name)
        
        if issue.issue_type == "completeness":
            return self._recommend_completeness_fix(issue, column_metrics, dataset_profile, dataset_path)
        elif issue.issue_type == "conformity":
            return self._recommend_conformity_fix(issue, column_metrics, dataset_path)
        elif issue.issue_type == "uniqueness":
            return self._recommend_uniqueness_fix(issue, column_metrics)
        
        return None
    
    def _recommend_for_issue_simple(self, issue: Issue) -> Optional[FixRecommendation]:
        """Generate a recommendation for a single issue without full context."""
        if issue.issue_type == "completeness":
            return self._recommend_completeness_fix_simple(issue)
        elif issue.issue_type == "conformity":
            return self._recommend_conformity_fix_simple(issue)
        elif issue.issue_type == "uniqueness":
            return self._recommend_uniqueness_fix_simple(issue)
        
        return None
    
    def _recommend_completeness_fix(
        self,
        issue: Issue,
        column_metrics: Optional[ColumnMetrics],
        dataset_profile,
        dataset_path: Optional[str] = None
    ) -> Optional[FixRecommendation]:
        """Recommend fix for completeness issues."""
        if not column_metrics:
            return self._recommend_completeness_fix_simple(issue)
        
        # Special handling for Entity Type Code - use row-by-row conditional logic
        if issue.column_name == "Entity Type Code":
            return self._recommend_entity_type_code_fix(issue, column_metrics, dataset_profile, dataset_path)
        
        # Analyze column patterns to suggest imputation
        suggested_value = None
        confidence = 0.5
        actionable = False
        strategy = "default_imputation"  # Default strategy instead of flag_for_review
        rationale = ""  # Initialize rationale
        
        # Strategy 1: Conditional completeness for name fields (similar to Entity Type Code)
        is_name_field = issue.column_name in [
            "Provider Last Name (Legal Name)",
            "Provider First Name",
            "Provider Organization Name (Legal Business Name)"
        ]
        if is_name_field and dataset_path:
            # These fields have conditional requirements based on Entity Type Code
            # But since we can't determine Entity Type Code from name fields themselves,
            # we'll use conditional_imputation strategy and let executor handle row-by-row logic
            strategy = "conditional_imputation"
            confidence = 0.75  # Moderate confidence - depends on Entity Type Code being available
            actionable = False  # Not actionable without Entity Type Code context
            rationale = (
                f"{issue.column_name} is conditionally required based on Entity Type Code. "
                f"Individual providers (Type 1) require Last/First Name. "
                f"Organizations (Type 2) require Organization Name. "
                f"Fix will be applied row-by-row based on Entity Type Code in each row."
            )
        
        # Strategy 2: Context-based imputation for City/State when missing
        is_city_column = "City Name" in issue.column_name and "Address" in issue.column_name
        is_state_column = "State Name" in issue.column_name and "Address" in issue.column_name
        if (is_city_column or is_state_column) and dataset_path and not is_name_field:
            # We rely on executor to perform row-by-row inference using address lines,
            # postal code normalization, and country code context. Here we recommend
            # the strategy with high confidence when sufficient context columns exist.
            # Determine which address type (Mailing vs Practice Location) based on column name
            is_practice_location = "Practice Location" in issue.column_name
            if is_practice_location:
                required_cols = [
                    "Provider First Line Business Practice Location Address",
                    "Provider Second Line Business Practice Location Address",
                    "Provider Business Practice Location Address Postal Code",
                    "Provider Business Practice Location Address Country Code (If outside U.S.)"
                ]
            else:
                # Mailing Address
                required_cols = [
                    "Provider First Line Business Mailing Address",
                    "Provider Second Line Business Mailing Address",
                    "Provider Business Mailing Address Postal Code",
                    "Provider Business Mailing Address Country Code (If outside U.S.)"
                ]
            try:
                import pandas as pd
                df_head = pd.read_csv(dataset_path, nrows=1)
                has_context = all(col in df_head.columns for col in required_cols)
            except Exception:
                has_context = False
            
            if has_context:
                strategy = "context_imputation"
                # Higher confidence for state inference (has multiple strategies); moderate for city
                # Set confidence high enough to be actionable (>= 0.85)
                confidence = 0.85 if is_state_column else 0.80
                actionable = True  # Context-based inference is safe when context columns exist
                rationale = (
                    f"Infer missing {('state' if is_state_column else 'city')} from address lines, postal code, and country code using context-based inference."
                )
            else:
                # Limited context available
                strategy = "context_imputation"
                confidence = 0.6
                actionable = False
                rationale = (
                    "Limited context for inference (address/postal/country columns missing) - manual review recommended"
                )
        
        # Strategy 3: Postal Code inference from address or state
        is_postal_code = "Postal Code" in issue.column_name and "Address" in issue.column_name
        if is_postal_code and dataset_path and not is_name_field and strategy == "default_imputation":
            # Postal codes can sometimes be inferred from address lines or state
            # But this is less reliable, so we use conditional_imputation
            strategy = "context_imputation"
            confidence = 0.70  # Lower confidence than city/state
            actionable = False  # Not safe to auto-fix postal codes
            rationale = (
                "Postal code can potentially be inferred from address lines or state, "
                "but requires manual verification. Manual review recommended."
            )
        
        # Strategy 4: Use default values for specific columns
        # NOTE: Default imputation is disabled - do not use default values to fill completeness values
        # If no suggested_value is found, treat as no recommendation
        
        # Strategy 6: Address line fields - optional fields, lower priority
        is_address_line = (
            "First Line" in issue.column_name or "Second Line" in issue.column_name
        ) and "Address" in issue.column_name
        if is_address_line and strategy == "default_imputation" and not suggested_value:
            strategy = "default_imputation"
            confidence = 0.50
            actionable = False
            rationale = (
                f"Address line fields are optional. Missing values may be acceptable. "
                f"Manual review recommended to determine if values should be filled."
            )
        
        # Strategy 7: Phone/Fax fields - optional fields
        is_phone_field = "Telephone Number" in issue.column_name or "Fax Number" in issue.column_name
        if is_phone_field and strategy == "default_imputation" and not suggested_value:
            strategy = "default_imputation"
            confidence = 0.50
            actionable = False
            rationale = (
                f"Phone/Fax fields are optional contact information. "
                f"Missing values may be acceptable. Manual review recommended."
            )
        
        # Strategy 8: Country Code - default to "US" for US addresses
        is_country_code = "Country Code" in issue.column_name and "Address" in issue.column_name
        if is_country_code and strategy == "default_imputation" and not suggested_value:
            # Check if state column exists and suggests US addresses
            state_col_name = issue.column_name.replace("Country Code (If outside U.S.)", "State Name")
            state_metrics = dataset_profile.column_metrics.get(state_col_name)
            if state_metrics and state_metrics.completeness_score > 50:
                # If state is present, likely US address - default to "US"
                suggested_value = "US"
                strategy = "default_imputation"
                confidence = 0.75
                actionable = True
                rationale = (
                    f"Country Code missing but State Name present suggests US address. "
                    f"Defaulting to 'US' for missing values."
                )
            else:
                strategy = "default_imputation"
                confidence = 0.50
                actionable = False
                rationale = (
                    "Country Code is optional (only required if outside U.S.). "
                    "Missing values may indicate US addresses. Manual review recommended."
                )
        
        # Determine estimated impact
        # For context_imputation and conditional_imputation, actionable flag is already set
        if strategy == "context_imputation":
            # Context-based inference actionable flag already set above
            estimated_impact = "low" if issue.percentage < 5 else "medium"
        elif strategy == "conditional_imputation":
            # Conditional imputation actionable flag already set above
            estimated_impact = "low" if issue.percentage < 5 else "medium"
        elif suggested_value:
            # If we have a suggested value, it can be auto-filled
            actionable = True
            estimated_impact = "low" if issue.percentage < 5 else "medium"
        else:
            # No suggested value available - treat as no recommendation
            # Don't create a recommendation if we can't suggest a value
            return None
        
        if not rationale:
            rationale = f"Missing values detected in {issue.column_name} ({issue.count} values, {issue.percentage:.1f}%). Manual review recommended to determine appropriate fill strategy."
        
        return FixRecommendation(
            recommendation_id=str(uuid.uuid4()),
            issue_id=issue.issue_id,
            dataset_name=dataset_profile.dataset_name,
            column_name=issue.column_name,
            issue_type=issue.issue_type,
            fix_strategy=strategy,
            fix_description=f"Fill {issue.count} missing values in {issue.column_name}",
            confidence_score=confidence,
            estimated_impact=estimated_impact,
            actionable=actionable,
            suggested_value=suggested_value,
            rationale=rationale,
            prerequisites=[],
            risk_assessment=""
        )
    
    def _recommend_completeness_fix_simple(self, issue: Issue) -> Optional[FixRecommendation]:
        """Recommend fix for completeness issues without full context."""
        # No default imputation - return None if no suggested value can be determined
        return None
    
    def _recommend_conformity_fix(
        self, 
        issue: Issue, 
        column_metrics: Optional[ColumnMetrics],
        dataset_path: Optional[str] = None
    ) -> Optional[FixRecommendation]:
        """Recommend fix for conformity issues."""
        if not column_metrics or not issue.examples:
            return self._recommend_conformity_fix_simple(issue)
        
        # Analyze violation examples
        violations = issue.examples[:10]  # Look at more examples for better analysis
        strategy = "format_correction"
        confidence = 0.7
        actionable = False
        fixable_count = 0
        
        # Try to identify common patterns in violations
        rule = self.validation_rules.get(issue.column_name)
        if not rule:
            return self._recommend_conformity_fix_simple(issue)
        
        # Analyze violations based on rule type
        if rule.rule_type == "regex":
            suggested_fixes, fixable_count = self._analyze_regex_violations(violations, rule, issue.column_name)
        elif rule.rule_type == "length":
            suggested_fixes, fixable_count = self._analyze_length_violations(violations, rule)
        elif rule.rule_type == "enum":
            suggested_fixes, fixable_count = self._analyze_enum_violations(violations, rule, issue.column_name, dataset_path)
        elif rule.rule_type == "phone":
            suggested_fixes, fixable_count = self._analyze_phone_violations(violations, rule)
        elif rule.rule_type == "date":
            suggested_fixes, fixable_count = self._analyze_date_violations(violations, rule)
        else:
            suggested_fixes = []
            fixable_count = 0
        
        # Calculate confidence based on how many violations appear fixable
        total_violations = len(violations)
        if total_violations > 0:
            fixable_ratio = fixable_count / total_violations
            # If 80%+ of violations are fixable, we're confident
            if fixable_ratio >= 0.8:
                confidence = 0.90
            elif fixable_ratio >= 0.6:
                confidence = 0.85
            elif fixable_ratio >= 0.4:
                confidence = 0.75
            else:
                confidence = 0.65
        
        # Build rationale - indicate if all violations were analyzed
        analyze_all = total_violations == issue.count
        if analyze_all:
            rationale = f"All {len(violations)} format violations analyzed in {issue.column_name} (out of {issue.count} total)"
        else:
            rationale = f"{len(violations)} format violation samples analyzed in {issue.column_name} (out of {issue.count} total)"
        
        # Always show fixable vs total to clarify how many can be automatically fixed
        if fixable_count > 0:
            rationale += f". {fixable_count} of {total_violations} violations appear auto-fixable"
            if suggested_fixes:
                rationale += f". Common fixes: {', '.join(suggested_fixes[:3])}"
        else:
            # No violations are auto-fixable - manual review needed
            rationale += f". 0 of {total_violations} violations are auto-fixable (requires manual review)"
            if suggested_fixes:
                rationale += f". Manual fixes may include: {', '.join(suggested_fixes[:3])}"
        
        # Determine if safe for auto-fix
        # If we have suggested fixes and can fix some values, it's actionable
        if len(suggested_fixes) > 0 and fixable_count > 0:
            actionable = True
            estimated_impact = "low"
        else:
            estimated_impact = "medium"
        
        return FixRecommendation(
            recommendation_id=str(uuid.uuid4()),
            issue_id=issue.issue_id,
            dataset_name="unknown",
            column_name=issue.column_name,
            issue_type=issue.issue_type,
            fix_strategy=strategy,
            fix_description=f"Correct {issue.count} format violations in {issue.column_name}",
            confidence_score=confidence,
            estimated_impact=estimated_impact,
            actionable=actionable,
            rationale=rationale,
            prerequisites=[],
            risk_assessment=""
        )
    
    def _recommend_conformity_fix_simple(self, issue: Issue) -> Optional[FixRecommendation]:
        """Recommend fix for conformity issues without full context."""
        return FixRecommendation(
            recommendation_id=str(uuid.uuid4()),
            issue_id=issue.issue_id,
            dataset_name="unknown",
            column_name=issue.column_name,
            issue_type=issue.issue_type,
            fix_strategy="format_correction",
            fix_description=f"Investigate and correct {issue.count} format violations in {issue.column_name}",
            confidence_score=0.6,
            estimated_impact="medium",
            actionable=False,
            rationale="",
            prerequisites=[],
            risk_assessment=""
        )
    
    def _recommend_uniqueness_fix(
        self, 
        issue: Issue, 
        column_metrics: Optional[ColumnMetrics]
    ) -> Optional[FixRecommendation]:
        """Recommend fix for uniqueness issues (duplicates)."""
        if not column_metrics or issue.column_name != "NPI":
            return self._recommend_uniqueness_fix_simple(issue)
        
        # For NPI duplicates, always flag for review as it's critical
        confidence = 0.95
        actionable = False
        strategy = "remove_duplicates"
        
        return FixRecommendation(
            recommendation_id=str(uuid.uuid4()),
            issue_id=issue.issue_id,
            dataset_name="unknown",
            column_name=issue.column_name,
            issue_type=issue.issue_type,
            fix_strategy=strategy,
            fix_description=f"Investigate and remove {issue.count} duplicate NPI values",
            confidence_score=confidence,
            estimated_impact="high",
            actionable=actionable,
            rationale="",
            prerequisites=[],
            risk_assessment=""
        )
    
    def _recommend_uniqueness_fix_simple(self, issue: Issue) -> Optional[FixRecommendation]:
        """Recommend fix for uniqueness issues without full context."""
        return FixRecommendation(
            recommendation_id=str(uuid.uuid4()),
            issue_id=issue.issue_id,
            dataset_name="unknown",
            column_name=issue.column_name,
            issue_type=issue.issue_type,
            fix_strategy="remove_duplicates",
            fix_description=f"Investigate and remove {issue.count} duplicate values in {issue.column_name}",
            confidence_score=0.8,
            estimated_impact="high",
            actionable=False,
            rationale="",
            prerequisites=[],
            risk_assessment=""
        )
    
    def _recommend_for_anomalies(self, anomalies: List[Anomaly]) -> List[FixRecommendation]:
        """Generate recommendations for anomaly issues."""
        recommendations = []
        
        for anomaly in anomalies:
            rec = self._recommend_for_anomaly(anomaly)
            if rec:
                recommendations.append(rec)
        
        return recommendations
    
    def _recommend_for_anomaly(self, anomaly: Anomaly) -> Optional[FixRecommendation]:
        """Generate a recommendation for a single anomaly."""
        # Anomalies typically indicate systemic issues requiring investigation
        strategy = "investigate_root_cause"
        confidence = 0.7 if anomaly.severity == "high" else 0.5
        actionable = False
        
        # Determine recommendation based on anomaly direction
        if anomaly.delta < 0:  # Quality degraded
            description = f"{anomaly.metric} dropped {abs(anomaly.delta):.1f}% from baseline"
            rationale = f"Degradation detected: {anomaly.metric} fell from {anomaly.baseline_value:.1f}% to {anomaly.current_value:.1f}%"
        else:  # Quality improved unexpectedly
            description = f"{anomaly.metric} increased {anomaly.delta:.1f}% from baseline"
            rationale = f"Unexpected improvement: {anomaly.metric} rose from {anomaly.baseline_value:.1f}% to {anomaly.current_value:.1f}%"
        
        # Adjust confidence based on severity and z-score
        if anomaly.severity == "high" and anomaly.z_score > 3:
            confidence = 0.9
        
        return FixRecommendation(
            recommendation_id=str(uuid.uuid4()),
            issue_id=anomaly.anomaly_id,
            dataset_name=anomaly.dataset_name,
            column_name=anomaly.column_name,
            issue_type="anomaly",
            fix_strategy=strategy,
            fix_description=description,
            confidence_score=confidence,
            estimated_impact=anomaly.severity,
            actionable=actionable,
            rationale=rationale,
            prerequisites=[],
            risk_assessment=""
        )
    
    def _recommend_entity_type_code_fix(
        self,
        issue: Issue,
        column_metrics: Optional[ColumnMetrics],
        dataset_profile,
        dataset_path: Optional[str] = None
    ) -> Optional[FixRecommendation]:
        """
        Recommend Entity Type Code fix using row-by-row conditional logic.
        
        For each missing Entity Type Code value:
        - If that row has Organization Name → Entity Type Code = "2" (Organization)
        - If that row doesn't have Organization Name but has Individual Name → Entity Type Code = "1" (Individual)
        - High confidence, auto-fixable
        """
        org_name_col = "Provider Organization Name (Legal Business Name)"
        last_name_col = "Provider Last Name (Legal Name)"
        first_name_col = "Provider First Name"
        entity_type_col = "Entity Type Code"
        
        # If dataset path is available, load and check row-by-row
        if dataset_path:
            try:
                logger.info(f"Loading dataset for row-by-row Entity Type Code analysis: {dataset_path}")
                df = FileHandler.load_dataset(dataset_path, focused_columns_only=False)
                
                # Check if required columns exist (try exact match first, then case-insensitive)
                def find_column(df, target_name):
                    """Find column by exact match or case-insensitive match."""
                    if target_name in df.columns:
                        return target_name
                    # Try case-insensitive match
                    for col in df.columns:
                        if col.lower() == target_name.lower():
                            return col
                    return None
                
                entity_type_col_actual = find_column(df, entity_type_col)
                org_name_col_actual = find_column(df, org_name_col)
                last_name_col_actual = find_column(df, last_name_col)
                first_name_col_actual = find_column(df, first_name_col)
                
                if not all([entity_type_col_actual, org_name_col_actual, last_name_col_actual, first_name_col_actual]):
                    missing = []
                    if not entity_type_col_actual:
                        missing.append(entity_type_col)
                    if not org_name_col_actual:
                        missing.append(org_name_col)
                    if not last_name_col_actual:
                        missing.append(last_name_col)
                    if not first_name_col_actual:
                        missing.append(first_name_col)
                    logger.warning(f"Missing required columns for row-by-row analysis: {missing}")
                    logger.info(f"Available columns: {list(df.columns)[:10]}...")  # Show first 10 columns
                    raise ValueError(f"Missing columns: {missing}")
                
                # Use actual column names found in dataset
                entity_type_col = entity_type_col_actual
                org_name_col = org_name_col_actual
                last_name_col = last_name_col_actual
                first_name_col = first_name_col_actual
                
                logger.info(f"Using columns: Entity Type={entity_type_col}, Org Name={org_name_col}, Last Name={last_name_col}, First Name={first_name_col}")
                
                # Count rows with missing Entity Type Code
                missing_mask = df[entity_type_col].apply(
                    lambda x: is_value_null_for_completeness(x, entity_type_col)
                )
                missing_rows = df[missing_mask]
                
                logger.info(f"Found {len(missing_rows)} rows with missing Entity Type Code out of {len(df)} total rows")
                
                if len(missing_rows) > 0:
                    # Count how many can be filled based on row-level logic
                    org_count = 0
                    individual_count = 0
                    unknown_count = 0
                    
                    for idx, row in missing_rows.iterrows():
                        # Check if Organization Name exists (not null)
                        org_name = row.get(org_name_col)
                        has_org_name = not is_value_null_for_completeness(org_name, org_name_col)
                        
                        # Check if Individual Names exist (not null)
                        last_name = row.get(last_name_col)
                        first_name = row.get(first_name_col)
                        has_last_name = not is_value_null_for_completeness(last_name, last_name_col)
                        has_first_name = not is_value_null_for_completeness(first_name, first_name_col)
                        has_individual_name = has_last_name or has_first_name
                        
                        if has_org_name:
                            org_count += 1
                        elif has_individual_name:
                            individual_count += 1
                        else:
                            unknown_count += 1
                    
                    total_analyzable = org_count + individual_count
                    total_missing = len(missing_rows)
                    
                    logger.info(f"Row-by-row analysis results: {org_count} org rows, {individual_count} individual rows, {unknown_count} unknown rows")
                    
                    if total_analyzable > 0:
                        # Calculate confidence based on how many can be determined
                        # High confidence since we're using row-by-row logic with related fields
                        analyzable_ratio = total_analyzable / total_missing
                        if analyzable_ratio >= 0.85:
                            # Scale from 85% to 95% confidence for 85-100% analyzable
                            # Linear: 85% analyzable → 85% conf, 100% analyzable → 95% conf
                            confidence = 0.85 + (analyzable_ratio - 0.85) * (0.95 - 0.85) / (1.0 - 0.85)
                        else:
                            # Scale from 0 to 85% confidence for 0-85% analyzable
                            # Linear: 0% analyzable → 0% conf, 85% analyzable → 85% conf
                            confidence = analyzable_ratio * 0.85
                        strategy = "conditional_imputation"
                        actionable = True  # Entity Type Code can be auto-filled when we have name context
                        
                        # Build rationale with detailed breakdown
                        rationale_parts = []
                        if org_count > 0:
                            rationale_parts.append(f"{org_count} rows have Organization Name → fill with '2'")
                        if individual_count > 0:
                            rationale_parts.append(f"{individual_count} rows have Individual Names → fill with '1'")
                        if unknown_count > 0:
                            rationale_parts.append(f"{unknown_count} rows have neither → review needed")
                        
                        rationale = "Row-by-row analysis: " + "; ".join(rationale_parts)
                        
                        # Suggested value: Clearly indicate it's row-specific
                        if org_count > 0 and individual_count > 0:
                            suggested_value = f"Row-specific: {org_count} rows → '2', {individual_count} rows → '1'"
                        elif org_count > 0:
                            suggested_value = f"Row-specific: {org_count} rows → '2' (Organization)"
                        elif individual_count > 0:
                            suggested_value = f"Row-specific: {individual_count} rows → '1' (Individual)"
                        else:
                            suggested_value = "Row-specific: Review needed for all rows"
                        
                        estimated_impact = "low" if issue.percentage < 5 else "medium"
                        
                        logger.info(f"Generated row-by-row recommendation: {org_count} org, {individual_count} individual, confidence={confidence:.2f}")
                        
                        return FixRecommendation(
                            recommendation_id=str(uuid.uuid4()),
                            issue_id=issue.issue_id,
                            dataset_name=dataset_profile.dataset_name,
                            column_name=issue.column_name,
                            issue_type=issue.issue_type,
                            fix_strategy=strategy,
                            fix_description=f"Fill {issue.count} missing values in {issue.column_name} using row-by-row conditional logic",
                            confidence_score=confidence,
                            estimated_impact=estimated_impact,
                            actionable=actionable,
                            suggested_value=suggested_value,
            rationale=rationale,
            prerequisites=[],
            risk_assessment=""
                        )
                    else:
                        logger.warning("No rows could be analyzed for Entity Type Code (all missing rows have neither org name nor individual names)")
                        # Return default_imputation - will be processed but may not have suggested values
                        return FixRecommendation(
                            recommendation_id=str(uuid.uuid4()),
                            issue_id=issue.issue_id,
                            dataset_name=dataset_profile.dataset_name,
                            column_name=issue.column_name,
                            issue_type=issue.issue_type,
                            fix_strategy="default_imputation",
                            fix_description=f"Fill {issue.count} missing values in {issue.column_name}",
                            confidence_score=0.5,
                            estimated_impact="high",
                            actionable=False,
                            suggested_value=None,
                            rationale=f"All {issue.count} missing Entity Type Code rows also have missing Organization Name and Individual Names - cannot determine entity type from related fields.",
                            prerequisites=[],
                            risk_assessment=""
                        )
            except Exception as e:
                logger.warning(f"Could not load dataset for row-by-row Entity Type Code analysis: {e}")
                import traceback
                logger.debug(f"Traceback: {traceback.format_exc()}")
                # Fall through to aggregate-based logic only if dataset couldn't be loaded
                # If dataset was loaded but analysis found no analyzable rows, we already returned above
        
        # Fallback: Aggregate-based logic (if dataset_path not available or load failed)
        # IMPORTANT: For Entity Type Code, we should NOT use aggregate logic if row-by-row
        # analysis was attempted but found no analyzable rows - that case is already handled above.
        # This fallback is only for cases where dataset_path was not provided or dataset load failed.
        suggested_value = None
        confidence = 0.5
        actionable = False
        strategy = "default_imputation"
        rationale = ""
        
        # For Entity Type Code, if we have dataset_path but still reached here,
        # it means dataset load failed - use default_imputation
        if issue.column_name == "Entity Type Code" and dataset_path:
            logger.warning("Entity Type Code: Dataset load failed for row-by-row analysis - using default_imputation")
            rationale = f"Cannot determine Entity Type Code from related fields - dataset analysis failed."
        else:
            # For other columns or when dataset_path not provided, use aggregate logic
            org_name_metrics = dataset_profile.column_metrics.get(org_name_col)
            last_name_metrics = dataset_profile.column_metrics.get(last_name_col)
            first_name_metrics = dataset_profile.column_metrics.get(first_name_col)
            
            # Strategy 1: Check if Provider Organization Name has data → suggest "2" (Organization)
            # BUT: Only use this for non-Entity-Type-Code columns, or when dataset_path was not provided
            if issue.column_name != "Entity Type Code" and org_name_metrics and org_name_metrics.completeness_score > 50:
                suggested_value = "2"
                confidence = min(0.85, org_name_metrics.completeness_score / 100)
                strategy = "conditional_imputation"
                rationale = f"Provider Organization Name present ({org_name_metrics.completeness_score:.1f}% complete) → Entity Type Code = 2 (Organization)"
            
            # Strategy 2: Check if Provider Last Name or First Name has data → suggest "1" (Individual)
            elif issue.column_name != "Entity Type Code" and ((last_name_metrics and last_name_metrics.completeness_score > 50) or \
                 (first_name_metrics and first_name_metrics.completeness_score > 50)):
                suggested_value = "1"
                name_completeness = max(
                    last_name_metrics.completeness_score if last_name_metrics else 0,
                    first_name_metrics.completeness_score if first_name_metrics else 0
                )
                confidence = min(0.85, name_completeness / 100)
                strategy = "conditional_imputation"
                rationale = f"Provider Last/First Name present ({name_completeness:.1f}% complete) → Entity Type Code = 1 (Individual)"
            
            # Strategy 3: Use default value "1" (Individual) as fallback (only for non-Entity-Type-Code)
            # NOTE: Mode-based imputation is disabled - do not use mode to fill completeness values
            if issue.column_name != "Entity Type Code" and not suggested_value:
                suggested_value = "1"
                confidence = 0.6
                strategy = "default_imputation"
                rationale = "Using default Entity Type Code = 1 (Individual) - no clear pattern from related fields"
        
        # For Entity Type Code with dataset_path, if we reached here and no suggested_value,
        # it means we cannot determine the value - return None (no recommendation)
        if issue.column_name == "Entity Type Code" and dataset_path and not suggested_value:
            return None
        
        # Determine estimated impact
        # actionable flag is already set above based on whether Entity Type Code can be determined
        if actionable:
            estimated_impact = "low" if issue.percentage < 5 else "medium"
        else:
            estimated_impact = "high"
            if rationale:
                rationale += " - requires review due to low confidence"
        
        if not rationale:
            rationale = f"Missing Entity Type Code values - manual review recommended"
        
        return FixRecommendation(
            recommendation_id=str(uuid.uuid4()),
            issue_id=issue.issue_id,
            dataset_name=dataset_profile.dataset_name,
            column_name=issue.column_name,
            issue_type=issue.issue_type,
            fix_strategy=strategy,
            fix_description=f"Fill {issue.count} missing values in {issue.column_name}",
            confidence_score=confidence,
            estimated_impact=estimated_impact,
            actionable=actionable,
            suggested_value=suggested_value,
            rationale=rationale,
            prerequisites=[],
            risk_assessment=""
        )
    
    def _get_column_defaults(
        self, 
        column_name: str, 
        column_metrics: Optional[ColumnMetrics] = None,
        dataset_profile = None
    ) -> Optional[Any]:
        """
        Get default values for specific columns.
        
        Args:
            column_name: Name of the column
            column_metrics: Column metrics (optional, for context)
            dataset_profile: Dataset profile (optional, for context)
        """
        from datetime import datetime
        
        # Date fields - use current date or enumeration date
        if column_name == "Last Update Date":
            return datetime.now().strftime("%m/%d/%Y")
        
        if column_name == "Provider Enumeration Date":
            # Try to use a reasonable default (e.g., current date or a date from profile)
            # For safety, we'll use current date but with lower confidence
            return datetime.now().strftime("%m/%d/%Y")
        
        if column_name == "Certification Date":
            # Certification dates are specific - no safe default
            return None
        
        # Country Code - default to "US" if state is present (handled in main logic)
        if "Country Code" in column_name and "Address" in column_name:
            # This is handled in the main recommendation logic
            return None
        
        # For other fields, no safe defaults
        return None
    
    def _analyze_regex_violations(self, violations: List[str], rule, column_name: str) -> Tuple[List[str], int]:
        """Analyze regex violations to suggest fixes and count fixable ones."""
        import re
        suggestions = []
        fixable_count = 0
        pattern = rule.rule_config.get("pattern", "")
        
        for violation in violations:
            v_str = str(violation).strip()
            is_fixable = False
            
            # Check for common fixable issues
            # 1. Extra whitespace
            cleaned = re.sub(r'\s+', ' ', v_str)
            if cleaned != v_str and re.match(pattern, cleaned):
                suggestions.append("Remove extra whitespace")
                is_fixable = True
            
            # 2. For numeric patterns: remove non-digits
            if r'\d' in pattern:
                digits_only = re.sub(r'\D', '', v_str)
                if len(digits_only) > 0:
                    # Check column-specific fixes
                    if column_name == "NPI":
                        if len(digits_only) >= 10:
                            suggestions.append("Extract 10 digits from value")
                            is_fixable = True
                    elif "EIN" in column_name:
                        if len(digits_only) >= 9:
                            suggestions.append("Extract 9 digits from value")
                            is_fixable = True
                    elif re.match(pattern, digits_only):
                        suggestions.append("Remove non-digit characters")
                        is_fixable = True
            
            # 3. For alphanumeric patterns: normalize case
            if r'[A-Za-z]' in pattern:
                upper = v_str.upper()
                if re.match(pattern, upper):
                    suggestions.append("Normalize to uppercase")
                    is_fixable = True
            
            # 4. Remove special characters for certain patterns
            if "Taxonomy Code" in column_name or "License Number" in column_name:
                alphanum_only = re.sub(r'[^A-Za-z0-9]', '', v_str)
                if len(alphanum_only) > 0 and re.match(pattern, alphanum_only.upper()):
                    suggestions.append("Remove special characters")
                    is_fixable = True
            
            # 5. Check if simple trimming fixes it
            if re.match(pattern, v_str.strip()):
                suggestions.append("Trim whitespace")
                is_fixable = True
            
            if is_fixable:
                fixable_count += 1
        
        if not suggestions:
            suggestions = ["Check regex pattern", "Verify data source", "Apply format transformation"]
        
        return list(set(suggestions)), fixable_count
    
    def _analyze_length_violations(self, violations: List[str], rule) -> Tuple[List[str], int]:
        """Analyze length violations to suggest fixes."""
        suggestions = []
        fixable_count = 0
        min_len = rule.rule_config.get("min", 0)
        max_len = rule.rule_config.get("max", float('inf'))
        
        for violation in violations:
            v_str = str(violation).strip()
            vlen = len(v_str)
            is_fixable = False
            
            if vlen > max_len:
                suggestions.append(f"Truncate to {max_len} characters")
                is_fixable = True  # Truncation is safe
            elif vlen < min_len:
                # Can't fix too-short values without padding (requires domain knowledge)
                suggestions.append("Value too short - requires domain knowledge")
                is_fixable = False
            else:
                # Already valid length, might just need trimming
                if len(str(violation)) != vlen:
                    suggestions.append("Trim whitespace")
                    is_fixable = True
        
        if not suggestions:
            suggestions = ["Check length constraints"]
        
        return list(set(suggestions)), fixable_count
    
    def _infer_state_from_context(
        self,
        invalid_state: str,
        city_name: Optional[str] = None,
        postal_code: Optional[str] = None,
        country_code: Optional[str] = None,
        address_line1: Optional[str] = None,
        address_line2: Optional[str] = None,
        dataset_path: Optional[str] = None
    ) -> Optional[str]:
        """
        Minimal inference used only for generating recommendations in enum analysis.
        Maps full state/territory names to USPS codes; deeper row-level inference
        happens in the executor, not here.
        """
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
        inval = str(invalid_state).strip().upper()
        return state_name_to_code.get(inval)

    def _analyze_enum_violations(self, violations: List[str], rule, column_name: str, dataset_path: Optional[str] = None) -> Tuple[List[str], int]:
        """Analyze enum violations to suggest fixes and count fixable ones."""
        allowed = rule.rule_config.get("values", [])
        suggestions = []
        fixable_count = 0
        
        # Check if this is a state column that could benefit from context-based inference
        is_state_column = "State" in column_name and "Address" in column_name
        
        for violation in violations:
            v_str = str(violation).strip()
            is_fixable = False
            
            # Special handling for Entity Type Code
            if column_name == "Entity Type Code":
                try:
                    float_val = float(v_str)
                    if float_val.is_integer():
                        normalized = str(int(float_val))
                        if normalized in allowed:
                            suggestions.append("Normalize float to integer string (e.g., '1.0' -> '1')")
                            is_fixable = True
                            fixable_count += 1
                            continue
                except (ValueError, TypeError):
                    pass
            
            # Special handling for state columns: try state inference
            if is_state_column and dataset_path:
                # Try to infer state from context (will be done row-by-row in executor)
                inferred = self._infer_state_from_context(v_str)
                if inferred and inferred in allowed:
                    suggestions.append(f"Infer state from context (e.g., '{v_str}' -> '{inferred}')")
                    is_fixable = True
                    fixable_count += 1
                    continue
            
            # Try case-insensitive match
            for allowed_val in allowed:
                if v_str.lower() == allowed_val.lower():
                    if v_str != allowed_val:
                        suggestions.append(f"Normalize case (e.g., '{v_str}' -> '{allowed_val}')")
                        is_fixable = True
                        fixable_count += 1
                        break
                # Try uppercase/lowercase variants
                if v_str.upper() == allowed_val or v_str.lower() == allowed_val:
                    suggestions.append("Normalize case")
                    is_fixable = True
                    fixable_count += 1
                    break
            
            if not is_fixable:
                suggestions.append(f"Value must be one of: {', '.join(allowed)}")
        
        if not suggestions:
            suggestions = [f"Ensure value is one of: {', '.join(allowed)}"]
        
        return list(set(suggestions)), fixable_count
    
    def _analyze_phone_violations(self, violations: List[str], rule) -> Tuple[List[str], int]:
        """Analyze phone number violations to suggest fixes."""
        import re
        suggestions = []
        fixable_count = 0
        min_digits = rule.rule_config.get("min_digits", 7)
        max_digits = rule.rule_config.get("max_digits", 20)
        
        for violation in violations:
            v_str = str(violation)
            digits = re.sub(r'\D', '', v_str)
            digit_count = len(digits)
            
            if min_digits <= digit_count <= max_digits:
                suggestions.append("Remove non-digit characters")
                fixable_count += 1
            elif digit_count > max_digits:
                suggestions.append(f"Extract first {max_digits} digits")
                fixable_count += 1
        
        if not suggestions:
            suggestions = [f"Ensure phone has {min_digits}-{max_digits} digits"]
        
        return list(set(suggestions)), fixable_count
    
    def _analyze_date_violations(self, violations: List[str], rule) -> Tuple[List[str], int]:
        """Analyze date violations to suggest fixes."""
        from datetime import datetime
        suggestions = []
        fixable_count = 0
        
        date_formats = [
            "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d",
            "%d/%m/%Y", "%m.%d.%Y", "%Y.%m.%d"
        ]
        
        for violation in violations:
            v_str = str(violation).strip()
            for fmt in date_formats:
                try:
                    datetime.strptime(v_str, fmt)
                    suggestions.append(f"Convert date format to MM/DD/YYYY")
                    fixable_count += 1
                    break
                except ValueError:
                    continue
        
        if not suggestions:
            suggestions = ["Standardize date format to MM/DD/YYYY"]
        
        return list(set(suggestions)), fixable_count
