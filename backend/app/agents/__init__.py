"""
Multi-Agent System: Specialized agents for different analysis domains.
"""

from typing import Any
from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """Base class for all specialized agents."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    @abstractmethod
    async def analyze(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """
        Analyze the query and return results.

        Args:
            query: User's question or request
            context: Additional context (company data, previous results, etc.)

        Returns:
            Analysis results
        """
        pass

    def can_handle(self, query: str) -> bool:
        """Check if this agent can handle the query."""
        return True
