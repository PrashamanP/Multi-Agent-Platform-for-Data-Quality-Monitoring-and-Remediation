"""
Unit tests for the Fix Recommendation Agent.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock

from src.agents.fix_recommendation_agent import FixRecommendationAgent
from src.core.data_models import (
    Issue,
    Anomaly,
    ColumnMetrics,
    DatasetProfile,
    ProfileResults,
    ValidationRule,
)


@pytest.fixture
def validation_rules():
    """Sample validation rules."""
    return {
        "Entity Type Code": ValidationRule(
            rule_id="ENTITY_TYPE_FORMAT",
            column_name="Entity Type Code",
            rule_type="enum",
            rule_config={"values": ["1", "2"]},
            description="Entity Type Code must be either '1' or '2'"
        ),
        "Provider First Name": ValidationRule(
            rule_id="PROVIDER_FIRST_NAME_FORMAT",
            column_name="Provider First Name",
            rule_type="regex",
            rule_config={"pattern": "^[A-Za-z\\s\\-\\'\\.]{1,100}$"},
            description="Provider First Name must be alphabetic"
        )
    }


@pytest.fixture
def fix_agent(validation_rules):
    """Create a FixRecommendationAgent instance."""
    config = {
        "confidence_threshold": 0.75,
        "enable_auto_suggestions": True,
        "enable_llm_fixes": False
    }
    return FixRecommendationAgent(validation_rules=validation_rules, config=config)


@pytest.fixture
def sample_completeness_issue():
    """Sample completeness issue."""
    return Issue(
        issue_id="test-issue-1",
        column_name="Provider First Name",
        issue_type="completeness",
        count=10,
        percentage=5.0,
        description="Provider First Name has 10 null values",
        rule_violated="PROVIDER_FIRST_NAME_NOT_NULL"
    )


@pytest.fixture
def sample_conformity_issue():
    """Sample conformity issue."""
    return Issue(
        issue_id="test-issue-2",
        column_name="Provider First Name",
        issue_type="conformity",
        count=5,
        percentage=2.5,
        description="Provider First Name has 5 format violations",
        examples=["123", "!!!", "ABC123!"],
        rule_violated="PROVIDER_FIRST_NAME_FORMAT"
    )


@pytest.fixture
def sample_uniqueness_issue():
    """Sample uniqueness issue."""
    return Issue(
        issue_id="test-issue-3",
        column_name="NPI",
        issue_type="uniqueness",
        count=3,
        percentage=1.5,
        description="NPI field has 3 duplicate values",
        rule_violated="NPI_UNIQUE"
    )


@pytest.fixture
def sample_anomaly():
    """Sample anomaly."""
    return Anomaly(
        dataset_name="test_dataset",
        column_name="Provider First Name",
        metric="completeness",
        current_value=75.0,
        baseline_value=95.0,
        delta=-20.0,
        z_score=3.5,
        severity="high",
        detection_method="delta_zscore_composite",
        context={"baseline_mean": 95.0, "baseline_std": 2.0, "absolute_delta": 20.0}
    )


@pytest.fixture
def sample_column_metrics():
    """Sample column metrics."""
    return ColumnMetrics(
        column_name="Provider First Name",
        total_count=200,
        non_null_count=190,
        unique_count=180,
        duplicate_count=10,
        conforming_count=185,
        non_conforming_count=5,
        completeness_score=95.0,
        conformity_score=97.4,
        most_common_values=[("John", 15), ("Jane", 12), ("Robert", 10)]
    )


@pytest.fixture
def sample_dataset_profile(sample_column_metrics):
    """Sample dataset profile."""
    return DatasetProfile(
        dataset_name="test_dataset",
        total_rows=200,
        total_columns=50,
        profiling_timestamp=datetime.now(),
        column_metrics={"Provider First Name": sample_column_metrics},
        overall_completeness=95.0,
        overall_uniqueness=100.0,
        overall_conformity=98.0,
        processing_time_seconds=5.0,
        data_size_mb=2.5
    )


class TestFixRecommendationAgent:
    """Test suite for FixRecommendationAgent."""
    
    def test_agent_initialization(self, fix_agent):
        """Test agent initializes correctly."""
        assert fix_agent.agent_name == "fix_recommendation"
        assert fix_agent.config["confidence_threshold"] == 0.75
        assert len(fix_agent.validation_rules) > 0
    
    def test_execute_with_empty_input(self, fix_agent):
        """Test execute with no issues or anomalies."""
        results = fix_agent.execute(issues=[], anomalies=[])
        assert len(results.recommendations) == 0
        assert results.execution_metadata["issues_analyzed"] == 0
        assert results.execution_metadata["anomalies_analyzed"] == 0
    
    def test_recommend_completeness_fix(self, fix_agent, sample_completeness_issue, sample_column_metrics, sample_dataset_profile):
        """Test recommendation for completeness issues."""
        recommendation = fix_agent._recommend_completeness_fix(
            sample_completeness_issue, 
            sample_column_metrics, 
            sample_dataset_profile
        )
        
        assert recommendation is not None
        assert recommendation.issue_type == "completeness"
        assert recommendation.column_name == "Provider First Name"
        assert recommendation.confidence_score > 0
        assert recommendation.estimated_impact in ["low", "medium", "high"]
        assert len(recommendation.prerequisites) > 0
    
    def test_recommend_completeness_fix_simple(self, fix_agent, sample_completeness_issue):
        """Test simple completeness recommendation without full context."""
        recommendation = fix_agent._recommend_completeness_fix_simple(sample_completeness_issue)
        
        assert recommendation is not None
        assert recommendation.issue_type == "completeness"
        assert recommendation.confidence_score > 0
    
    def test_recommend_conformity_fix(self, fix_agent, sample_conformity_issue, sample_column_metrics):
        """Test recommendation for conformity issues."""
        recommendation = fix_agent._recommend_conformity_fix(sample_conformity_issue, sample_column_metrics)
        
        assert recommendation is not None
        assert recommendation.issue_type == "conformity"
        assert recommendation.column_name == "Provider First Name"
        assert recommendation.confidence_score > 0
        assert "format" in recommendation.fix_strategy.lower()
    
    def test_recommend_conformity_fix_simple(self, fix_agent, sample_conformity_issue):
        """Test simple conformity recommendation without full context."""
        recommendation = fix_agent._recommend_conformity_fix_simple(sample_conformity_issue)
        
        assert recommendation is not None
        assert recommendation.issue_type == "conformity"
        assert recommendation.confidence_score > 0
    
    def test_recommend_uniqueness_fix(self, fix_agent, sample_uniqueness_issue, sample_column_metrics):
        """Test recommendation for uniqueness issues."""
        recommendation = fix_agent._recommend_uniqueness_fix(sample_uniqueness_issue, sample_column_metrics)
        
        assert recommendation is not None
        assert recommendation.issue_type == "uniqueness"
        assert recommendation.column_name == "NPI"
        assert recommendation.confidence_score > 0
        assert "duplicate" in recommendation.fix_strategy.lower() or "duplicate" in recommendation.fix_description.lower()
    
    def test_recommend_uniqueness_fix_simple(self, fix_agent, sample_uniqueness_issue):
        """Test simple uniqueness recommendation without full context."""
        recommendation = fix_agent._recommend_uniqueness_fix_simple(sample_uniqueness_issue)
        
        assert recommendation is not None
        assert recommendation.issue_type == "uniqueness"
        assert recommendation.confidence_score > 0
    
    def test_recommend_for_anomaly(self, fix_agent, sample_anomaly):
        """Test recommendation for anomalies."""
        recommendation = fix_agent._recommend_for_anomaly(sample_anomaly)
        
        assert recommendation is not None
        assert recommendation.issue_type == "anomaly"
        assert recommendation.column_name == "Provider First Name"
        assert recommendation.confidence_score > 0
        assert "investigate" in recommendation.fix_strategy.lower() or "investigate" in recommendation.rationale.lower()
    
    def test_execute_with_profile_results(self, fix_agent, sample_completeness_issue, sample_dataset_profile):
        """Test execute with profile results."""
        # Create profile results
        profile_results = ProfileResults(
            dataset_profile=sample_dataset_profile,
            issues=[sample_completeness_issue],
            execution_metadata={"run_id": "test-run-123"}
        )
        
        results = fix_agent.execute(profile_results=profile_results)
        
        assert len(results.recommendations) > 0
        assert results.dataset_name == "test_dataset"
        assert results.run_id == "test-run-123"
        assert results.execution_metadata["issues_analyzed"] > 0
    
    def test_execute_with_anomaly_results(self, fix_agent, sample_anomaly):
        """Test execute with anomaly results."""
        from src.core.data_models import AnomalyDetectionResults
        
        anomaly_results = AnomalyDetectionResults(
            dataset_name="test_dataset",
            run_id="test-run-456",
            anomalies=[sample_anomaly],
            execution_metadata={}
        )
        
        results = fix_agent.execute(anomaly_results=anomaly_results)
        
        assert len(results.recommendations) > 0
        assert results.execution_metadata["anomalies_analyzed"] > 0
    
    def test_actionable_count_property(self, fix_agent):
        """Test actionable count property in results."""
        from src.core.data_models import FixRecommendationResults, FixRecommendation
        
        recommendations = [
            FixRecommendation(
                recommendation_id="rec-1",
                issue_id="issue-1",
                dataset_name="test",
                column_name="col1",
                issue_type="completeness",
                fix_strategy="imputation",
                fix_description="Fix",
                confidence_score=0.9,
                estimated_impact="low",
                actionable=True,
                rationale="Test"
            ),
            FixRecommendation(
                recommendation_id="rec-2",
                issue_id="issue-2",
                dataset_name="test",
                column_name="col2",
                issue_type="conformity",
                fix_strategy="format_correction",
                fix_description="Fix",
                confidence_score=0.6,
                estimated_impact="high",
                actionable=False,
                rationale="Test"
            )
        ]
        
        results = FixRecommendationResults(
            dataset_name="test",
            run_id="test-run",
            recommendations=recommendations
        )
        
        assert results.actionable_count == 1
        assert results.requires_review_count == 1
    
    def test_get_column_defaults(self, fix_agent):
        """Test getting default values for columns."""
        default = fix_agent._get_column_defaults("Entity Type Code")
        assert default == "1"
        
        default = fix_agent._get_column_defaults("NonExistent Column")
        assert default is None
    
    def test_analyze_length_violations(self, fix_agent):
        """Test analyzing length violations."""
        rule = ValidationRule(
            rule_id="TEST_LENGTH",
            column_name="Test Column",
            rule_type="length",
            rule_config={"min": 2, "max": 10},
            description="Test"
        )
        
        violations = ["a", "this_is_too_long"]
        suggestions = fix_agent._analyze_length_violations(violations, rule)
        
        assert len(suggestions) > 0
        assert any("short" in s.lower() for s in suggestions)
        assert any("long" in s.lower() for s in suggestions)
    
    def test_analyze_enum_violations(self, fix_agent):
        """Test analyzing enum violations."""
        rule = ValidationRule(
            rule_id="TEST_ENUM",
            column_name="Test Column",
            rule_type="enum",
            rule_config={"values": ["A", "B", "C"]},
            description="Test"
        )
        
        violations = ["X", "Y", "Z"]
        suggestions = fix_agent._analyze_enum_violations(violations, rule)
        
        assert len(suggestions) > 0
        assert "A" in suggestions[0] or "B" in suggestions[0] or "C" in suggestions[0]
