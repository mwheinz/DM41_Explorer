"""
Abstract class representing the Voyager console commands.
"""

from abc import ABC, abstractmethod
from typing import List, Any, Optional


class BaseCommand(ABC):
    """Base class for all Voyager console commands."""

    def __init__(self, args: Optional[List] = None, timeout: float = 1.0, serial=None):
        self.args = args
        self.timeout = timeout
        self.serial = serial

    @property
    @abstractmethod
    def command_string(self) -> str:
        """Returns the actual ASCII string to send over serial."""
        # pass

    @abstractmethod
    def parse_response(self, raw_data: str) -> Any:
        """Parses the response data from the device after filtering echoes."""
        # pass

    def get_request(self) -> str:
        """Formats the command string with arguments if necessary."""
        return self.command_string
