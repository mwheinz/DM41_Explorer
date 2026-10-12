"""
Centralized configuration for DM41_Explorer (and any sibling app that shares
this code). Each app keeps its own preferences file -- see ProjectConfig's
`prefs_filename` argument -- so installing more than one never makes them
overwrite each other's settings or recent-file lists.
"""

import copy
import json
import logging
from pathlib import Path

from memory import DEFAULT_MODE, DeviceMode, atomic_write_text

logger = logging.getLogger(__name__)


class PreferencesError(OSError):
    """The preferences file could not be read or written. The message
    names the file; the underlying error is chained as __cause__."""


class _Pref:
    """A preference stored as-is under the attribute's own name, for the
    settings that need no conversion or validation. Settings that do (mode,
    log_directory, high_contrast, window_size, recent_files) are written
    out as properties on ProjectConfig instead."""

    def __set_name__(self, owner, name):
        self.key = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return obj._prefs[self.key]

    def __set__(self, obj, value):
        obj._prefs[self.key] = value


class ProjectConfig:
    """Centralized configuration with file-based persistence."""

    # Persistent storage location in the user's home directory. This is the
    # DM41_Explorer's file.
    PREFS_FILE = Path.home() / ".dm41_explorer.json"

    # Default values
    DEFAULT_PREFS = {
        # Which calculator the app works as -- see memory.DeviceMode and
        # docs/dm41x_explorer_plan.md phase 6. The DM41X is the more common
        # device, so it is the default; note that an existing user has no
        # such key, and so gets DM41X whichever calculator they own.
        "mode": DEFAULT_MODE.value,
        "baudrate": 38400,  # The only speed the DM41L currently supports.
        "console_timeout_minutes": 10,
        "serial_port": "/dev/tty.usbmodem14101",  # MacOS default(?)
        "logging_level": "INFO",
        "log_directory": str(Path.home()),
        "appearance_mode": "System",  # "System", "Light", "Dark"
        "color_theme": "blue",  # CustomTkinter built-in theme name (or a theme file's path)
        "high_contrast": True,  # True = gui/contrast.py adjusts the theme's colors so text is readable
        "font_family": "",  # "" = use CustomTkinter's built-in per-platform default
        "font_size": 0,  # 0 = use CustomTkinter's built-in default size
        "recent_files": [],  # paths of recently opened/saved state files, most-recent first
        "window_width": 0,  # main window size at last close; 0 = not saved yet
        "window_height": 0,
        # main window position at last close, in the window system's own
        # units (GitHub issue #63); None = not saved yet
        "window_x": None,
        "window_y": None,
        # Per mode, the XROM libraries the user has arranged, highest
        # priority first: {"DM41X": [{"id": "math", "enabled": true}, ...]}.
        # The mode's built-in library is implied, not stored (GitHub issue
        # #53).
        "xrom_libraries": {},
    }

    # File > Open Recent is capped at this many entries -- oldest
    # dropped first, enforced by add_recent_file() below.
    MAX_RECENT_FILES = 10

    # Settings read and written as stored (see _Pref).
    baudrate = _Pref()
    console_timeout_minutes = _Pref()
    serial_port = _Pref()
    logging_level = _Pref()
    appearance_mode = _Pref()
    color_theme = _Pref()
    font_family = _Pref()
    font_size = _Pref()

    def __init__(self, prefs_filename: str = None):
        """Initializes the config with default values.

        `prefs_filename` (e.g. ".dm41x_explorer.json") names this app's own
        preferences file in the user's home directory. Omit it to use the
        class-level PREFS_FILE (DM41_Explorer's)."""
        if prefs_filename is not None:
            self.PREFS_FILE = Path.home() / prefs_filename
        # A deep copy: DEFAULT_PREFS holds a list (recent_files), which a
        # shallow copy would share with every ProjectConfig.
        self._prefs = copy.deepcopy(self.DEFAULT_PREFS)

    def load(self) -> None:
        """Merges the preferences file over the defaults. A missing file is
        not an error: the defaults simply stay in force.

        Raises PreferencesError if the file exists but can't be read, isn't
        valid JSON, or isn't a JSON object; the current settings are then
        left unchanged."""
        if not self.PREFS_FILE.exists():
            return
        try:
            with open(self.PREFS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError) as e:
            raise PreferencesError(
                f"Could not load preferences from {self.PREFS_FILE}: {e}"
            ) from e
        if not isinstance(loaded, dict):
            raise PreferencesError(
                f"Could not load preferences from {self.PREFS_FILE}: "
                f"expected a JSON object, found {type(loaded).__name__}"
            )
        self._prefs.update(loaded)

    def save(self) -> None:
        """Writes the current preferences to disk. Raises PreferencesError
        if the file can't be written; an existing file is then left as it
        was."""
        try:
            # Atomic, so a failed save can't leave a truncated file that
            # then stops the preferences loading at the next start.
            atomic_write_text(self.PREFS_FILE, json.dumps(self._prefs, indent=2))
        except OSError as e:
            raise PreferencesError(
                f"Could not save preferences to {self.PREFS_FILE}: {e}"
            ) from e

    @property
    def mode(self) -> DeviceMode:
        """The saved DM41L/DM41X mode, as a DeviceMode.

        This is the *persisted* setting. A session can be working in the
        other mode after an auto-switch on load, which is deliberately not
        written here (docs/dm41x_explorer_plan.md phase 6) -- ask the
        application for the effective mode, not the config.

        A hand-edited or unrecognised value reads as the default rather
        than raising, so a bad preferences file cannot stop the app
        starting."""
        return DeviceMode.from_value(self._prefs["mode"])

    @mode.setter
    def mode(self, value):
        """Accepts a DeviceMode or its name; stores the name, so the
        preferences file stays readable JSON."""
        self._prefs["mode"] = DeviceMode.from_value(value).value

    @property
    def log_directory(self) -> Path:
        return Path(self._prefs["log_directory"]).expanduser()

    @log_directory.setter
    def log_directory(self, value):
        self._prefs["log_directory"] = str(value)

    @property
    def high_contrast(self) -> bool:
        """Whether the theme's colors are adjusted for readable text. A
        hand-edited value that is not a JSON true/false reads as the
        default (on)."""
        value = self._prefs["high_contrast"]
        if isinstance(value, bool):
            return value
        return self.DEFAULT_PREFS["high_contrast"]

    @high_contrast.setter
    def high_contrast(self, value):
        self._prefs["high_contrast"] = bool(value)

    @property
    def window_size(self):
        """The main window's (width, height) saved at the last close, or
        None if none was saved (or the saved values are unusable)."""
        try:
            width = int(self._prefs["window_width"])
            height = int(self._prefs["window_height"])
        except (KeyError, TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return (width, height)

    @window_size.setter
    def window_size(self, value):
        width, height = value
        self._prefs["window_width"] = int(width)
        self._prefs["window_height"] = int(height)

    @property
    def window_position(self):
        """The main window's (x, y) saved at the last close, or None if none
        was saved (or the saved values are unusable). Either number can be
        negative: a monitor left of or above the main one."""
        x, y = self._prefs.get("window_x"), self._prefs.get("window_y")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (x, y)):
            return None
        return (x, y)

    @window_position.setter
    def window_position(self, value):
        x, y = value
        self._prefs["window_x"] = int(x)
        self._prefs["window_y"] = int(y)

    @property
    def recent_files(self) -> list:
        """Paths of recently opened/saved state files, most-recent
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
        time (see gui/app.py's open_state_file())."""
        path_str = str(path)
        self._prefs["recent_files"] = [
            p for p in self._prefs["recent_files"] if p != path_str
        ]

    def clear_recent_files(self) -> None:
        """Empties the recent-files list entirely -- File > Open
        Recent > Clear Recent Files."""
        self._prefs["recent_files"] = []

    @property
    def xrom_directory(self) -> Path:
        """Where the user's own XROM library files live: a folder named
        after the preferences file, ~/.dm41_explorer/xroms for
        ~/.dm41_explorer.json, so each app (and each test's preferences
        file) has its own."""
        return self.PREFS_FILE.with_suffix("") / "xroms"

    def xrom_library_entries(self, mode) -> list:
        """The XROM libraries arranged for `mode` (a DeviceMode or its
        name), highest priority first, as (id, enabled) pairs. Entries
        that can't be read -- a hand-edited file -- are skipped, and a
        repeated id keeps its first place, so a bad setting can't stop the
        app starting. Ids are returned whether or not a library still has
        them: the caller shows the missing ones rather than dropping
        them."""
        stored = self._prefs.get("xrom_libraries")
        entries = (
            stored.get(DeviceMode.from_value(mode).value)
            if isinstance(stored, dict)
            else None
        )
        if not isinstance(entries, list):
            return []
        result, seen = [], set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            library_id = entry.get("id")
            if not isinstance(library_id, str) or not library_id or library_id in seen:
                continue
            seen.add(library_id)
            result.append((library_id, entry.get("enabled") is True))
        return result

    def set_xrom_library_entries(self, mode, entries) -> None:
        """Stores `entries`, (id, enabled) pairs highest priority first,
        for `mode`. The other mode's arrangement is kept."""
        stored = self._prefs.get("xrom_libraries")
        stored = dict(stored) if isinstance(stored, dict) else {}
        stored[DeviceMode.from_value(mode).value] = [
            {"id": library_id, "enabled": bool(enabled)}
            for library_id, enabled in entries
        ]
        self._prefs["xrom_libraries"] = stored

    def enabled_xrom_library_ids(self, mode) -> list:
        """The ids of the libraries enabled for `mode`, highest priority
        first -- what xrom_library.active_set() takes."""
        return [i for i, enabled in self.xrom_library_entries(mode) if enabled]

    def get_all(self) -> dict:
        return self._prefs
