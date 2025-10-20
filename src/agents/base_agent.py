"""
Base agent class providing common functionality for all DQ agents.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseAgent(ABC):
    """
    Base class for all data quality agents.
    
    Provides common functionality:
    - Execution tracking and logging
    - Error handling and retry logic
    - Configuration management
    - Performance monitoring
    """
    
    def __init__(self, agent_name: str, config: Optional[Dict[str, Any]] = None):
        """
        Initialize base agent.
        
        Args:
            agent_name: Name of the agent
            config: Agent configuration dictionary
        """
        self.agent_name = agent_name
        self.config = config or {}
        self.logger = logging.getLogger(f"src.agents.{agent_name}")
    
    
    @abstractmethod
    def execute(self, **kwargs) -> Any:
        """
        Abstract method for agent-specific execution logic.
        
        Args:
            **kwargs: Agent-specific parameters
            
        Returns:
            Agent results
        """
        pass
    