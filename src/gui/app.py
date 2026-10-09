'''
DM41_Explorer: a CustomTkinter GUI for DM41-series calculator memory states.

'''

import logging
import logging.handlers
import sys
from pathlib import Path
from datetime import datetime
import platform
from tkinter import filedialog, messagebox, Menu

import customtkinter as ctk

from memory import (
    ERROR,
    WARNING,
    DeviceMode,
    Memory,
    check_profile_fit,
    evaluate_mode_switch,
    format_findings,
)
from engine.serial_manager import SerialManager
from engine.command_engine import CommandEngine
from engine.commands import (
    BatteryCheckCommand,
    GetTimeCommand,
    SetTimeCommand,
    MemoryStringCommand,
    LoadMemoryStringCommand,
    ConsoleTimeoutCommand,
)

from config import ProjectConfig
from gui.contrast import SECONDARY_TEXT, WARNING_TEXT, improve_theme_contrast
from gui.tab_common import ui_font
from gui.window_geometry import initial_window_size, minimum_window_size, parse_geometry
from gui.port_dialog import PortSelectionDialog
from gui.preferences_dialog import PreferencesDialog
from gui.help_dialog import KeyboardShortcutsDialog
from gui.mnemonics_reference_dialog import MnemonicsReferenceDialog
from gui.overview_tab import OverviewTab
from gui.flags_tab import FlagsTab
from gui.data_registers_tab import DataRegistersTab
from gui.xm_files_tab import XMFilesTab
from gui.hex_view_tab import HexViewTab
from gui.program_tab import ProgramTab
from gui.key_assignments_tab import KeyAssignmentsTab
from gui.alarms_tab import AlarmsTab

try:
    from dm41version import APP_VERSION
except ImportError:
    APP_VERSION = "unknown"

ENGINE_POLL_MS = 50

PLATFORM_SYSTEM = platform.system()

# Header-button methods that Cmd/Ctrl+E / Cmd/Ctrl+I forward to, for
# whichever tab is currently active -- see _bind_keys() and
# DM41ExplorerApp._dispatch_tab_action() below. A tab name missing
# from one of these dicts means that shortcut is a silent no-op there
# (e.g. Overview has no Import/Export at all).
_EXPORTABLE_TABS = {
    "Data Registers": "_export_registers",
    "XM Files": "_export_selected",
    "Programs": "_export_selected",
}
_IMPORTABLE_TABS = {
    "Data Registers": "_import_registers",
    "XM Files": "_import_file",
    "Programs": "_import_program",
}

# File > Open Recent: long paths are shortened by keeping the tail
# (the part that actually distinguishes files living in different
# folders) rather than the head.
_MAX_RECENT_LABEL_LEN = 60


def _format_recent_label(path_str: str) -> str:
    if len(path_str) <= _MAX_RECENT_LABEL_LEN:
        return path_str
    return "\u2026" + path_str[-(_MAX_RECENT_LABEL_LEN - 1) :]


logger = logging.getLogger(__name__)

# Cap the log file at 2MB with 3 rotated backups (dm41_explorer.log,
# .log.1, .log.2, .log.3 -- ~8MB worst case) so a long-running session
# (or a chatty DEBUG level left on by mistake) can't grow the file
# unbounded the way the previous plain FileHandler did.
LOG_FILE_MAX_BYTES = 2 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 3

# --- Logging model -----------------------------------------------------
# Every module gets its own logger via `logging.getLogger(__name__)` at
# import time (never the root logger directly) -- log records then carry
# the originating module's dotted path (e.g. "gui.hex_view_tab") for
# free, which is what makes a shared log file navigable once more than a
# couple of modules are writing to it. See CONTRIBUTING.md's "Logging"
# section for the full writeup; the short version, by level:
#   DEBUG    Internal detail only useful while actively debugging (raw
#            serial bytes, state-machine transitions, expected/no-op
#            exceptions swallowed during defensive rendering).
#   INFO     Normal lifecycle events a user could plausibly want to see
#            in their own log: connect/disconnect, a state loaded/saved,
#            an XM file added/edited/removed, a register edited,
#            preferences saved.
#   WARNING  Something unexpected happened but the app recovered on its
#            own and kept going (e.g. an invalid log directory fell back
#            to the home directory; a malformed preference value was
#            ignored).
#   ERROR    An operation the user asked for failed and was surfaced to
#            them via a dialog. Every `messagebox.showerror(...)` call
#            in this codebase should be paired with a `logger.error(...)`
#            or `logger.exception(...)` (inside an `except` block, to
#            capture the traceback) right next to it -- the dialog tells
#            the user something broke, the log records enough to
#            diagnose *why* after the fact.
#   CRITICAL Reserved for failures serious enough to abort a whole run
#            loop (see serial_manager.py's read-thread crash handling).
# The data/model layer (memory/*.py) deliberately has no loggers of its
# own -- it raises (ValueError/DM41MemoryError) rather than swallowing,
# so logging happens exactly once, at whichever GUI boundary catches the
# exception and decides how to present it to the user.


def _setup_logging(config_store, log_basename="dm41_explorer"):
    # `log_basename` names the log file (<log_basename>.log) so each app in
    # this repository writes its own, instead of all sharing one.
    level = getattr(logging, config_store.logging_level.upper(), logging.INFO)

    log_dir = config_store.log_directory
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # Falls back rather than raising -- a bad configured log
        # directory shouldn't prevent the app from starting at all, just
        # log to the default location instead. Logged as a warning (not
        # an error) once the fallback handler below is attached; nothing
        # is lost, just redirected.
        logger.warning(
            "Could not use log directory %s (%s); falling back to %s",
            log_dir,
            e,
            Path.home(),
        )
        log_dir = Path.home()
    log_file = log_dir / f"{log_basename}.log"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        mode="a",
        encoding="utf-8",
        maxBytes=LOG_FILE_MAX_BYTES,
        backupCount=LOG_FILE_BACKUP_COUNT,
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    file_handler.setLevel(level)

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    logger.info(
        "Logging configured: level=%s, file=%s", config_store.logging_level, log_file
    )


def _apply_color_theme(name):
    '''Loads the CustomTkinter color theme `name` (a built-in theme's name,
    or the path of a theme JSON file). A theme that can't be loaded -- a
    hand-edited preference naming a file that is gone, say -- is logged and
    replaced by the default theme instead of stopping the app from
    starting. Must run before any widget is built.'''
    try:
        ctk.set_default_color_theme(name)
    except (OSError, ValueError) as e:
        default = ProjectConfig.DEFAULT_PREFS["color_theme"]
        logger.warning("Could not load color theme %r, using %r: %s", name, default, e)
        ctk.set_default_color_theme(default)


def _apply_font_prefs(config_store):
    '''Overrides CustomTkinter's default UI font family/size, if configured.

    CTkFont() instances read `ThemeManager.theme["CTkFont"]` at construction
    time for whichever of family/size/weight isn't explicitly passed in, so
    this has to run before any widget with a default (unset) font is built
    -- in practice, right after `set_default_color_theme()` and before
    `_build_layout()`. There's no supported way to retroactively change the
    font of widgets that already exist, which is why a font change made in
    Preferences only takes effect after a restart (see
    gui/preferences_dialog.py).

    Leaves CTk's own per-platform default (set by `set_default_color_theme`
    above) alone for whichever of family/size the user hasn't overridden.
    '''
    if config_store.font_family:
        ctk.ThemeManager.theme["CTkFont"]["family"] = config_store.font_family
    if config_store.font_size:
        ctk.ThemeManager.theme["CTkFont"]["size"] = config_store.font_size


