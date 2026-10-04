"""
Centralized configuration for the DM41_Explorer apps (DM41L_Explorer,
DM41X_Explorer, ...). Each app keeps its own preferences file -- see
ProjectConfig's `prefs_filename` argument -- so installing more than one
never makes them overwrite each other's settings or recent-file lists.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ProjectConfig:
    """Centralized configuration with file-based persistence."""

    # Persistent storage location in the user's home directory. This is the
    # DM41L_Explorer's file (its name predates the other apps, and existing
    # users' settings live there). Another app passes its own
    # `prefs_filename` to __init__() instead, which shadows this class
    # attribute on that instance only.
    PREFS_FILE = Path.home() / ".dm41l_explorer.json"

    # Default values
    DEFAULT_PREFS = {
        "baudrate": 38400, # The only speed the DM41L currently supports.
        "console_timeout_minutes": 10,
        "serial_port": "/dev/tty.usbmodem14101", # MacOS default(?)
        "logging_level": "INFO",
        "log_directory": str(Path.home()),
        "appearance_mode": "System",  # "System", "Light", "Dark"
        "color_theme": "blue",  # CustomTkinter built-in theme name
        "font_family": "",  # "" = use CustomTkinter's built-in per-platform default
        "font_size": 0,  # 0 = use CustomTkinter's built-in default size
        "recent_files": [],  # paths of recently opened/saved dump files, most-recent first
    }

    # File > Open Recent is capped at this many entries -- oldest
    # dropped first, enforced by add_recent_file() below.
    MAX_RECENT_FILES = 10

    def __init__(self, prefs_filename: str = None):
        """Initializes the config with default values.

        `prefs_filename` (e.g. ".dm41x_explorer.json") names this app's own
        preferences file in the user's home directory. Omit it to use the
        class-level PREFS_FILE (DM41L_Explorer's)."""
        if prefs_filename is not None:
            self.PREFS_FILE = Path.home() / prefs_filename
        self._prefs = self.DEFAULT_PREFS.copy()

    def load(self) -> dict:
        """
        Loads preferences from disk. Throws an exception if the preference
        file cannot be found or is otherwise unreadable.
        """
        loaded_data = {}
        try:
            if self.PREFS_FILE.exists():
                with open(self.PREFS_FILE, "r", encoding="utf-8") as f:
                    loaded_data = json.load(f)
                    self._prefs.update(loaded_data)
        except Exception as e:
            raise IOError(
                f"Warning: Could not load preferences from {self.PREFS_FILE}"
            ) from e


    def save(self, prefs: dict = None) -> None:
        """Saves current preferences to disk."""
        if prefs is not None:
            self._prefs = prefs

        try:
            with open(self.PREFS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._prefs, f, indent=2)
        except Exception as e:
            raise Exception(
                f"Error: Could not save preferences to {self.PREFS_FILE}"
                ) from e

    @property
    def baudrate(self) -> int:
        return self._prefs["baudrate"]

    @baudrate.setter
    def baudrate(self, value):
        self._prefs["baudrate"] = value

    @property
    def console_timeout_minutes(self) -> int:
        return self._prefs["console_timeout_minutes"]

    @console_timeout_minutes.setter
    def console_timeout_minutes(self, value):
        self._prefs["console_timeout_minutes"] = value

    @property
    def serial_port(self) -> str:
        return self._prefs["serial_port"]

    @serial_port.setter
    def serial_port(self, value):
        self._prefs["serial_port"] = value

    @property
    def logging_level(self) -> str:
        return self._prefs["logging_level"]

    @logging_level.setter
    def logging_level(self, value):
        self._prefs["logging_level"] = value

    @property
    def log_directory(self) -> Path:
        return Path(self._prefs["log_directory"]).expanduser()

    @log_directory.setter
    def log_directory(self, value):
        self._prefs["log_directory"] = str(value)

    @property
    def appearance_mode(self) -> str:
        return self._prefs["appearance_mode"]

    @appearance_mode.setter
    def appearance_mode(self, value):
        self._prefs["appearance_mode"] = value

    @property
    def color_theme(self) -> str:
        return self._prefs["color_theme"]

    @color_theme.setter
    def color_theme(self, value):
        self._prefs["color_theme"] = value

    @property
    def font_family(self) -> str:
        return self._prefs["font_family"]

    @font_family.setter
    def font_family(self, value):
        self._prefs["font_family"] = value

    @property
    def font_size(self) -> int:
        return self._prefs["font_size"]

    @font_size.setter
    def font_size(self, value):
        self._prefs["font_size"] = value

    @property
    def recent_files(self) -> list:
        """Paths of recently opened/saved dump files, most-recent
        first. Returns a defensive copy -- there's no setter; use
        add_recent_file()/remove_recent_file()/clear_recent_files()
        instead, so the dedup/cap invariants always hold."""
        return list(self._prefs["recent_files"])

    def add_recent_file(self, path) -> None:
        """Records `path` as the most-recently-used file: moves it
        to the front if it's already listed (never duplicated), and
        caps the list at MAX_RECENT_FILES, dropping the oldest.

        Stored as plain strings (not Path objects) since this is
        JSON-serialized as-is by save().
        """
        path_str = str(path)
        files = [p for p in self._prefs["recent_files"] if p != path_str]
        files.insert(0, path_str)
        self._prefs["recent_files"] = files[: self.MAX_RECENT_FILES]

    def remove_recent_file(self, path) -> None:
        """Drops `path` from the recent-files list, if present --
        used when a listed file turns out to be missing at open
        time (see gui/app.py's open_dump_file())."""
        path_str = str(path)
        self._prefs["recent_files"] = [
            p for p in self._prefs["recent_files"] if p != path_str
        ]

    def clear_recent_files(self) -> None:
        """Empties the recent-files list entirely -- File > Open
        Recent > Clear Recent Files."""
        self._prefs["recent_files"] = []

    def get_all(self) -> dict:
        return self._prefs
