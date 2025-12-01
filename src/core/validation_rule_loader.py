"""
Validation rule loader for converting YAML configuration to ValidationRule objects.
"""

from typing import Dict, List, Any
from src.core.data_models import ValidationRule
from src.core.config_manager import ConfigManager
import logging

logger = logging.getLogger(__name__)


class ValidationRuleLoader:
    """Loads and converts validation rules from YAML configuration."""
    
    def __init__(self, config_manager: ConfigManager = None):
        """
        Initialize the validation rule loader.
        
        Args:
            config_manager: ConfigManager instance. If None, creates a new one.
        """
        self.config_manager = config_manager or ConfigManager()
    
    def load_validation_rules(self) -> Dict[str, ValidationRule]:
        """
        Load validation rules from YAML configuration.
        
        Returns:
            Dictionary mapping column names to ValidationRule objects
        """
        rules_config = self.config_manager.get_config("validation_rules", {})
        rules = rules_config.get("rules", {})
        
        validation_rules = {}
        
        for column_name, rule_config in rules.items():
            try:
                validation_rule = self._create_validation_rule(column_name, rule_config)
                validation_rules[column_name] = validation_rule
            except Exception as e:
                logger.error(f"Failed to create validation rule for column '{column_name}': {str(e)}")
                continue
        
        logger.info(f"Loaded {len(validation_rules)} validation rules from configuration")
        return validation_rules
    
    def _create_validation_rule(self, column_name: str, rule_config: Dict[str, Any]) -> ValidationRule:
        """
        Create a ValidationRule object from configuration.
        
        Args:
            column_name: Name of the column
            rule_config: Rule configuration dictionary
            
        Returns:
            ValidationRule object
        """
        rule_id = rule_config.get("rule_id", f"{column_name.upper().replace(' ', '_')}_FORMAT")
        rule_type = rule_config.get("rule_type")
        rule_config_dict = rule_config.get("rule_config", {})
        description = rule_config.get("description", f"Validation rule for {column_name}")
        
        if not rule_type:
            raise ValueError(f"Missing 'rule_type' for column '{column_name}'")
        
        return ValidationRule(
            rule_id=rule_id,
            column_name=column_name,
            rule_type=rule_type,
            rule_config=rule_config_dict,
            description=description
        )
    
    def reload_rules(self) -> Dict[str, ValidationRule]:
        """
        Reload validation rules from configuration.
        
        Returns:
            Dictionary mapping column names to ValidationRule objects
        """
        self.config_manager.reload_configs()
        return self.load_validation_rules()
