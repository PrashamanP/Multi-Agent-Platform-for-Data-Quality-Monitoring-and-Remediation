"""
Comprehensive test module for the ProfilerAgent.
"""

import pytest
import pandas as pd
import numpy as np
import tempfile
import time
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.agents.profiler_agent import ProfilerAgent
from src.core.data_models import ValidationRule, ProfileResults
from src.core.validation_rule_loader import ValidationRuleLoader
from src.data_access.file_handler import FileHandler


class TestProfilerAgent:
    """Test suite for ProfilerAgent."""
    
    @pytest.fixture
    def sample_npi_data(self):
        """Create sample NPI data for testing."""
        return pd.DataFrame({
            'NPI': [
                '1234567890',  # Valid
                '9876543210',  # Valid
                '123456789',   # Invalid - 9 digits
                None,          # Null
                '12345678ab'   # Invalid - contains letters
            ],
            'Entity Type Code': [
                '1',           # Valid
                '2',           # Valid
                '3',           # Invalid
                None,          # Null
                '1'            # Valid
            ],
            'Provider Organization Name (Legal Business Name)': [
                'ABC Medical Center',      # Valid
                'XYZ Healthcare',          # Valid
                None,                      # Null
                'A',                       # Invalid - too short
                'Valid Organization Name'   # Valid
            ],
            'Provider Last Name (Legal Name)': [
                'Smith',       # Valid
                "O'Connor",    # Valid with apostrophe
                'Smith123',    # Invalid - contains numbers
                None,          # Null
                'Johnson'      # Valid
            ],
            'Provider Enumeration Date': [
                '2020-01-15',  # Valid
                '2021-06-30',  # Valid
                '2025-12-01',  # Invalid - future date
                None,          # Null
                '2019-03-22'   # Valid
            ],
            'Provider Sex Code': [
                'M',           # Valid
                'F',           # Valid
                'X',           # Invalid
                None,          # Null
                'M'            # Valid
            ]
        })
    
    @pytest.fixture
    def profiler_agent(self):
        """Create ProfilerAgent instance for testing."""
        return ProfilerAgent()
    
    @pytest.fixture
    def temp_csv_file(self, sample_npi_data):
        """Create temporary CSV file with sample data."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            sample_npi_data.to_csv(f.name, index=False)
            yield f.name
        os.unlink(f.name)
    
    def test_init_default_rules(self):
        """Test ProfilerAgent initialization with default NPI rules."""
        agent = ProfilerAgent()
        
        assert agent.validation_rules is not None
        assert len(agent.validation_rules) > 0
        assert 'NPI' in agent.validation_rules
        assert 'Entity Type Code' in agent.validation_rules
        
        # Test that field categories are loaded correctly
        assert len(agent.universal_required_fields) > 0
        
        # Test that universal required fields include expected values
        assert 'NPI' in agent.universal_required_fields
        assert 'Entity Type Code' in agent.universal_required_fields
    
    def test_init_custom_rules(self):
        """Test ProfilerAgent initialization with custom rules."""
        custom_rules = {
            'test_field': ValidationRule(
                rule_id="TEST_RULE",
                column_name="test_field",
                rule_type="regex",
                rule_config={"pattern": r"^\d+$"},
                description="Test rule"
            )
        }
        
        agent = ProfilerAgent(validation_rules=custom_rules)
        assert agent.validation_rules == custom_rules
        assert 'test_field' in agent.validation_rules
    
    def test_init_with_custom_config(self):
        """Test ProfilerAgent initialization with custom configuration."""
        custom_config = {
            'enable_sampling': True,
            'sample_size': 50000,
            'max_violation_examples': 5,
            'parallel_processing': False
        }
        
        agent = ProfilerAgent(config=custom_config)
        assert agent.enable_sampling == True
        assert agent.sample_size == 50000
        assert agent.max_violation_examples == 5
    
    def test_init_with_invalid_config(self):
        """Test ProfilerAgent handles invalid configuration gracefully."""
        # Should not crash with None or empty config
        agent = ProfilerAgent(config=None)
        assert agent.config is not None
        
        agent = ProfilerAgent(config={})
        assert agent.config is not None
    
    def test_init_missing_validation_rules(self):
        """Test ProfilerAgent initialization when validation rules fail to load."""
        with patch('src.agents.profiler_agent.ValidationRuleLoader') as mock_loader:
            mock_loader_instance = MagicMock()
            mock_loader_instance.load_validation_rules.return_value = {}
            mock_loader.return_value = mock_loader_instance
            
            agent = ProfilerAgent()
            assert agent.validation_rules == {}
            assert len(agent.universal_required_fields) > 0  # Should still have defaults
    
    def test_load_dataset_success(self, profiler_agent, sample_npi_data, temp_csv_file):
        """Test standard execution uses FileHandler to load dataset."""
        with patch('src.data_access.file_handler.FileHandler.load_dataset', return_value=sample_npi_data) as mock_load:
            results = profiler_agent._execute_standard(temp_csv_file)
        
        assert mock_load.call_count == 1
        assert results.dataset_profile.total_rows == len(sample_npi_data)
        assert 'NPI' in results.dataset_profile.column_metrics
    
    def test_load_dataset_file_not_found(self, profiler_agent):
        """Test standard execution propagates FileNotFoundError."""
        with patch('src.data_access.file_handler.FileHandler.load_dataset', side_effect=FileNotFoundError("missing")):
            with pytest.raises(FileNotFoundError):
                profiler_agent._execute_standard('/path/that/does/not/exist.csv')
    
    def test_load_dataset_encoding_fallback(self, sample_npi_data):
        """Test dataset loading with encoding fallback via FileHandler."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='latin1') as f:
            sample_npi_data.to_csv(f.name, index=False)
            temp_file = f.name
        
        try:
            with patch('src.data_access.file_handler.pd.read_csv') as mock_read_csv:
                mock_read_csv.side_effect = [
                    UnicodeDecodeError('utf-8', b'', 0, 1, 'invalid start byte'),
                    sample_npi_data
                ]
                
                df = FileHandler.load_dataset(temp_file, focused_columns_only=False)
                
                assert isinstance(df, pd.DataFrame)
                assert mock_read_csv.call_count == 2
                assert mock_read_csv.call_args_list[1][1]['encoding'] == 'latin1'
        finally:
            os.unlink(temp_file)
    
    def test_profile_column_completeness_universal(self, profiler_agent):
        """Test column completeness calculation for universal required fields."""
        test_series = pd.Series([1, 2, None, 4, None])
        
        metrics = profiler_agent._profile_column(
            pd.DataFrame({'NPI': test_series}), 
            'NPI'
        )
        
        assert metrics.column_name == 'NPI'
        assert metrics.total_count == 5
        assert metrics.non_null_count == 3
        assert metrics.null_count == 2
        assert metrics.completeness_score == 60.0
    
    def test_profile_column_completeness_conditional_individual(self, profiler_agent):
        """Test column completeness calculation for conditional fields (Individual)."""
        df = pd.DataFrame({
            'Entity Type Code': ['1', '1', '1', '1', '1'],
            'Provider Last Name (Legal Name)': ['Smith', 'Jones', None, 'Brown', None]
        })
        
        metrics = profiler_agent._profile_column(df, 'Provider Last Name (Legal Name)')
        
        # For Entity Type Code = 1, Provider Last Name (Legal Name) is required
        # 3 out of 5 are not null, so completeness should be 60%
        assert metrics.completeness_score == 60.0
    
    def test_profile_column_completeness_conditional_organization(self, profiler_agent):
        """Test column completeness calculation for conditional fields (Organization)."""
        df = pd.DataFrame({
            'Entity Type Code': ['2', '2', '2', '2', '2'],
            'Provider Organization Name (Legal Business Name)': ['ABC Corp', 'XYZ Inc', None, 'DEF LLC', None]
        })
        
        metrics = profiler_agent._profile_column(df, 'Provider Organization Name (Legal Business Name)')
        
        # For Entity Type Code = 2, Provider Organization Name (Legal Business Name) is required
        # 3 out of 5 are not null, so completeness should be 60%
        assert metrics.completeness_score == 60.0
    
    def test_profile_column_completeness_conditional_optional(self, profiler_agent):
        """Test column completeness calculation for optional fields."""
        df = pd.DataFrame({
            'Entity Type Code': ['1', '1', '1', '1', '1'],
            'Provider Organization Name (Legal Business Name)': ['ABC Corp', None, None, None, None]
        })
        
        metrics = profiler_agent._profile_column(df, 'Provider Organization Name (Legal Business Name)')
        
        # For Entity Type Code = 1, Provider Organization Name (Legal Business Name) is optional
        # All rows should be considered valid regardless of null values
        assert metrics.completeness_score == 100.0
    
    def test_profile_column_uniqueness(self, profiler_agent):
        """Test column uniqueness calculation."""
        test_series = pd.Series([1, 2, 2, 3, 3])
        
        metrics = profiler_agent._profile_column(
            pd.DataFrame({'NPI': test_series}), 
            'NPI'
        )
        
        assert metrics.unique_count == 3
        assert metrics.duplicate_count == 2
        assert metrics.uniqueness_score == 60.0
    
    def test_profile_column_conformity_valid(self, profiler_agent):
        """Test column conformity with valid NPI values."""
        test_series = pd.Series(['1234567890', '9876543210', '1111111111'])
        
        metrics = profiler_agent._profile_column(
            pd.DataFrame({'NPI': test_series}), 
            'NPI'
        )
        
        assert metrics.conforming_count == 3
        assert metrics.non_conforming_count == 0
        assert metrics.conformity_score == 100.0
        assert len(metrics.conformity_violations) == 0
    
    def test_profile_column_conformity_invalid(self, profiler_agent):
        """Test column conformity with invalid NPI values."""
        test_series = pd.Series(['1234567890', '123456789', '12345678ab', None])
        
        metrics = profiler_agent._profile_column(
            pd.DataFrame({'NPI': test_series}), 
            'NPI'
        )
        
        assert metrics.conforming_count == 1
        assert metrics.non_conforming_count == 2  # Excludes null
        assert metrics.conformity_score == pytest.approx(33.3333, rel=1e-3)
        assert '123456789' in metrics.conformity_violations
        assert '12345678ab' in metrics.conformity_violations
    
    def test_profile_column_no_rule(self, profiler_agent):
        """Test column profiling when no validation rule exists."""
        test_series = pd.Series(['value1', 'value2', None])
        
        metrics = profiler_agent._profile_column(
            pd.DataFrame({'unknown_col': test_series}), 
            'unknown_col'
        )
        
        # Should treat all non-null values as conforming
        assert metrics.conforming_count == 2
        assert metrics.non_conforming_count == 0
        assert metrics.conformity_score == 100.0
    
    def test_profile_column_string_lengths(self, profiler_agent):
        """Test string length analysis for text columns."""
        test_series = pd.Series(['a', 'abc', 'abcde', None])
        
        metrics = profiler_agent._profile_column(
            pd.DataFrame({'text_col': test_series}), 
            'text_col'
        )
        
        assert metrics.min_length == 1
        assert metrics.max_length == 5
        assert metrics.avg_length == 3.0
    
    def test_profile_column_most_common_values(self, profiler_agent):
        """Test most common values tracking."""
        test_series = pd.Series(['A', 'B', 'A', 'C', 'A', 'B'])
        
        metrics = profiler_agent._profile_column(
            pd.DataFrame({'test_col': test_series}), 
            'test_col'
        )
        
        assert len(metrics.most_common_values) > 0
        # Most common should be 'A' with count 3
        assert metrics.most_common_values[0] == ('A', 3)
        assert metrics.most_common_values[1] == ('B', 2)
    
    def test_check_conformity_npi_rule(self, profiler_agent):
        """Test conformity checking for NPI field."""
        test_series = pd.Series([
            '1234567890',  # Valid
            '123456789',   # Invalid - 9 digits
            '12345678ab',  # Invalid - contains letters
            None           # Null - excluded
        ])
        
        conforming, non_conforming, violations = profiler_agent._check_conformity(
            test_series, 'NPI'
        )
        
        assert conforming == 1
        assert non_conforming == 2
        assert '123456789' in violations
        assert '12345678ab' in violations
    
    def test_calculate_conditional_completeness(self, profiler_agent):
        """Test conditional completeness calculation."""
        # Test data with mixed Entity Type Codes
        df = pd.DataFrame({
            'Entity Type Code': ['1', '1', '2', '2', '1'],
            'Provider Last Name (Legal Name)': ['Smith', None, 'Jones', 'Brown', 'Wilson'],
            'Provider Organization Name (Legal Business Name)': [None, None, 'ABC Corp', None, None]
        })
        
        # Test Provider Last Name (Legal Name) (required for Entity Type 1)
        last_name_completeness = profiler_agent._calculate_conditional_completeness(df, 'Provider Last Name (Legal Name)')
        # Entity Type 1: 2 out of 3 have values (Smith, Wilson), Entity Type 2: 2 out of 2 are optional
        # Total: 4 out of 5 = 80%
        assert last_name_completeness == 80.0
        
        # Test Provider Organization Name (Legal Business Name) (required for Entity Type 2)
        org_name_completeness = profiler_agent._calculate_conditional_completeness(df, 'Provider Organization Name (Legal Business Name)')
        # Entity Type 1: 3 out of 3 are optional, Entity Type 2: 1 out of 2 have values (ABC Corp)
        # Total: 4 out of 5 = 80%
        assert org_name_completeness == 80.0
        
        # Test universal required field
        df['NPI'] = ['1234567890', None, '9876543210', '1111111111', '2222222222']
        npi_completeness = profiler_agent._calculate_conditional_completeness(df, 'NPI')
        # 4 out of 5 are not null = 80%
        assert npi_completeness == 80.0
    
    def test_check_conformity_enum_rule(self, profiler_agent):
        """Test conformity checking for enum field."""
        test_series = pd.Series(['1', '2', '3', '1', None])
        
        conforming, non_conforming, violations = profiler_agent._check_conformity(
            test_series, 'Entity Type Code'
        )
        
        assert conforming == 3  # Two '1's and one '2'
        assert non_conforming == 1  # One '3'
        assert '3' in violations
    
    def test_detect_issues_completeness_universal(self, profiler_agent, sample_npi_data):
        """Test detection of universal completeness issues."""
        profile = profiler_agent._profile_dataset(sample_npi_data, 'test.csv')
        issues = profiler_agent._detect_issues(sample_npi_data, profile)
        
        # Should detect issues for universal required fields with nulls
        completeness_issues = [i for i in issues if i.issue_type == 'completeness']
        assert len(completeness_issues) > 0
        
        # NPI is a universal required field with nulls - should be critical
        npi_issues = [i for i in completeness_issues if i.column_name == 'NPI']
        assert len(npi_issues) > 0
        # Removed severity assertion - severity indicators have been removed
    
    def test_detect_issues_completeness_conditional(self, profiler_agent):
        """Test detection of conditional completeness issues."""
        # Create test data with mixed Entity Type Codes
        test_data = pd.DataFrame({
            'NPI': ['1234567890', '9876543210', '1111111111', '2222222222'],
            'Entity Type Code': ['1', '1', '2', '2'],
            'Provider Last Name (Legal Name)': ['Smith', None, 'Jones', None],  # Required for Entity Type 1
            'Provider First Name': ['John', 'Jane', None, None],  # Required for Entity Type 1
            'Provider Organization Name (Legal Business Name)': [None, None, 'ABC Corp', None]  # Required for Entity Type 2
        })
        
        profile = profiler_agent._profile_dataset(test_data, 'test.csv')
        issues = profiler_agent._detect_issues(test_data, profile)
        
        # Should detect conditional completeness issues
        completeness_issues = [i for i in issues if i.issue_type == 'completeness']
        
        # Check for Provider Last Name (Legal Name) issue for Entity Type 1
        last_name_issues = [i for i in completeness_issues if i.column_name == 'Provider Last Name (Legal Name)']
        assert len(last_name_issues) > 0
        
        # Check for Provider Organization Name (Legal Business Name) issue for Entity Type 2
        org_name_issues = [i for i in completeness_issues if i.column_name == 'Provider Organization Name (Legal Business Name)']
        assert len(org_name_issues) > 0
    
    def test_detect_issues_conformity(self, profiler_agent, sample_npi_data):
        """Test detection of conformity issues."""
        profile = profiler_agent._profile_dataset(sample_npi_data, 'test.csv')
        issues = profiler_agent._detect_issues(sample_npi_data, profile)
        
        # Should detect conformity issues
        conformity_issues = [i for i in issues if i.issue_type == 'conformity']
        assert len(conformity_issues) > 0
        
        # Check NPI conformity issues
        npi_conformity_issues = [i for i in conformity_issues if i.column_name == 'NPI']
        assert len(npi_conformity_issues) > 0
        assert npi_conformity_issues[0].count == 2  # Two invalid NPIs
    
    def test_detect_issues_uniqueness(self, profiler_agent):
        """Test detection of uniqueness issues for NPI."""
        df_with_duplicates = pd.DataFrame({
            'NPI': ['1234567890', '1234567890', '9876543210']  # One duplicate
        })
        
        profile = profiler_agent._profile_dataset(df_with_duplicates, 'test.csv')
        issues = profiler_agent._detect_issues(df_with_duplicates, profile)
        
        uniqueness_issues = [i for i in issues if i.issue_type == 'uniqueness' and i.column_name == 'NPI']
        assert len(uniqueness_issues) > 0
        # Removed severity assertion - severity indicators have been removed
        assert uniqueness_issues[0].count == 1  # One duplicate value
    
    def test_detect_issues_dataset_level(self, profiler_agent):
        """Test detection of dataset-level issues."""
        # Create dataset with poor overall completeness
        poor_quality_data = pd.DataFrame({
            'NPI': [None, None, '1234567890', None, None],
            'Entity Type Code': [None, None, '1', None, None],
            'Provider Organization Name (Legal Business Name)': [None, None, 'Test Org', None, None]
        })
        
        profile = profiler_agent._profile_dataset(poor_quality_data, 'test.csv')
        issues = profiler_agent._detect_issues(poor_quality_data, profile)
        
        # Should detect overall completeness issue
        dataset_issues = [i for i in issues if i.column_name == '__DATASET__']
        completeness_issues = [i for i in dataset_issues if i.issue_type == 'completeness']
        assert len(completeness_issues) > 0
    
    def test_execute_full_workflow(self, profiler_agent, temp_csv_file):
        """Test complete profiler execution workflow."""
        results = profiler_agent.execute(temp_csv_file)
        
        assert isinstance(results, ProfileResults)
        assert results.dataset_profile is not None
        assert len(results.issues) > 0
        assert results.execution_metadata is not None
        
        # Check dataset profile
        profile = results.dataset_profile
        assert profile.total_rows == 5
        assert profile.total_columns == 5
        assert 'NPI' in profile.column_metrics
        
        # Check execution metadata
        assert 'execution_time_seconds' in results.execution_metadata
        assert 'agent_version' in results.execution_metadata
        assert 'rules_applied' in results.execution_metadata
    
    def test_overall_uniqueness_npi_only(self, profiler_agent):
        """Test that overall uniqueness only considers NPI column."""
        # Create test data with duplicates in non-NPI columns but unique NPI
        test_data = pd.DataFrame({
            'NPI': ['1234567890', '9876543210', '1111111111'],  # All unique
            'Entity Type Code': ['1', '1', '1'],  # Required for conditional completeness
            'Provider Last Name (Legal Name)': ['Smith', 'Smith', 'Smith'],  # All duplicates
            'Provider First Name': ['John', 'John', 'John']      # All duplicates
        })
        
        profile = profiler_agent._profile_dataset(test_data, 'test.csv')
        
        # Overall uniqueness should be 100% because NPI is unique
        # Even though other columns have duplicates
        assert profile.overall_uniqueness == 100.0
        
        # Verify individual column metrics
        assert profile.column_metrics['NPI'].uniqueness_score == 100.0
        assert profile.column_metrics['Provider Last Name (Legal Name)'].uniqueness_score is None  # Should be None for non-NPI columns
        assert profile.column_metrics['Provider First Name'].uniqueness_score is None  # Should be None for non-NPI columns
    
    def test_overall_uniqueness_no_npi_column(self, profiler_agent):
        """Test overall uniqueness when NPI column is not present."""
        test_data = pd.DataFrame({
            'Entity Type Code': ['1', '1', '1'],  # Required for conditional completeness
            'Provider Last Name (Legal Name)': ['Smith', 'Smith', 'Smith'],  # All duplicates
            'Provider First Name': ['John', 'John', 'John']      # All duplicates
        })
        
        profile = profiler_agent._profile_dataset(test_data, 'test.csv')
        
        # Overall uniqueness should default to 100% when NPI column is not present
        assert profile.overall_uniqueness == 100.0
        
        # Verify that all columns have None uniqueness_score when NPI is not present
        for col_name, metrics in profile.column_metrics.items():
            assert metrics.uniqueness_score is None
    
    def test_execute_file_not_found(self, profiler_agent):
        """Test execute with non-existent file."""
        with pytest.raises(FileNotFoundError):
            profiler_agent.execute('/path/that/does/not/exist.csv')
    
    @patch('src.agents.profiler_agent.logger')
    def test_execute_with_logging(self, mock_logger, profiler_agent, temp_csv_file):
        """Test that execute method logs appropriately."""
        results = profiler_agent.execute(temp_csv_file)
        
        assert results is not None
        mock_logger.info.assert_called()
        
        # Check that logging calls were made
        log_calls = [call.args[0] for call in mock_logger.info.call_args_list]
        assert any('Starting profiling' in call for call in log_calls)
        assert any('Profiling completed' in call for call in log_calls)
    
    def test_profile_results_methods(self, profiler_agent, temp_csv_file):
        """Test ProfileResults helper methods."""
        results = profiler_agent.execute(temp_csv_file)
        
        # Test get_issues_by_severity
        # Removed severity filtering - using all issues instead
        all_issues = results.issues
        assert isinstance(all_issues, list)
        
        # Test get_critical_issues
        critical_issues = results.dataset_profile.get_critical_issues()
        assert isinstance(critical_issues, list)
        
        # Test summary_stats
        summary = results.summary_stats()
        assert 'total_rows' in summary
        assert 'total_columns' in summary
        assert 'overall_completeness' in summary
        assert 'overall_conformity' in summary
        assert 'total_issues' in summary
    
    def test_large_dataset_performance(self, profiler_agent):
        """Test profiler performance with larger dataset."""
        # Create larger test dataset
        large_data = pd.DataFrame({
            'NPI': ['1234567890'] * 1000 + ['invalid'] * 100,
            'Entity Type Code': ['1'] * 500 + ['2'] * 500 + ['3'] * 100,
            'Provider Organization Name (Legal Business Name)': ['Test Org'] * 1100
        })
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            large_data.to_csv(f.name, index=False)
            temp_file = f.name
        
        try:
            start_time = time.time()
            results = profiler_agent.execute(temp_file)
            execution_time = time.time() - start_time
            
            # Should complete in reasonable time (less than 10 seconds)
            assert execution_time < 10.0
            assert results.dataset_profile.total_rows == 1100
            
        finally:
            os.unlink(temp_file)
    
    def test_chunked_processing_enabled(self):
        """Test profiler with chunked processing configuration."""
        config = {
            'enable_chunked_processing': True,
            'chunk_size': 1000,
            'chunked_processing_threshold_mb': 1
        }
        
        agent = ProfilerAgent(config=config)
        assert agent.enable_chunked_processing == True
        assert agent.chunk_size == 1000
        assert agent.chunked_processing_threshold_mb == 1
    
    def test_parallel_processing_config(self):
        """Test profiler with parallel processing configuration."""
        config = {
            'parallel_processing': True,
            'max_workers': 8
        }
        
        agent = ProfilerAgent(config=config)
        assert agent.parallel_processing == True
        assert agent.max_workers == 8
    
    def test_sampling_configuration(self):
        """Test profiler with sampling enabled for large datasets."""
        config = {
            'enable_sampling': True,
            'sample_size': 25000
        }
        
        agent = ProfilerAgent(config=config)
        assert agent.enable_sampling == True
        assert agent.sample_size == 25000
    
    def test_memory_optimization_config(self):
        """Test profiler with memory optimization settings."""
        config = {
            'optimize_dtypes': True,
            'enable_progress_tracking': False
        }
        
        agent = ProfilerAgent(config=config)
        assert agent.optimize_dtypes == True
        assert agent.enable_progress_tracking == False
    
    def test_sampling_large_dataset_simulation(self, profiler_agent):
        """Test that sampling works correctly when enabled."""
        # Create large dataset simulation
        large_data = pd.DataFrame({
            'NPI': ['1234567890'] * 1000,
            'Entity Type Code': ['1'] * 1000,
            'Provider Organization Name (Legal Business Name)': ['Test Org'] * 1000
        })
        
        # Enable sampling with small sample size
        profiler_agent.enable_sampling = True
        profiler_agent.sample_size = 100
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            large_data.to_csv(f.name, index=False)
            temp_file = f.name
        
        try:
            # Should complete quickly due to sampling
            start_time = time.time()
            results = profiler_agent.execute(temp_file)
            execution_time = time.time() - start_time
            
            # Should be fast due to sampling
            assert execution_time < 5.0
            assert results.dataset_profile.total_rows <= profiler_agent.sample_size
            
        finally:
            os.unlink(temp_file)
    
    def test_empty_dataset(self, profiler_agent):
        """Test profiler behavior with empty dataset."""
        empty_data = pd.DataFrame({
            'NPI': [],
            'Entity Type Code': []
        })
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            empty_data.to_csv(f.name, index=False)
            temp_file = f.name
        
        try:
            results = profiler_agent.execute(temp_file)
            
            assert results.dataset_profile.total_rows == 0
            assert results.dataset_profile.overall_completeness == 0
            
        finally:
            os.unlink(temp_file)