# A DM41L `.dm41` state and a DM41X `.d41` state file are the same text format.
STATE_EXTENSIONS = (".dm41", ".d41")

# What Save As offers for a buffer that has no file of its own yet. The text
# format is identical either way; this only picks the extension the user's
# own calculator writes, so a new state lands with the name they expect.
DEFAULT_EXTENSION_BY_MODE = {
    DeviceMode.DM41L: ".dm41",
    DeviceMode.DM41X: ".d41",
}
STATE_FILETYPES = [
    ("DM41 memory state", ("*.dm41", "*.d41")),
    ("All files", "*.*"),
]


class DM41ExplorerApp(ctk.CTk):
    '''Main application window.'''

    # How long after the last resize event the window size is saved.
    RESIZE_SAVE_DELAY_MS = 1000

    # How long show_connect_dialog() waits after the menu command returns.
    CONNECT_DIALOG_DELAY_MS = 20

    def __init__(self):
        super().__init__()

        self._closing = False
        self._tracking_size = False
        self._applied_size = None
        self._resize_after_id = None
        self._tracking_after_id = None

        self.config_store = ProjectConfig()
        try:
            self.config_store.load()
        except Exception as e:
            messagebox.showerror("Unable to load preferences",
                                  str(e))

        _setup_logging(self.config_store)

        ctk.set_appearance_mode(self.config_store.appearance_mode)
        _apply_color_theme(self.config_store.color_theme)
        _apply_font_prefs(self.config_store)
        if self.config_store.high_contrast:
            improve_theme_contrast(ctk.ThemeManager.theme)

        self.title("DM41_Explorer")

        # The effective DM41L/DM41X mode: which calculator the app is
        # working as. It starts from the saved preference, and an
        # auto-switch on load can move it for this session only without
        # writing the preference back (see _offer_mode_switch_for_load).
        # Everything that needs a memory map asks self.profile, never the
        # config and never the connection.
        self.mode = self.config_store.mode

        self.serial = SerialManager(error_callback=self._handle_serial_error)
        self.engine = CommandEngine(self.serial)
        self.memory = Memory(profile=self.profile)
        self.memory_source = None
        self._command_pending = False

        self._build_layout()
        self._apply_window_size()
        self._menubar = self._build_menus()
        self._apply_mode_to_menus()
        self._bind_keys()

        if PLATFORM_SYSTEM == "Darwin":
            # macOS delivers "double-click a .dm41 file" as an "Open
            # Document" AppleEvent rather than a sys.argv entry -- both for
            # a cold launch (Finder launches the app, then sends this) and
            # for double-clicking a file while the app is already running.
            # Tk exposes it as a registered command rather than a normal
            # event binding; see main()/_handle_startup_file_arg below for
            # the non-macOS (sys.argv-based) half of double-click support.
            self.createcommand("::tk::mac::OpenDocument", self._on_mac_open_document)
            # Cmd+Q and the application menu's own "Quit DM41_Explorer"
            # call this command, whose Tk default does not go through the
            # WM_DELETE_WINDOW handler: without this they would skip the
            # unsaved-changes prompt and the saving of the window size.
            self.createcommand("::tk::mac::Quit", self.on_close)

        self._start_engine_pump()
        self._render_tabs()

        # Nothing touches the serial port until the user chooses
        # Connect / Reconnect... (the app starts offline).

    # -- Mode (DM41L / DM41X) ---------------------------------------------

    @property
    def profile(self):
        """The DeviceProfile the app is working in right now.

        The single source of truth for every memory map question: what a
        state is opened with, what the Overview counts, which keyboard the
        Key Assignments tab draws. Derived from the mode, so there is no
        second copy to keep in step."""
        return self.mode.profile

    @property
    def mode_is_temporary(self) -> bool:
        """Whether the effective mode differs from the saved preference,
        i.e. an auto-switch moved it for this session only."""
        return self.mode is not self.config_store.mode

    def _mode_status_text(self) -> str:
        text = f"{self.mode.value} mode"
        if self.mode_is_temporary:
            # Mike, 2026-10-09: the status bar is where a temporary
            # override is shown; the Preferences dialog shows the saved
            # setting and says nothing about this.
            text += " (this session)"
        return text

    def _idle_status(self) -> str:
        """The status text for "nothing going on": the connection state in
        a mode that has a serial console, and nothing at all in one that
        does not.

        "Not connected" is only meaningful where connecting is possible
        (Mike, 2026-10-09). In DM41X mode there is nothing to connect to,
        so the status bar stays empty until something actually happens
        (a state loaded, a mode changed), rather than reporting the
        absence of a connection the app would refuse to make anyway."""
        return "Not connected" if self.mode.supports_serial else ""

    def _refresh_mode_indicator(self):
        self._mode_label.configure(text=self._mode_status_text())

    def _apply_mode_to_menus(self):
        """Greys out the serial actions in a mode whose calculator has no
        serial console -- disabled rather than hidden (Mike, 2026-10-09),
        so the user can see the app has them and why they are unavailable.

        The keyboard shortcuts bypass menu state entirely, so every one of
        these actions also guards itself with _require_serial_mode()."""
        state = "normal" if self.mode.supports_serial else "disabled"
        for index in self._serial_menu_indices:
            self._connect_menu.entryconfigure(index, state=state)

    def _require_serial_mode(self) -> bool:
        """True if the serial actions apply in this mode. Otherwise says
        why and returns False -- the guard behind every serial action, for
        the keyboard shortcuts that never consult the menu's state."""
        if self.mode.supports_serial:
            return True
        messagebox.showinfo(
            f"Not Available in {self.mode.value} Mode",
            f"A {self.mode.value} has no serial console, so there is "
            "nothing to connect to.\n\n"
            "Switch to DM41L mode in Preferences to use the serial "
            "connection. Note that switching mode starts a new, empty "
            "memory state.",
        )
        return False

    def set_mode(self, mode, *, persist: bool, reason: str = None) -> bool:
        """Changes the effective mode, erasing the open state.

        Changing mode starts a new, empty state on the new mode's profile
        (Mike, 2026-10-09): a Memory fixes its profile when it is built, and
        rebuilding one through to_string()/from_string() to keep the
        contents would be a silent partial conversion in the narrowing
        direction. Moving a state between models is done by exporting the
        programs and data wanted and re-importing them.

        The sequence, which `reason` only adds a leading sentence to:
          1. the usual discard guard, if the buffer is modified;
          2. a confirmation naming the consequence, which also names the
             disconnect when a serial connection is open;
          3. the disconnect, then the switch and the empty buffer.

        `persist` writes the new mode to the preferences file. An
        auto-switch on load passes False, so it lasts for this session
        only. Returns whether the mode was changed; False means the user
        cancelled at some step, and neither the mode nor the open state
        was touched."""
        if mode is self.mode:
            return True

        if self.memory.modified and not messagebox.askyesno(
            "Unsaved Changes",
            f"Discard unsaved changes and switch to {mode.value} mode?",
        ):
            return False

        lines = []
        if reason:
            lines.append(reason)
        lines.append(
            f"Switching to {mode.value} mode starts a new, empty memory "
            "state. Anything still in the current one is discarded."
        )
        connected = self.serial.is_connected
        if connected:
            lines.append("The serial connection will be closed.")
        lines.append(
            "To move programs or data between models, export them before "
            "switching and import them afterwards."
        )
        lines.append(f"Switch to {mode.value} mode?")
        if not messagebox.askyesno("Switch Mode", "\n\n".join(lines)):
            return False

        if connected:
            self.disconnect()

        previous = self.mode
        self.mode = mode
        if persist:
            self.config_store.mode = mode
            self._persist_config("the DM41L/DM41X mode")
        logger.info(
            "Mode changed from %s to %s (%s)",
            previous.value,
            mode.value,
            "saved" if persist else "this session only",
        )

        self.memory = Memory(profile=self.profile)
        self.memory_source = None
        self._modified_label.configure(text="")
        self._update_source_label()
        self._apply_mode_to_menus()
        self._refresh_mode_indicator()
        self._render_tabs()
        self._set_status(
            f"Switched to {mode.value} mode; started a new, empty memory state."
        )
        return True

    def _offer_mode_switch_for_load(self, path, findings) -> bool:
        """Offers DM41X mode for a state that will not fit the current one,
        and returns whether the caller should go on to load it.

        The offer is deliberately session-only: someone who owns one
        calculator will rarely want the other mode permanently (Mike,
        2026-10-09), so accepting does not write the preference. Declining
        aborts the load and leaves both the mode and the open state alone."""
        target = DeviceMode.DM41X
        logger.info(
            "%s does not fit %s mode: %s",
            path,
            self.mode.value,
            "; ".join(str(finding) for finding in findings),
        )
        reason = (
            f"{Path(path).name} does not fit a {self.mode.value}:\n\n"
            f"{format_findings(findings)}\n\n"
            f"{target.value} mode can open it."
        )
        if not self.set_mode(target, persist=False, reason=reason):
            self._set_status(f"Did not load {Path(path).name}: needs DM41X mode.")
            return False
        return True

    def _handle_serial_error(self, msg: str):
        logger.error("Serial error: %s", msg)
        self._set_status(self._idle_status())
        self._command_pending = False
        # No self.serial.disconnect() call here (unlike the other error
        # handlers below): this callback runs directly on SerialManager's
        # own background thread (see _run_loop's error_callback invocation),
        # and disconnect() calls self._thread.join(), which raises
        # RuntimeError if a thread tries to join itself. SerialManager
        # already closes its own port handle and clears is_connected before
        # invoking this callback -- see serial_manager.py's _run_loop -- so
        # there's nothing left to clean up from here.
        self.after(0, lambda: messagebox.showerror("Error", "Disconnected."))

    # -- Window size ------------------------------------------------------

    def _apply_window_size(self):
        '''Sets the minimum size and the opening size (GitHub issue #42).

        Must run after _build_layout(): the minimum is never less than what
        the tab bar and status bar ask for, and both grow with the
        application font. The opening size is the one saved at the last
        close if there is one, else a default scaled with the font; see
        gui/window_geometry.py for the rules.
        '''
        self.update_idletasks()
        needed = (
            self._reverse_window_scaling(self.winfo_reqwidth()),
            self._reverse_window_scaling(self.winfo_reqheight()),
        )
        screen = (
            self._reverse_window_scaling(self.winfo_screenwidth()),
            self._reverse_window_scaling(self.winfo_screenheight()),
        )
        font_size = ctk.ThemeManager.theme["CTkFont"]["size"]
        self.minsize(*minimum_window_size(font_size, needed, screen))
        width, height = initial_window_size(
            self.config_store.window_size, font_size, needed, screen
        )
        self.geometry(f"{width}x{height}")
        self._applied_size = (width, height)
        # Let the window manager finish placing the window before resizes
        # count as the user's: the size is then re-read, and every later
        # change is saved shortly after it stops (see _on_configure()).
        self._tracking_after_id = self.after(2000, self._start_tracking_size)

    def _start_tracking_size(self):
        self._tracking_after_id = None
        self.update_idletasks()
        self._applied_size = parse_geometry(self.geometry()) or self._applied_size
        self._tracking_size = True
        self.bind("<Configure>", self._on_configure, add="+")

    def _on_configure(self, event):
        '''Saves the window size a moment after the user stops resizing, so
        it survives however the app ends (force quit, crash, a quit path
        that never reaches on_close()).'''
        if event.widget is not self or not self._tracking_size:
            return
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(
            self.RESIZE_SAVE_DELAY_MS, self._save_size_after_resize
        )

    def _save_size_after_resize(self):
        self._resize_after_id = None
        self._remember_window_size()

    def destroy(self):
        '''Cancels the pending size timers first, so none fires into a
        window that no longer exists.'''
        for name in ("_tracking_after_id", "_resize_after_id"):
            after_id = getattr(self, name, None)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except Exception:  # pylint: disable=broad-except
                    pass
                setattr(self, name, None)
        super().destroy()

    def _window_is_maximized(self) -> bool:
        '''True if the window is maximized/zoomed, whose size must not be
        remembered (restoring it would open a normal window at full-screen
        size).'''
        try:
            if self.state() == "zoomed":
                return True
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            return bool(self.attributes("-zoomed"))  # X11
        except Exception:  # pylint: disable=broad-except
            return False

    def _remember_window_size(self):
        '''Saves the current window size to the preferences, for the next
        launch. Never raises: failing to save a size mustn't stop the app
        from closing.'''
        try:
            if self._window_is_maximized():
                return
            size = parse_geometry(self.geometry())
            if size is None or size == self._applied_size:
                return  # nothing the user changed: don't touch the file
            self.config_store.window_size = size
            self.config_store.save()
            self._applied_size = size
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Could not save the window size: %s", e)

    # -- Layout ---------------------------------------------------------

    def _build_layout(self):
        # Status bar first, pinned to the bottom, so the tab view above it
        # gets whatever space is left -- packing order (not just `side`)
        # is what keeps it pinned regardless of window resizing.
        status_bar = ctk.CTkFrame(self)
        status_bar.pack(side="bottom", fill="x", padx=8, pady=(0, 8))

        # The effective mode comes first (Mike, 2026-10-09): it is the
        # standing fact about the session, while the label beside it is a
        # running commentary that _set_status() rewrites constantly. This
        # order also keeps the bar tidy in DM41X mode, where the status
        # text is empty -- the mode reads flush left instead of sitting
        # behind an empty label's padding.
        #
        # Its own label, not text appended to the status, so neither can
        # overwrite the other; it also carries whether the mode is only
        # for this session, which is the one place an auto-switch
        # override is visible.
        self._mode_label = ctk.CTkLabel(
            status_bar,
            text=self._mode_status_text(),
            font=ui_font(),
            text_color=SECONDARY_TEXT,
        )
        self._mode_label.pack(side="left", padx=8, pady=6)
        self._status_label = ctk.CTkLabel(
            status_bar, text=self._idle_status(), font=ui_font()
        )
        self._status_label.pack(side="left", padx=8, pady=6)
        self._modified_label = ctk.CTkLabel(
            status_bar, text="", font=ui_font(), text_color=WARNING_TEXT
        )
        self._modified_label.pack(side="left", padx=8, pady=6)
        self._battery_label = ctk.CTkLabel(
            status_bar, text="", font=ui_font()
        )
        self._battery_label.pack(side="right", padx=16, pady=6)
        self._calc_time_label = ctk.CTkLabel(
            status_bar, text="", font=ui_font()
        )
        self._calc_time_label.pack(side="right", padx=16, pady=6)
        self._source_label = ctk.CTkLabel(
            status_bar,
            text="(new, unsaved buffer)",
            font=ui_font(),
            text_color=SECONDARY_TEXT,
        )
        self._source_label.pack(side="right", padx=16, pady=6)

        self.tabview = ctk.CTkTabview(self, command=self._on_tab_changed)
        self.tabview.pack(side="top", fill="both", expand=True, padx=8, pady=8)
        self.tabview.add("Overview")
        self.tabview.add("Flags")
        self.tabview.add("Programs")
        self.tabview.add("Key Assignments")
        self.tabview.add("Alarms")
        self.tabview.add("Data Registers")
        self.tabview.add("XM Files")
        self.tabview.add("Hex View")

        self.overview_tab = OverviewTab(
            self.tabview.tab("Overview"), on_change=self._on_memory_changed
        )
        self.overview_tab.pack(fill="both", expand=True)

        self.flags_tab = FlagsTab(
            self.tabview.tab("Flags"), on_change=self._on_memory_changed
        )
        self.flags_tab.pack(fill="both", expand=True)

        self.data_registers_tab = DataRegistersTab(
            self.tabview.tab("Data Registers"), on_change=self._on_memory_changed
        )
        self.data_registers_tab.pack(fill="both", expand=True)

        self.hex_view_tab = HexViewTab(self.tabview.tab("Hex View"))
        self.hex_view_tab.pack(fill="both", expand=True)

        self.program_tab = ProgramTab(
            self.tabview.tab("Programs"), on_change=self._on_memory_changed
        )
        self.program_tab.pack(fill="both", expand=True)

        self.key_assignments_tab = KeyAssignmentsTab(
            self.tabview.tab("Key Assignments"), on_change=self._on_memory_changed
        )
        self.key_assignments_tab.pack(fill="both", expand=True)

        self.xm_files_tab = XMFilesTab(
            self.tabview.tab("XM Files"), on_change=self._on_memory_changed
        )
        self.xm_files_tab.pack(fill="both", expand=True)

        self.alarms_tab = AlarmsTab(
            self.tabview.tab("Alarms"), on_change=self._on_memory_changed
        )
        self.alarms_tab.pack(fill="both", expand=True)

        # Which tabs are showing stale content -- see _render_active_tab().
        self._tabs = {
            "Overview": self.overview_tab,
            "Flags": self.flags_tab,
            "Data Registers": self.data_registers_tab,
            "Hex View": self.hex_view_tab,
            "Programs": self.program_tab,
            "Key Assignments": self.key_assignments_tab,
            "XM Files": self.xm_files_tab,
            "Alarms": self.alarms_tab,
        }
        self._tabs_dirty = {name: True for name in self._tabs}

    def _build_menus(self):
        '''Builds the application's menu bar. Every basic function lives
        here rather than in toolbar buttons, per the current design.'''

        acc = "Command" if PLATFORM_SYSTEM == "Darwin" else "Control"

        menubar = Menu(self)

        if PLATFORM_SYSTEM == "Darwin":
            self.createcommand("tkAboutDialog", self._show_about)

        # File Menu
        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(
            label="New Memory Buffer",
            command=self.new_memory_buffer,
            accelerator=f"{acc}+N",
            underline=0,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Open State...",
            command=self.load_state_from_file,
            accelerator=f"{acc}+O",
            underline=0,
        )
        self._recent_menu = Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Open Recent", menu=self._recent_menu, underline=5)
        file_menu.add_command(
            label="Save State",
            command=self.save_state_to_file,
            accelerator=f"{acc}+S",
            underline=0,
        )
        file_menu.add_command(
            label="Save State As...",
            command=self.save_state_as,
            underline=13,
        )
        file_menu.add_separator()
        if PLATFORM_SYSTEM != "Darwin":
            file_menu.add_command(
                label="About Box", command=self._show_about, underline=0
            )
        file_menu.add_command(
            label="Preferences",
            command=self.show_preferences,
            accelerator=f"{acc}+,",
            underline=0,
        )
        if PLATFORM_SYSTEM != "Darwin":
            file_menu.add_separator()
            file_menu.add_command(
                label="Quit", command=self.on_close, accelerator=f"{acc}+Q", underline=0
            )
        menubar.add_cascade(label="File", menu=file_menu)

        # Connect Menu
        connect_menu = Menu(menubar, tearoff=0)
        # Every entry in this menu needs a serial console, so the whole
        # menu is gated by the mode (see _apply_mode_to_menus). The indices
        # are collected as the entries are added rather than hard-coded, so
        # inserting an entry later cannot silently gate the wrong one.
        self._connect_menu = connect_menu
        self._serial_menu_indices = []
        connect_menu.add_command(
            label="Connect / Reconnect...",
            command=self.show_connect_dialog,
            accelerator=f"{acc}+K",
            underline=0,
        )
        connect_menu.add_command(
            label="Disconnect",
            command=self.disconnect,
            accelerator=f"{acc}+D",
            underline=0,
        )
        connect_menu.add_separator()
        connect_menu.add_command(
            label="Set Calculator Time",
            command=self.set_calculator_time,
            accelerator=f"{acc}+T",
            underline=4,
        )
        connect_menu.add_separator()
        connect_menu.add_command(
            label="Get State from DM41L",
            command=self.get_state_from_calculator,
            accelerator=f"{acc}+G",
            underline=0,
        )
        connect_menu.add_command(
            label="Send State to DM41L",
            command=self.send_state_to_calculator,
            accelerator=f"{acc}+U",
            underline=0,
        )
        self._serial_menu_indices = [
            index
            for index in range(connect_menu.index("end") + 1)
            if connect_menu.type(index) == "command"
        ]
        menubar.add_cascade(label="Connect", menu=connect_menu)

        # View Menu
        view_menu = Menu(menubar, tearoff=0)
        view_menu.add_command(
            label="Refresh Tabs",
            command=self._render_tabs,
            accelerator="F5",
            underline=0,
        )
        menubar.add_cascade(label="View", menu=view_menu)

        # Tools Menu
        tools_menu = Menu(menubar, tearoff=0)
        tools_menu.add_command(
            label="Pack Memory...",
            command=self.pack_memory,
            underline=0,
        )
        menubar.add_cascade(label="Tools", menu=tools_menu)

        # Help Menu
        help_menu = Menu(menubar, tearoff=0)
        if PLATFORM_SYSTEM != "Darwin":
            help_menu.add_command(
                label="About DM41_Explorer", command=self._show_about
            )
            help_menu.add_separator()
        help_menu.add_command(
            label="Keyboard Shortcuts",
            command=self.show_keyboard_shortcuts,
            underline=0,
        )
        help_menu.add_command(
            label="FOCAL Mnemonics Reference",
            command=self.show_mnemonics_reference,
            underline=0,
        )
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)
        self._rebuild_recent_files_menu()
        return menubar

    def _bind_keys(self):
        acc = "Command" if PLATFORM_SYSTEM == "Darwin" else "Control"

        self.bind(f"<{acc}-n>", lambda e: self.new_memory_buffer())
        self.bind(f"<{acc}-o>", lambda e: self.load_state_from_file())
        self.bind(f"<{acc}-s>", lambda e: self.save_state_to_file())
        self.bind(f"<{acc}-q>", lambda e: self.on_close())
        # Preferences' menu accelerator (Cmd/Ctrl+,) never actually had
        # a matching bind() -- the menu's `accelerator=` text is purely
        # cosmetic in Tk, so this shortcut has silently never worked
        # until now.
        self.bind(f"<{acc}-comma>", lambda e: self.show_preferences())
        self.bind("<F5>", lambda e: self._render_tabs())

        # GitHub issue #7 follow-up: Export.../Import... are tab-scoped
        # header buttons (Data Registers, XM Files, Programs), not menu
        # items, so their shortcuts are bound globally here and just
        # forward to whichever tab is currently active -- see
        # _dispatch_tab_action().
        self.bind(f"<{acc}-e>", lambda e: self._export_current_tab())
        self.bind(f"<{acc}-i>", lambda e: self._import_current_tab())

        # Connect menu (GitHub issue #9) -- each of these already no-ops
        # with a "Not Connected" warning (or, for disconnect(), silently)
        # when the serial port isn't open, exactly like clicking the menu
        # item itself would, so binding them globally doesn't open up any
        # new not-connected crash path.
        self.bind(f"<{acc}-k>", lambda e: self.show_connect_dialog())
        self.bind(f"<{acc}-d>", lambda e: self.disconnect())
        self.bind(f"<{acc}-t>", lambda e: self.set_calculator_time())
        self.bind(f"<{acc}-g>", lambda e: self.get_state_from_calculator())
        self.bind(f"<{acc}-u>", lambda e: self.send_state_to_calculator())

    def _show_about(self):
        messagebox.showinfo(
            "About DM41_Explorer",
            f"Read, Write and Manipulate\n"
            "DM41-series memory states\n\n"
            f"Version:\n{APP_VERSION}\n\n"
            "Written by Michael Heinz.\n\n"
            "Free software under the GNU General Public License,\n"
            "version 3, with no warranty. See the LICENSE file.\n",
        )

    def _export_current_tab(self):
        self._dispatch_tab_action(_EXPORTABLE_TABS)

    def _import_current_tab(self):
        self._dispatch_tab_action(_IMPORTABLE_TABS)

    def _dispatch_tab_action(self, method_names_by_tab: dict):
        """Forwards to the named method on whichever tab is currently
        active, if that tab is in `method_names_by_tab` at all -- see
        the module-level _EXPORTABLE_TABS/_IMPORTABLE_TABS comment
        above. Every target method already guards against "no memory
        loaded" / "no selection" itself (the same guards that protect
        double-click, which also bypasses button state), so this is
        safe to call unconditionally."""
        name = self.tabview.get()
        method_name = method_names_by_tab.get(name)
        if method_name is None:
            return
        getattr(self._tabs[name], method_name)()

    # -- File > Open Recent ----------------------------------------------

    def _rebuild_recent_files_menu(self):
        """Repopulates File > Open Recent from
        self.config_store.recent_files -- called once at startup
        (_build_menus()) and again every time the list changes (a
        state opened/saved, a stale entry pruned in open_state_file(),
        or Clear Recent Files). Tk menu items are static once added,
        so the submenu has to be torn down and rebuilt rather than
        updated in place."""
        self._recent_menu.delete(0, "end")
        recent = self.config_store.recent_files
        if not recent:
            self._recent_menu.add_command(label="(No Recent Files)", state="disabled")
            return
        for path_str in recent:
            self._recent_menu.add_command(
                label=_format_recent_label(path_str),
                command=lambda p=path_str: self.open_state_file(p),
            )
        self._recent_menu.add_separator()
        self._recent_menu.add_command(
            label="Clear Recent Files", command=self._clear_recent_files
        )

    def _clear_recent_files(self):
        self.config_store.clear_recent_files()
        self._persist_config("recent files")
        self._rebuild_recent_files_menu()

    def _persist_config(self, context: str):
        """Saves self.config_store to disk, surfacing a failure the
        same way _connect_and_verify's own preference-save already
        does -- these config writes are rare enough (once per state
        opened/saved) that a real failure (e.g. a read-only home
        directory) is worth telling the user about rather than
        silently dropping."""
        try:
            self.config_store.save()
        except Exception as e:
            logger.error(
                "Could not save %s to %s: %s",
                context, self.config_store.PREFS_FILE, e
            )
            messagebox.showerror(
                "Could Not Save Preferences", f"Could not save {context}: {e}"
            )

    def _set_status(self, text: str):
        self._status_label.configure(text=text)

    def _on_memory_changed(self):
        self.memory.is_modified()
        self._modified_label.configure(text="* Modified")
        # The tab that made this edit already re-rendered itself (each
        # tab's own edit handler calls its render() directly, for
        # immediate feedback) -- just mark every *other* tab stale so it
        # picks up the change next time it's actually shown, instead of
        # re-rendering tabs nobody's looking at right now.
        active = self.tabview.get()
        for name in self._tabs_dirty:
            if name != active:
                self._tabs_dirty[name] = True

    def _on_tab_changed(self):
        self._render_active_tab()

    def _render_active_tab(self):
        '''Renders only the tab currently on screen, if it's stale.

        Tabs render lazily rather than all at once: building a tab's
        widgets from scratch for a big register table is genuinely slow
        (see the module docstring), so there's no reason to pay that cost
        for tabs the user hasn't looked at yet, or to pay it twice for a
        tab that's already showing current content.
        '''
        name = self.tabview.get()
        if not self._tabs_dirty.get(name, True):
            return
        self._tabs[name].render(self.memory)
        self._tabs_dirty[name] = False

    def _render_tabs(self):
        '''Invalidates every tab (e.g. after loading a whole new state) and
        immediately re-renders whichever one is currently visible; the
        rest pick up the new memory next time they're selected.'''
        for name in self._tabs_dirty:
            self._tabs_dirty[name] = True
        self._render_active_tab()

    def _update_source_label(self):
        text = (
            str(self.memory_source) if self.memory_source else "(new, unsaved buffer)"
        )
        self._source_label.configure(text=text)

    # -- Engine pump (drives CommandEngine off the Tk main loop) ---------

    def _start_engine_pump(self):
        self._pump_engine()

    def _pump_engine(self):
        try:
            while self.engine.process_incoming_data():
                pass
        except Exception as e:
            logger.error("Engine pump error: %s", e)
        self.after(ENGINE_POLL_MS, self._pump_engine)

    # -- Connection -------------------------------------------------------

    def _prompt_for_port(self, message: str):
        dialog = PortSelectionDialog(
            self,
            self.serial,
            default_port=self.config_store.serial_port,
            message=message,
        )
        self.wait_window(dialog)
        if dialog.result:
            self._connect_and_verify(dialog.result)
        elif self.serial.is_connected:
            # Cancelling out of Reconnect leaves the existing connection
            # untouched -- don't overwrite the status bar with "Not
            # connected".
            self._set_status(f"Connected to {self.serial.serial_inst.port}")
        else:
            self._set_status(self._idle_status())

    def show_connect_dialog(self):
        if not self._require_serial_mode():
            return
        # _prompt_for_port() blocks in wait_window() until the dialog closes.
        # Called straight from a menu item, that nested wait runs inside
        # macOS's menu-tracking event loop mode, which never delivers events
        # to the new window: the dialog shows, but can't be clicked or
        # closed, and the app gets the spinning beachball. Returning from the
        # menu command first lets the dialog run in the normal event loop.
        self.after(self.CONNECT_DIALOG_DELAY_MS, lambda: self._prompt_for_port(None))

    def disconnect(self):
        if not self.serial.is_connected:
            return
        self.serial.disconnect()
        self._set_status(self._idle_status())
        self._battery_label.configure(text="")
        self._calc_time_label.configure(text="")

    def _connect_and_verify(self, port: str):
        self._set_status(f"Connecting to {port}...")
        success, message = self.serial.connect(
            port, baudrate=self.config_store.baudrate
        )
        if not success:
            self._set_status(self._idle_status())
            self._prompt_for_port(f"Connection to '{port}' failed: {message}")
            return

        self.config_store.serial_port = port
        try:
            self.config_store.save()
        except Exception as e:
            logger.error("Could not save preferences to %s: %s",
                         self.config_store.PREFS_FILE, e)
            messagebox.showerror("Could not save preferences",
                                 f"{self.config_store.PREFS_FILE}, {e}")
        self._set_status(f"Connected to {port} -- verifying...")

        self.engine.execute(
            BatteryCheckCommand(timeout=2.0),
            self._on_startup_battery,
            self._on_verify_failed,
        )

    def _on_verify_failed(self, message: str):
        # This callback runs on the main Tk thread (via the engine pump in
        # _pump_engine), so it's safe to call disconnect() directly here --
        # unlike _handle_serial_error above, which runs on SerialManager's
        # own background thread and can't join itself.
        if self.serial.is_connected:
            self.serial.disconnect()
        self._set_status(self._idle_status())
        self.after(
            0,
            lambda: self._prompt_for_port(
                f"Connected to the port, but the DM41L did not respond ({message}). "
                "Please select a different serial port."
            ),
        )

    # -- Startup sequence: battery -> timeout -> time -> memory state

    def _on_startup_battery(self, voltage_mv):
        logger.info("Battery: %s", voltage_mv)
        self._battery_label.configure(text=f"Battery: {voltage_mv} mV")
        self._set_status(f"Connected to {self.serial.serial_inst.port}")
        self.after(
            10,
            lambda: self.engine.execute(
                ConsoleTimeoutCommand([self.config_store.console_timeout_minutes]),
                self._on_timeout_command,
                self._on_command_error,
            ),
        )

    def _on_timeout_command(self, result):
        logger.info("Timeout: %s", result)
        self.after(
            10,
            lambda: self.engine.execute(
                GetTimeCommand(), self._on_startup_time, self._on_command_error
            ),
        )

    def _on_startup_time(self, time_str):
        logger.info("Time: %s", time_str)
        self._calc_time_label.configure(text=f"Calc time: {time_str}")
        self.after(10, self._fetch_memory_state)

    def _fetch_memory_state(self):
        if self.memory_source is None and not self.memory.modified:
            self._set_status("Reading memory state...")
            self.after(
                10,
                lambda: self.engine.execute(
                    MemoryStringCommand(),
                    self._on_auto_state_received,
                    self._on_command_error,
                ),
            )

        # We've already got a modified memory state loaded. Just return.
        self._set_status(f"Connected to {self.serial.serial_inst.port}")
        return

    def _on_auto_state_received(self, state):
        # Re-check right at the moment the fetched state is about to be
        # applied. It's a small race condition, but it does exist.
        if self.memory_source is not None or self.memory.modified:
            logger.info(
                "Discarding auto-connect's fetched state: a file was opened "
                "in the meantime."
            )
            self._set_status(f"Connected to {self.serial.serial_inst.port}")
            return
        self._on_state_received(state)

    def _on_state_received(self, state):
        logger.info("State received.")
        try:
            # The profile is the connection driver's declared model (the
            # serial driver declares the DM41L), not the app's mode: this
            # state came off that calculator. The two agree today, since
            # only DM41L mode can connect at all.
            self.memory = Memory.from_string(state, profile=self.serial.profile)
            self.memory_source = None
            self._modified_label.configure(text="")
            self._update_source_label()
            self._render_tabs()
            self._set_status("Connected -- memory state loaded.")
        except Exception as e:
            self._on_command_error(f"Failed to parse memory state: {e}")

    # -- Command callbacks -------------------------------------------------

    def _on_command_error(self, message: str):
        logger.error("%s", message)
        self._command_pending = False
        self._set_status(self._idle_status())
        # Runs on the main Tk thread (engine callback via _pump_engine), so
        # a direct disconnect() call here is safe -- see _on_verify_failed.
        if self.serial.is_connected:
            self.serial.disconnect()
        # Deferred for the same reason as the previous GUI: showing a modal
        # dialog immediately would open a nested Tk event loop while the
        # engine is mid-reset, risking reentrant calls into
        # process_incoming_data().
        self.after(0, lambda: messagebox.showerror("Error", message))

    # -- File menu actions --------------------------------------------------

    def new_memory_buffer(self):
        if self.memory.modified and not messagebox.askyesno(
            "Unsaved Changes", "Discard unsaved changes and start a new buffer?"
        ):
            return
        self.memory = Memory(profile=self.profile)
        self.memory_source = None
        self._modified_label.configure(text="")
        self._update_source_label()
        self._render_tabs()
        self._set_status("Started a new, empty memory buffer.")

    # -- Tools menu actions --------------------------------------------------

    def pack_memory(self):
        '''GitHub issue #31 ("DM41L_Explorer needs PACK functionality"):
        explicitly repacks Key Assignments/Alarms and program memory
        (Memory.pack() -- see that method's own docstring for exactly
        what it does and why, including the chain-repair case per the
        user's own correction to this method's first version). Meant to
        be run before an Import to guarantee the maximum possible free
        space is available for it, per the issue's own suggested use --
        lives in its own menu (rather than on the Program tab, next to
        Import) since it also touches Key Assignments/Alarms, which
        aren't shown there.'''
        if self.memory is None:
            messagebox.showwarning(
                "No Memory Loaded", "Load or start a memory buffer first."
            )
            return
        before_count = len(self.memory.programs.list_programs())
        try:
            freed = self.memory.pack()
        except Exception as e:
            logger.warning("Could not pack memory: %s", e)
            messagebox.showerror("Could Not Pack Memory", str(e))
            return
        recovered = len(self.memory.programs.list_programs()) - before_count
        logger.info(
            "Packed memory: %d register(s) reclaimed, %d program(s) recovered",
            freed, recovered,
        )
        self._on_memory_changed()
        self._render_tabs()
        if freed > 0 or recovered > 0:
            summary = f"{freed} register(s) reclaimed"
            if recovered > 0:
                summary += (
                    f", {recovered} program(s) recovered -- their global "
                    "labels are now visible and can be assigned to a key"
                )
            self._set_status(f"Packed memory -- {summary}.")
            messagebox.showinfo("Pack Memory", f"Packed memory: {summary}.")
        else:
            self._set_status("Packed memory -- already fully packed.")
            messagebox.showinfo(
                "Pack Memory",
                "Memory is already fully packed -- nothing to reclaim.",
            )

    def set_calculator_time(self):
        if not self._require_serial_mode():
            return
        if not self.serial.is_connected:
            messagebox.showwarning("Not Connected", "Connect to the DM41L first.")
            return
        now = datetime.now()
        args = [now.strftime("%Y%m%d"), now.strftime("%H%M%S")]
        self.engine.execute(
            SetTimeCommand(args), self._on_time_set, self._on_command_error
        )

    def _on_time_set(self, result):
        logger.info("Time set: %s", result)
        self._calc_time_label.configure(text="Calc time set to system time")
        self.after(
            0,
            lambda: messagebox.showinfo(
                "Success", "Calculator clock set to system time."
            ),
        )

    def save_state_to_file(self):
        if self.memory_source is None:
            self.save_state_as()
            return
        if not messagebox.askyesno(
            "Overwrite File", f"Overwrite {self.memory_source} with your changes?"
        ):
            return
        try:
            self.memory.to_file(self.memory_source)
            self._modified_label.configure(text="")
            logger.info("State saved to %s", self.memory_source)
            self.config_store.add_recent_file(self.memory_source)
            self._persist_config("recent files")
            self._rebuild_recent_files_menu()
            messagebox.showinfo("Saved", f"Memory state written to {self.memory_source}")
        except Exception as e:
            logger.exception("Could not save state to %s", self.memory_source)
            messagebox.showerror("Error", f"Could not save state: {e}")

    def save_state_as(self):
        # A file keeps its own extension: the text format is the same. A
        # buffer with no file of its own gets the extension the current
        # mode's calculator writes (.dm41 for a DM41L, .d41 for a DM41X).
        extension = (
            self.memory_source.suffix
            if self.memory_source and self.memory_source.suffix in STATE_EXTENSIONS
            else DEFAULT_EXTENSION_BY_MODE[self.mode]
        )
        path = filedialog.asksaveasfilename(
            defaultextension=extension,
            filetypes=STATE_FILETYPES,
        )
        if not path:
            return
        try:
            self.memory.to_file(path)
            self.memory_source = Path(path)
            self._modified_label.configure(text="")
            self._update_source_label()
            logger.info("State saved to %s", path)
            self.config_store.add_recent_file(path)
            self._persist_config("recent files")
            self._rebuild_recent_files_menu()
            messagebox.showinfo("Saved", f"Memory state written to {path}")
        except Exception as e:
            logger.exception("Could not save state to %s", path)
            messagebox.showerror("Error", f"Could not save state: {e}")

    def load_state_from_file(self):
        if self.memory.modified and not messagebox.askyesno(
            "Unsaved Changes", "Discard unsaved changes and load a different state?"
        ):
            return
        path = filedialog.askopenfilename(filetypes=STATE_FILETYPES)
        if not path:
            return
        self._load_state_into_buffer(path)

    def _load_state_into_buffer(self, path):
        '''Reads `path` into self.memory and refreshes the UI to match.
        This is the actual load step shared by every way of opening a state
        (File > Open..., a startup file argument, and double-clicking a
        .dm41 file) -- callers are responsible for checking `self.memory.modified`
        and confirming with the user first, since the right prompt (or
        whether to prompt at all) differs by caller.

        A state is opened with the current mode's profile. If it does not
        fit that model, the user is offered DM41X mode instead
        (_offer_mode_switch_for_load); declining leaves the open state
        alone, so nothing is assigned here until that is settled.'''
        try:
            # Parsed into a local first: the state only replaces the open
            # buffer once it is known to fit the mode (or the user has
            # agreed to switch), so a declined offer is a true no-op.
            loaded = Memory.from_file(path, profile=self.profile)
        except Exception as e:
            logger.exception("Could not load state from %s", path)
            messagebox.showerror("Error", f"Could not load state: {e}")
            return

        findings = evaluate_mode_switch(loaded, self.profile)
        if findings:
            if not self._offer_mode_switch_for_load(path, findings):
                return
            # The mode moved, so the state has to be re-read under the new
            # profile: the one just parsed still carries the old memory map.
            try:
                loaded = Memory.from_file(path, profile=self.profile)
            except Exception as e:
                logger.exception("Could not load state from %s", path)
                messagebox.showerror("Error", f"Could not load state: {e}")
                return

        try:
            self.memory = loaded
            self.memory_source = Path(path)
            self._modified_label.configure(text="")
            self._update_source_label()
            self._render_tabs()
            name = Path(path).name
            self._set_status(f"Loaded state from {name}")
            logger.info("State loaded from %s (%s mode)", path, self.mode.value)
            self.config_store.add_recent_file(path)
            self._persist_config("recent files")
            self._rebuild_recent_files_menu()
        except Exception as e:
            logger.exception("Could not load state from %s", path)
            messagebox.showerror("Error", f"Could not load state: {e}")

    def open_state_file(self, path):
        '''Opens `path` as the app's current state, prompting to discard
        unsaved changes first if needed (same prompt File > Open... uses).

        Public entry point for anything that hands the app a file path
        directly, bypassing the File > Open... dialog: a startup file
        argument (double-clicking a .dm41 file on Windows/Linux, or
        launching fresh via double-click on macOS -- see
        `_handle_startup_file_arg`/main() below) and the macOS "Open
        Document" AppleEvent for double-clicking a .dm41 file while the
        app is already running (see `_on_mac_open_document` below).'''
        if not Path(path).exists():
            logger.warning("Could not find file: %s", path)
            messagebox.showerror("Error", f"Could not find file: {path}")
            self.config_store.remove_recent_file(path)
            self._persist_config("recent files")
            self._rebuild_recent_files_menu()
            return
        if self.memory.modified and not messagebox.askyesno(
            "Unsaved Changes", "Discard unsaved changes and load a different state?"
        ):
            return
        self._load_state_into_buffer(path)

    def _on_mac_open_document(self, *paths):
        '''Handles macOS's "Open Document" AppleEvent -- what Finder sends
        for double-clicking a .dm41 file, whether that launches the app
        fresh or the app is already running. Tk surfaces this as a
        registered command (wired in __init__) rather than a normal
        widget-level event or sys.argv, per
        https://www.tcl.tk/man/tcl/TkCmd/tk_mac.html.'''
        if not paths:
            return
        # Finder can in principle hand over more than one path at once
        # (e.g. multi-selecting files and choosing Open); this app only
        # has one buffer, so just open the last one -- "last one wins" is
        # the least surprising choice for a single-document app.
        self.open_state_file(paths[-1])

    # -- Connect menu actions -----------------------------------------------

    def send_state_to_calculator(self):
        if not self._require_serial_mode():
            return
        if not self.serial.is_connected:
            messagebox.showwarning("Not Connected", "Connect to the DM41L first.")
            return

        # A state can hold things the calculator on the other end does not
        # have. The connection declares which model that is -- not the
        # app's mode; see memory/profile_fit.py.
        #
        # Since phase 6 the XM *error* branch below is defence in depth
        # rather than a path a user can reach: connecting needs DM41L
        # mode, and opening a state too large for a DM41L in DM41L mode
        # forces a switch to DM41X mode, which disconnects. The check is
        # kept because it is shared with evaluate_mode_switch() and costs
        # nothing, and because a later model (a DM41XN over serial) could
        # make the combination reachable again. The XROM *warning* branch
        # is still reached normally: a state full of DM41X-only functions
        # opens in DM41L mode quite happily.
        target = self.serial.profile
        findings = check_profile_fit(self.memory, target)
        errors = [f for f in findings if f.level == ERROR]
        warnings = [f for f in findings if f.level == WARNING]
        for finding in findings:
            logger.warning("Send check (%s): %s", finding.level, finding)
        if errors:
            messagebox.showerror(
                f"Cannot Send to {target.name}",
                f"This memory state cannot be sent to a {target.name}:\n\n"
                f"{format_findings(errors)}\n\nNothing was sent.",
            )
            return

        message = (
            "This will overwrite the calculator's current memory with the "
            "currently loaded state."
        )
        if warnings:
            message += (
                f"\n\nThese will not work on a {target.name}:\n\n"
                f"{format_findings(warnings)}\n\nSend it anyway?"
            )
        else:
            message += " Continue?"
        if not messagebox.askyesno("Send State to Calculator", message):
            return

        state_text = self.memory.to_string()

        command = LoadMemoryStringCommand([state_text], serial=self.serial)
        accepted = self.engine.execute(
            command,
            self._on_state_sent,
            self._on_io_failed,
        )
        if not accepted:
            messagebox.showwarning(
                "Busy",
                "Another command is still in progress. Please wait a moment and try again.",
            )
            return
        command.trigger_transfer()

    def _on_state_sent(self, result):
        logger.info("State sent: %s", result)
        self.after(
            0,
            lambda: messagebox.showinfo("Sent", "Memory state sent to the calculator."),
        )

    def _on_io_failed(self, message):
        self._on_command_error(message)

    def get_state_from_calculator(self):
        if not self._require_serial_mode():
            return
        if not self.serial.is_connected:
            messagebox.showwarning("Not Connected", "Connect to the DM41L first.")
            return
        if self.memory.modified and not messagebox.askyesno(
            "Unsaved Changes",
            "Discard unsaved changes and read the calculator's memory?",
        ):
            return
        self._set_status("Reading memory state...")
        self.after(
            10,
            lambda: self.engine.execute(
                MemoryStringCommand(), self._on_state_received, self._on_io_failed
            ),
        )

    def show_preferences(self):
        PreferencesDialog(
            self,
            self.config_store,
            self.serial,
            on_saved=self._on_preferences_saved,
            mode=self.config_store.mode,
        )

    def show_keyboard_shortcuts(self):
        KeyboardShortcutsDialog(self)

    def show_mnemonics_reference(self):
        """Opens the non-modal mnemonics reference, or raises the one
        already open."""
        dialog = getattr(self, "_mnemonics_reference", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.deiconify()
            dialog.lift()
            dialog.focus_set()
            return dialog
        self._mnemonics_reference = MnemonicsReferenceDialog(self)
        return self._mnemonics_reference

    def _on_preferences_saved(self, requested_mode=None):
        """Re-applies everything the dialog may have changed.

        `requested_mode` is the mode the user chose there, which the dialog
        deliberately does NOT write to the config itself: changing mode
        erases the open state, so it has to go through set_mode()'s
        confirmation, and a cancelled switch must leave the saved
        preference as it was. The dialog's other settings are already
        saved by the time this runs, so cancelling the switch keeps those
        and only the mode change is dropped."""
        if requested_mode is not None and requested_mode is not self.mode:
            self.set_mode(requested_mode, persist=True)
        elif requested_mode is not None and self.mode_is_temporary:
            # The dialog's value matches the mode already in force, so the
            # user has confirmed this session's override as the saved
            # setting. Nothing to erase; just stop calling it temporary.
            self.config_store.mode = requested_mode
            self._persist_config("the DM41L/DM41X mode")
            self._refresh_mode_indicator()

        ctk.set_appearance_mode(self.config_store.appearance_mode)
        _setup_logging(self.config_store)
        # Hex View, Data Registers, XM Files, and Programs use native
        # ttk.Treeview widgets, which CTk's theme engine has no hook into
        # -- set_appearance_mode() above does nothing for their region/
        # stripe colors on its own, so they need to be told explicitly to
        # recompute and re-apply them (see each tab's refresh_theme() for
        # why -- GitHub issues #21/#22's Treeview conversions are what
        # grew this list from two tabs to four). Overview/Flags/Key
        # Assignments don't need an equivalent call: their CTk-widget
        # render()s already recompute stripe color fresh every time, so
        # marking them stale and re-rendering the active tab below (the
        # same thing F5/"Refresh Tabs" already does) is enough to pick up
        # the new theme.
        self.hex_view_tab.refresh_theme()
        self.data_registers_tab.refresh_theme()
        self.xm_files_tab.refresh_theme()
        self.program_tab.refresh_theme()
        self.alarms_tab.refresh_theme()
        self._render_tabs()

    # -- Shutdown -----------------------------------------------------------

    def on_close(self):
        if self._closing:
            return  # e.g. Cmd+Q delivered by two routes at once
        if self.memory.modified and not messagebox.askyesno(
            "Unsaved Changes", "Discard unsaved changes and quit?"
        ):
            return
        self._closing = True
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
            self._resize_after_id = None
        self._remember_window_size()
        # Runs on the main Tk thread (window-close handler), so a direct
        # disconnect() call here is safe -- see _on_verify_failed.
        if self.serial.is_connected:
            self.serial.disconnect()
        self.destroy()


def _handle_startup_file_arg(app):
    '''Opens a state file passed on the command line at launch, if any --
    the Windows/Linux half of "double-click a .dm41 file to open it": file
    associations on those platforms launch the app with the file's path as
    an argument. macOS instead delivers this via an "Open Document"
    AppleEvent (see `DM41ExplorerApp._on_mac_open_document`), which
    doesn't go through sys.argv at all, but this also covers running the
    app directly from a shell with a file argument on any platform.

    Ignores anything that looks like a flag (starts with "-") rather than
    a path, since this app has no other command-line options of its own to
    parse -- a stray flag shouldn't be treated as a file to open.
    '''
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        app.open_state_file(sys.argv[1])


def main():
    app = DM41ExplorerApp()
    logger.info(
        "DM41_Explorer %s starting (Python %s, %s)",
        APP_VERSION,
        platform.python_version(),
        platform.platform(),
    )
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    _handle_startup_file_arg(app)
    app.mainloop()


if __name__ == "__main__":
    main()
