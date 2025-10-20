"""
Configuration management for the DQ system.
"""

import yaml
import os
from pathlib import Path
from typing import Any, Dict, Optional
import logging


logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages configuration loading and access."""
    
    def __init__(self, config_dir: str = "config"):
        """
        Initialize configuration manager.
        
        Args:
            config_dir: Directory containing configuration files
        """
        self.config_dir = Path(config_dir)
        self._configs = {}
        self._load_all_configs()
    
    def _load_all_configs(self) -> None:
        """Load all YAML configuration files."""
        if not self.config_dir.exists():
            logger.warning(f"Configuration directory not found: {self.config_dir}")
            return
        
        for config_file in self.config_dir.glob("*.yaml"):
            try:
                with open(config_file, 'r') as f:
                    config_name = config_file.stem
                    self._configs[config_name] = yaml.safe_load(f)
                logger.info(f"Loaded configuration: {config_name}")
            except Exception as e:
                logger.error(f"Failed to load config {config_file}: {str(e)}")
    
    def get_agent_config(self, agent_name: str) -> Dict[str, Any]:
        """Get configuration for a specific agent."""
        agents_config = self._configs.get("agents", {})
        return agents_config.get(agent_name, {})
    
    def get_config(self, config_name: str, default: Any = None) -> Any:
        """Get any configuration by name."""
        return self._configs.get(config_name, default)
    
    def reload_configs(self) -> None:
        """Reload all configuration files."""
        self._configs.clear()
        self._load_all_configs()
    