# Additional test fixtures and utilities

@pytest.fixture
def npi_test_data_generator():
    """Generate various types of NPI test data."""
    
    def generate_data(scenario='mixed'):
        if scenario == 'perfect':
            return pd.DataFrame({
                'NPI': ['1234567890', '9876543210', '1111111111'],
                'Entity Type Code': ['1', '2', '1'],
                'Provider Organization Name (Legal Business Name)': ['ABC Medical', 'XYZ Healthcare', 'Test Clinic']
            })
        
        elif scenario == 'all_invalid':
            return pd.DataFrame({
                'NPI': ['123456789', '12345678ab', ''],
                'Entity Type Code': ['3', 'X', ''],
                'Provider Organization Name (Legal Business Name)': ['', 'A', '']
            })
        
        elif scenario == 'mixed':
            return pd.DataFrame({
                'NPI': ['1234567890', '123456789', None],
                'Entity Type Code': ['1', '3', None],
                'Provider Organization Name (Legal Business Name)': ['Valid Org', 'A', None]
            })
    
    return generate_data


class TestNPISpecificValidation:
    """Tests specifically for NPI dataset validation rules."""
    
    def test_npi_format_validation(self):
        """Test NPI format validation specifically."""
        rule_loader = ValidationRuleLoader()
        rules = rule_loader.load_validation_rules()
        rule = rules['NPI']
        
        # Valid NPIs
        assert rule.validate_value('1234567890') == True
        assert rule.validate_value('0000000000') == True
        assert rule.validate_value('9999999999') == True
        
        # Invalid NPIs
        assert rule.validate_value('123456789') == False   # 9 digits
        assert rule.validate_value('12345678901') == False # 11 digits
        assert rule.validate_value('12345678ab') == False  # contains letters
        assert rule.validate_value('123-456-7890') == False # contains hyphens
        assert rule.validate_value(None) == False          # null
        assert rule.validate_value('') == False            # empty string
    
    def test_entity_type_validation(self):
        """Test Entity Type Code validation."""
        rule_loader = ValidationRuleLoader()
        rules = rule_loader.load_validation_rules()
        rule = rules['Entity Type Code']
        
        # Valid values
        assert rule.validate_value('1') == True
        assert rule.validate_value('2') == True
        
        # Invalid values
        assert rule.validate_value('3') == False
        assert rule.validate_value('0') == False
        assert rule.validate_value('A') == False
        assert rule.validate_value(None) == False
        assert rule.validate_value('') == False
    
    def test_phone_number_validation(self):
        """Test phone number validation."""
        rule_loader = ValidationRuleLoader()
        rules = rule_loader.load_validation_rules()
        rule = rules['Provider Business Mailing Address Telephone Number']
        
        # Valid phone numbers (various formats)
        assert rule.validate_value('1234567890') == True      # 10 digits
        assert rule.validate_value('(123) 456-7890') == True  # formatted
        assert rule.validate_value('123-456-7890') == True    # dashed
        assert rule.validate_value('12345678901234567890') == True  # 20 digits (max)
        
        # Invalid phone numbers
        assert rule.validate_value('123456') == False         # too short
        assert rule.validate_value('123456789012345678901') == False  # too long
        assert rule.validate_value('abcdefghij') == False     # no digits
        assert rule.validate_value(None) == False
        assert rule.validate_value('') == False
    
    def test_date_validation_rules(self):
        """Test date validation rules."""
        rule_loader = ValidationRuleLoader()
        rules = rule_loader.load_validation_rules()
        rule = rules['Provider Enumeration Date']
        
        # Valid dates (not in future)
        assert rule.validate_value('2020-01-15') == True
        assert rule.validate_value('2021-12-31') == True
        assert rule.validate_value('2019-06-30') == True
        
        # Invalid dates
        assert rule.validate_value('2025-12-31') == False     # future date
        assert rule.validate_value('invalid-date') == False   # invalid format
        assert rule.validate_value('2020-13-01') == False     # invalid month
        assert rule.validate_value('2020-02-30') == False     # invalid day
        assert rule.validate_value(None) == False
        assert rule.validate_value('') == False
    
    def test_length_validation_rules(self):
        """Test length validation rules."""
        rule_loader = ValidationRuleLoader()
        rules = rule_loader.load_validation_rules()
        rule = rules['Provider Organization Name (Legal Business Name)']
        
        # Valid lengths (2-120 characters)
        assert rule.validate_value('AB') == True              # min length
        assert rule.validate_value('ABC Medical Center') == True  # normal length
        assert rule.validate_value('A' * 120) == True        # max length
        
        # Invalid lengths
        assert rule.validate_value('A') == False             # too short
        assert rule.validate_value('A' * 121) == False      # too long
        assert rule.validate_value(None) == False
        assert rule.validate_value('') == False
    
    def test_numeric_validation_rules(self):
        """Test numeric validation rules (if any exist)."""
        # Create a custom numeric rule for testing
        from src.core.data_models import ValidationRule
        
        numeric_rule = ValidationRule(
            rule_id="TEST_NUMERIC",
            column_name="test_numeric",
            rule_type="numeric",
            rule_config={},
            description="Test numeric validation"
        )
        
        # Valid numeric values
        assert numeric_rule.validate_value('123') == True
        assert numeric_rule.validate_value('123.45') == True
        assert numeric_rule.validate_value('-123') == True
        assert numeric_rule.validate_value('0') == True
        
        # Invalid numeric values
        assert numeric_rule.validate_value('abc') == False
        assert numeric_rule.validate_value('123abc') == False
        assert numeric_rule.validate_value(None) == False
        assert numeric_rule.validate_value('') == False
    
    def test_regex_validation_comprehensive(self):
        """Test comprehensive regex validation scenarios."""
        rule_loader = ValidationRuleLoader()
        rules = rule_loader.load_validation_rules()
        
        # Test NPI regex validation
        npi_rule = rules['NPI']
        assert npi_rule.validate_value('1234567890') == True
        assert npi_rule.validate_value('123456789') == False   # 9 digits
        assert npi_rule.validate_value('12345678ab') == False  # contains letters
        
        # Test EIN regex validation (allows <UNAVAIL>)
        ein_rule = rules['Employer Identification Number (EIN)']
        assert ein_rule.validate_value('123456789') == True    # 9 digits
        assert ein_rule.validate_value('<UNAVAIL>') == True    # special value
        assert ein_rule.validate_value('12345678') == False    # 8 digits
        assert ein_rule.validate_value('unavailable') == False # wrong format
    
    def test_enum_validation_comprehensive(self):
        """Test comprehensive enum validation scenarios."""
        rule_loader = ValidationRuleLoader()
        rules = rule_loader.load_validation_rules()
        
        # Test Entity Type Code enum
        entity_rule = rules['Entity Type Code']
        assert entity_rule.validate_value('1') == True
        assert entity_rule.validate_value('2') == True
        assert entity_rule.validate_value('3') == False
        assert entity_rule.validate_value('0') == False
        
        # Test state code enum (if available)
        if 'Provider License Number State Code_1' in rules:
            state_rule = rules['Provider License Number State Code_1']
            assert state_rule.validate_value('CA') == True
            assert state_rule.validate_value('NY') == True
            assert state_rule.validate_value('XX') == False  # invalid state
            assert state_rule.validate_value('California') == False  # full name not code


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
