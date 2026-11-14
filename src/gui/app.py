"""
DM41_Explorer: a CustomTkinter GUI for DM41-series calculator memory states.

"""

import logging
import logging.handlers
import sys
from pathlib import Path
import platform
from tkinter import filedialog, messagebox
from typing import NamedTuple, Optional

import customtkinter as ctk

from memory import DeviceMode, Memory, evaluate_mode_switch
from memory.mnemonics import set_active_libraries
from memory.xrom_import import user_library_paths
from memory.xrom_library import WARNING as XROM_WARNING
from memory.xrom_library import active_set, find_conflicts, load_libraries

from config import ProjectConfig
from gui import fonts
from gui.fonts import font_for
from gui.connection import Connection
from gui.contrast import SECONDARY_TEXT, WARNING_TEXT, improve_theme_contrast
from gui.dialog_common import ask_yes_no
from gui.menus import MenuBar
from gui.screens import screen_rectangles
from gui.window_geometry import (
    geometry_string,
    initial_window_size,
    minimum_window_size,
    parse_geometry,
    parse_position,
    restored_position,
)
from gui.preferences_dialog import PreferencesDialog
from gui.xrom_libraries_dialog import XromLibrariesDialog
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

PLATFORM_SYSTEM = platform.system()


class TabSpec(NamedTuple):
    """One of the main window's tabs. `attr` is the DM41ExplorerApp
    attribute that holds it. `editable` tabs get the app's on_change
    callback. `export_method`/`import_method` name the tab's own header-
    button handlers that Cmd/Ctrl+E / Cmd/Ctrl+I forward to while it is
    the active tab (see dispatch_tab_action()); None means that shortcut
    is a silent no-op there."""

    name: str
    attr: str
    cls: type
    editable: bool = True
    export_method: Optional[str] = None
    import_method: Optional[str] = None


# The main window's tabs, in display order.
TABS = (
    TabSpec("Overview", "overview_tab", OverviewTab),
    TabSpec("Flags", "flags_tab", FlagsTab),
    TabSpec(
        "Programs",
        "program_tab",
        ProgramTab,
        export_method="_export_selected",
        import_method="_import_program",
    ),
    TabSpec(
        "Key Assignments",
        "key_assignments_tab",
        KeyAssignmentsTab,
        export_method="_save_pdf",
    ),
    TabSpec("Alarms", "alarms_tab", AlarmsTab),
    TabSpec(
        "Data Registers",
        "data_registers_tab",
        DataRegistersTab,
        export_method="_export_registers",
        import_method="_import_registers",
    ),
    TabSpec(
        "XM Files",
        "xm_files_tab",
        XMFilesTab,
        export_method="_export_selected",
        import_method="_import_file",
    ),
    TabSpec("Hex View", "hex_view_tab", HexViewTab, editable=False),
)

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
    """Loads the CustomTkinter color theme `name` (a built-in theme's name,
    or the path of a theme JSON file). A theme that can't be loaded -- a
    hand-edited preference naming a file that is gone, say -- is logged and
    replaced by the default theme instead of stopping the app from
    starting. Must run before any widget is built."""
    try:
        ctk.set_default_color_theme(name)
    except (OSError, ValueError) as e:
        default = ProjectConfig.DEFAULT_PREFS["color_theme"]
        logger.warning("Could not load color theme %r, using %r: %s", name, default, e)
        ctk.set_default_color_theme(default)


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
    """Main application window."""

    # How long after the last resize event the window size is saved.
    RESIZE_SAVE_DELAY_MS = 1000

    def __init__(self):
        super().__init__()

        self._closing = False
        self._tracking_size = False
        self._applied_size = None
        self._applied_position = None
        self._resize_after_id = None
        self._tracking_after_id = None

        self.config_store = ProjectConfig()
        try:
            self.config_store.load()
        except Exception as e:
            messagebox.showerror("Unable to load preferences", str(e))

        _setup_logging(self.config_store)

        ctk.set_appearance_mode(self.config_store.appearance_mode)
        _apply_color_theme(self.config_store.color_theme)
        fonts.apply(self.config_store, self)
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
        # The XROM libraries arranged for that mode, before anything shows
        # an instruction name (GitHub issue #53).
        xrom_errors = self._apply_xrom_libraries()

        self.connection = Connection(self)
        self.memory = Memory(profile=self.profile)
        self.memory_source = None

        self._build_layout()
        self._apply_window_size()
        self.menus = MenuBar(self, is_mac=PLATFORM_SYSTEM == "Darwin")
        self.menus.set_serial_enabled(self.mode.supports_serial)
        self.menus.bind_shortcuts()
        if xrom_errors:
            self.set_status(
                f"{len(xrom_errors)} XROM library file(s) could not be loaded; "
                "see Tools > XROM Libraries."
            )

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

        self.connection.start()
        self.render_tabs()

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
            # The status bar is the one place a temporary override is
            # shown; the Preferences dialog shows the saved setting.
            text += " (this session)"
        return text

    def idle_status(self) -> str:
        """The status text for "nothing going on": the connection state in
        a mode that has a serial console, and nothing at all in one that
        does not.

        "Not connected" is only meaningful where connecting is possible.
        In DM41X mode there is nothing to connect to,
        so the status bar stays empty until something actually happens
        (a state loaded, a mode changed), rather than reporting the
        absence of a connection the app would refuse to make anyway."""
        return "Not connected" if self.mode.supports_serial else ""

    def _refresh_mode_indicator(self):
        self._mode_label.configure(text=self._mode_status_text())

    def set_mode(self, mode, *, persist: bool, reason: str = None) -> bool:
        """Changes the effective mode, erasing the open state.

        Changing mode starts a new, empty state on the new mode's profile:
        a Memory fixes its profile when it is built, and
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

        if not self.confirm_discard(f"switch to {mode.value} mode"):
            return False

        lines = []
        if reason:
            lines.append(reason)
        lines.append(
            f"Switching to {mode.value} mode starts a new, empty memory "
            "state. Anything still in the current one is discarded."
        )
        connected = self.connection.serial.is_connected
        if connected:
            lines.append("The serial connection will be closed.")
        lines.append(
            "To move programs or data between models, export them before "
            "switching and import them afterwards."
        )
        lines.append(f"Switch to {mode.value} mode?")
        # Wider than messagebox.askyesno(), for the list of findings an
        # auto-switch's `reason` carries (GitHub issue #55).
        if not ask_yes_no(self, "Switch Mode", "\n\n".join(lines)):
            return False

        if connected:
            self.connection.disconnect()

        previous = self.mode
        self.mode = mode
        # Each mode has its own built-in library and its own arrangement.
        self._apply_xrom_libraries()
        if persist:
            self.config_store.mode = mode
            self.persist_config("the DM41L/DM41X mode")
        logger.info(
            "Mode changed from %s to %s (%s)",
            previous.value,
            mode.value,
            "saved" if persist else "this session only",
        )

        self.menus.set_serial_enabled(self.mode.supports_serial)
        self._refresh_mode_indicator()
        self.replace_memory(Memory(profile=self.profile))
        self.set_status(
            f"Switched to {mode.value} mode; started a new, empty memory state."
        )
        return True

    def _offer_mode_switch_for_load(self, path, findings) -> bool:
        """Offers DM41X mode for a state that will not fit the current one,
        and returns whether the caller should go on to load it.

        The offer is deliberately session-only: someone who owns one
        calculator will rarely want the other mode permanently, so
        accepting does not write the preference. Declining aborts the load
        and leaves both the mode and the open state alone."""
        target = DeviceMode.DM41X
        logger.info(
            "%s does not fit %s mode: %s",
            path,
            self.mode.value,
            "; ".join(str(finding) for finding in findings),
        )
        # The findings are for the log, above: to decide whether to switch,
        # the user only needs to know the state is too big (issue #60).
        reason = (
            f"{Path(path).name} needs more memory than a {self.mode.value} has."
            f"\n\n{target.value} mode can open it."
        )
        if not self.set_mode(target, persist=False, reason=reason):
            self.set_status(f"Did not load {Path(path).name}: needs DM41X mode.")
            return False
        return True

    # -- Window size ------------------------------------------------------

    def _apply_window_size(self):
        """Sets the minimum size and the opening size (GitHub issue #42).

        Must run after _build_layout(): the minimum is never less than what
        the tab bar and status bar ask for, and both grow with the
        application font. The opening size is the one saved at the last
        close if there is one, else a default scaled with the font; see
        gui/window_geometry.py for the rules.
        """
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
        # The position saved at the last close too, if the window would
        # still be on a screen there (GitHub issue #63); otherwise the
        # window manager places it.
        position = restored_position(
            self.config_store.window_position, (width, height), screen_rectangles(self)
        )
        self.geometry(geometry_string((width, height), position))
        self._applied_size = (width, height)
        self._applied_position = position
        # Let the window manager finish placing the window before resizes
        # count as the user's: the size is then re-read, and every later
        # change is saved shortly after it stops (see _on_configure()).
        self._tracking_after_id = self.after(2000, self._start_tracking_size)

    def _start_tracking_size(self):
        self._tracking_after_id = None
        self.update_idletasks()
        geometry = self.geometry()
        self._applied_size = parse_geometry(geometry) or self._applied_size
        self._applied_position = parse_position(geometry) or self._applied_position
        self._tracking_size = True
        self.bind("<Configure>", self._on_configure, add="+")

    def _on_configure(self, event):
        """Saves the window size a moment after the user stops resizing, so
        it survives however the app ends (force quit, crash, a quit path
        that never reaches on_close())."""
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
        """Cancels the pending size timers first, so none fires into a
        window that no longer exists."""
        for name in ("_tracking_after_id", "_resize_after_id"):
            after_id = getattr(self, name, None)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass
                setattr(self, name, None)
        super().destroy()

    def _window_is_maximized(self) -> bool:
        """True if the window is maximized/zoomed, whose size must not be
        remembered (restoring it would open a normal window at full-screen
        size)."""
        try:
            if self.state() == "zoomed":
                return True
        except Exception:
            pass
        try:
            return bool(self.attributes("-zoomed"))  # X11
        except Exception:
            return False

    def _remember_window_size(self):
        """Saves the current window size and position to the preferences,
        for the next launch. Only what the user changed is saved, and the
        position only once tracking has started: until then the window
        manager may still be placing the window. Never raises: failing to
        save mustn't stop the app from closing."""
        try:
            if self._window_is_maximized():
                return
            geometry = self.geometry()
            size = parse_geometry(geometry)
            position = parse_position(geometry) if self._tracking_size else None
            new_size = size is not None and size != self._applied_size
            new_position = position is not None and position != self._applied_position
            if not (new_size or new_position):
                return  # nothing the user changed: don't touch the file
            if new_size:
                self.config_store.window_size = size
            if new_position:
                self.config_store.window_position = position
            self.config_store.save()
            # Only once saved, so a failed save is tried again later.
            if new_size:
                self._applied_size = size
            if new_position:
                self._applied_position = position
        except Exception as e:
            logger.warning("Could not save the window size and position: %s", e)

    # -- Layout ---------------------------------------------------------

    def _build_layout(self):
        # Status bar first, pinned to the bottom, so the tab view above it
        # gets whatever space is left -- packing order (not just `side`)
        # is what keeps it pinned regardless of window resizing.
        status_bar = ctk.CTkFrame(self)
        status_bar.pack(side="bottom", fill="x", padx=8, pady=(0, 8))

        # The effective mode comes first: it is the
        # standing fact about the session, while the label beside it is a
        # running commentary that set_status() rewrites constantly. This
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
            font=font_for("ui"),
            text_color=SECONDARY_TEXT,
        )
        self._mode_label.pack(side="left", padx=8, pady=6)
        self._status_label = ctk.CTkLabel(
            status_bar, text=self.idle_status(), font=font_for("ui")
        )
        self._status_label.pack(side="left", padx=8, pady=6)
        self._modified_label = ctk.CTkLabel(
            status_bar, text="", font=font_for("ui"), text_color=WARNING_TEXT
        )
        self._modified_label.pack(side="left", padx=8, pady=6)
        self._battery_label = ctk.CTkLabel(status_bar, text="", font=font_for("ui"))
        self._battery_label.pack(side="right", padx=16, pady=6)
        self._calc_time_label = ctk.CTkLabel(status_bar, text="", font=font_for("ui"))
        self._calc_time_label.pack(side="right", padx=16, pady=6)
        self._source_label = ctk.CTkLabel(
            status_bar,
            text="(new, unsaved buffer)",
            font=font_for("ui"),
            text_color=SECONDARY_TEXT,
        )
        self._source_label.pack(side="right", padx=16, pady=6)

        self.tabview = ctk.CTkTabview(self, command=self._on_tab_changed)
        self.tabview.pack(side="top", fill="both", expand=True, padx=8, pady=8)
        # The tabs by name -- also which ones are showing stale content,
        # see _render_active_tab().
        self._tabs = {}
        for spec in TABS:
            self.tabview.add(spec.name)
            kwargs = {"on_change": self._on_memory_changed} if spec.editable else {}
            tab = spec.cls(self.tabview.tab(spec.name), **kwargs)
            tab.pack(fill="both", expand=True)
            setattr(self, spec.attr, tab)
            self._tabs[spec.name] = tab
        self._tabs_dirty = {name: True for name in self._tabs}
        self.program_tab.raw_checksum_enabled = lambda: self.config_store.raw_checksum
        self.key_assignments_tab.source_path = lambda: self.memory_source

    def show_about(self):
        messagebox.showinfo(
            "About DM41_Explorer",
            f"Read, Write and Manipulate\n"
            "DM41-series memory states\n\n"
            f"Version:\n{APP_VERSION}\n\n"
            "Written by Michael Heinz.\n\n"
            "Free software under the GNU General Public License,\n"
            "version 3, with no warranty. See the LICENSE file.\n",
        )

    def dispatch_tab_action(self, which: str):
        """Forwards Cmd/Ctrl+E or Cmd/Ctrl+I to the active tab's own
        export or import handler (`which` is the TabSpec field naming it),
        if it has one -- see TABS. Every target method already guards
        against "no memory loaded" / "no selection" itself (the same
        guards that protect double-click, which also bypasses button
        state), so this is safe to call unconditionally."""
        name = self.tabview.get()
        spec = next(spec for spec in TABS if spec.name == name)
        method_name = getattr(spec, which)
        if method_name is not None:
            getattr(self._tabs[name], method_name)()

    def persist_config(self, context: str):
        """Saves self.config_store to disk, surfacing a failure as
        Connection._connect_and_verify() does for the serial port --
        these config writes are rare enough (once per state
        opened/saved) that a real failure (e.g. a read-only home
        directory) is worth telling the user about rather than
        silently dropping."""
        try:
            self.config_store.save()
        except Exception as e:
            logger.error(
                "Could not save %s to %s: %s", context, self.config_store.PREFS_FILE, e
            )
            messagebox.showerror(
                "Could Not Save Preferences", f"Could not save {context}: {e}"
            )

    def set_status(self, text: str):
        self._status_label.configure(text=text)

    def set_battery_text(self, text: str):
        self._battery_label.configure(text=text)

    def set_calc_time_text(self, text: str):
        self._calc_time_label.configure(text=text)

    def _on_memory_changed(self):
        self.memory.mark_modified()
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
        """Renders only the tab currently on screen, if it's stale.

        Tabs render lazily rather than all at once: building a tab's
        widgets from scratch for a big register table is genuinely slow,
        so there's no reason to pay that cost
        for tabs the user hasn't looked at yet, or to pay it twice for a
        tab that's already showing current content.
        """
        name = self.tabview.get()
        if not self._tabs_dirty.get(name, True):
            return
        self._tabs[name].render(self.memory)
        self._tabs_dirty[name] = False

    def render_tabs(self):
        """Invalidates every tab (e.g. after loading a whole new state) and
        immediately re-renders whichever one is currently visible; the
        rest pick up the new memory next time they're selected."""
        for name in self._tabs_dirty:
            self._tabs_dirty[name] = True
        self._render_active_tab()

    def confirm_discard(self, action: str) -> bool:
        """True if there are no unsaved changes, or the user agrees to
        discard them in order to `action` (e.g. "quit")."""
        return not self.memory.modified or messagebox.askyesno(
            "Unsaved Changes", f"Discard unsaved changes and {action}?"
        )

    def replace_memory(self, memory, source=None):
        """Makes `memory` the open buffer -- read from the file `source`,
        or None for a new buffer or one read from the calculator -- and
        refreshes everything that shows it."""
        self.memory = memory
        self.memory_source = None if source is None else Path(source)
        self._modified_label.configure(text="")
        self._update_source_label()
        self.render_tabs()

    def _update_source_label(self):
        text = (
            str(self.memory_source) if self.memory_source else "(new, unsaved buffer)"
        )
        self._source_label.configure(text=text)

    # -- File menu actions --------------------------------------------------

    def new_memory_buffer(self):
        if not self.confirm_discard("start a new buffer"):
            return
        self.replace_memory(Memory(profile=self.profile))
        self.set_status("Started a new, empty memory buffer.")

    # -- Tools menu actions --------------------------------------------------

    def pack_memory(self):
        """Tools > Pack Memory: repacks Key Assignments/Alarms and program
        memory the way a real PACK does (see Memory.pack()), reporting the
        registers reclaimed and any programs whose global labels became
        visible. Useful before an Import, to make the most free space
        available. It lives in the Tools menu rather than on the Programs
        tab because it also changes Key Assignments and Alarms."""
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
            freed,
            recovered,
        )
        self._on_memory_changed()
        self.render_tabs()
        if freed > 0 or recovered > 0:
            summary = f"{freed} register(s) reclaimed"
            if recovered > 0:
                summary += (
                    f", {recovered} program(s) recovered -- their global "
                    "labels are now visible and can be assigned to a key"
                )
            self.set_status(f"Packed memory -- {summary}.")
            messagebox.showinfo("Pack Memory", f"Packed memory: {summary}.")
        else:
            self.set_status("Packed memory -- already fully packed.")
            messagebox.showinfo(
                "Pack Memory",
                "Memory is already fully packed -- nothing to reclaim.",
            )

    def save_state_to_file(self):
        if self.memory_source is None:
            self.save_state_as()
            return
        if not messagebox.askyesno(
            "Overwrite File", f"Overwrite {self.memory_source} with your changes?"
        ):
            return
        self._write_state(self.memory_source)

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
        self._write_state(path)

    def _write_state(self, path):
        """Writes the buffer to `path`, which then becomes its file. Shared
        by Save and Save As; any confirmation is the caller's."""
        try:
            self.memory.to_file(path)
        except Exception as e:
            logger.exception("Could not save state to %s", path)
            messagebox.showerror("Error", f"Could not save state: {e}")
            return
        logger.info("State saved to %s", path)
        self.memory_source = Path(path)
        self._modified_label.configure(text="")
        self._update_source_label()
        self.menus.remember_recent_file(path)
        messagebox.showinfo("Saved", f"Memory state written to {path}")

    def load_state_from_file(self):
        if not self.confirm_discard("load a different state"):
            return
        path = filedialog.askopenfilename(filetypes=STATE_FILETYPES)
        if not path:
            return
        self._load_state_into_buffer(path)

    def _load_state_into_buffer(self, path):
        """Reads `path` into self.memory and refreshes the UI to match.
        This is the actual load step shared by every way of opening a state
        (File > Open..., a startup file argument, and double-clicking a
        .dm41 file) -- callers are responsible for checking `self.memory.modified`
        and confirming with the user first, since the right prompt (or
        whether to prompt at all) differs by caller.

        A state is opened with the current mode's profile. If it does not
        fit that model, the user is offered DM41X mode instead
        (_offer_mode_switch_for_load); declining leaves the open state
        alone, so nothing is assigned here until that is settled."""
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
        if findings and evaluate_mode_switch(loaded, DeviceMode.DM41X.profile):
            # It does not fit DM41X mode either -- an XM directory that
            # cannot be read in any mode -- so offering that mode as the
            # cure would be wrong (GitHub issue #68). It opens as it is,
            # as it would in DM41X mode, and the XM Files tab says why
            # the directory cannot be listed.
            logger.warning(
                "%s has problems no mode fixes: %s",
                path,
                "; ".join(str(finding) for finding in findings),
            )
        elif findings:
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

        self.replace_memory(loaded, source=path)
        self.set_status(f"Loaded state from {Path(path).name}")
        logger.info("State loaded from %s (%s mode)", path, self.mode.value)
        self.menus.remember_recent_file(path)

    def open_state_file(self, path):
        """Opens `path` as the app's current state, prompting to discard
        unsaved changes first if needed (same prompt File > Open... uses).

        Public entry point for anything that hands the app a file path
        directly, bypassing the File > Open... dialog: a startup file
        argument (double-clicking a .dm41 file on Windows/Linux, or
        launching fresh via double-click on macOS -- see
        `_handle_startup_file_arg`/main() below) and the macOS "Open
        Document" AppleEvent for double-clicking a .dm41 file while the
        app is already running (see `_on_mac_open_document` below)."""
        if not Path(path).exists():
            logger.warning("Could not find file: %s", path)
            messagebox.showerror("Error", f"Could not find file: {path}")
            self.menus.forget_recent_file(path)
            return
        if not self.confirm_discard("load a different state"):
            return
        self._load_state_into_buffer(path)

    def _on_mac_open_document(self, *paths):
        """Handles macOS's "Open Document" AppleEvent -- what Finder sends
        for double-clicking a .dm41 file, whether that launches the app
        fresh or the app is already running. Tk surfaces this as a
        registered command (wired in __init__) rather than a normal
        widget-level event or sys.argv, per
        https://www.tcl.tk/man/tcl/TkCmd/tk_mac.html."""
        if not paths:
            return
        # Finder can in principle hand over more than one path at once
        # (e.g. multi-selecting files and choosing Open); this app only
        # has one buffer, so just open the last one -- "last one wins" is
        # the least surprising choice for a single-document app.
        self.open_state_file(paths[-1])

    def show_preferences(self):
        """Opens Preferences, or raises the one already open -- the menu
        item and the Cmd/Ctrl+, binding may both deliver the same key
        press on macOS. Returns the dialog."""
        dialog = getattr(self, "_preferences_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            dialog.focus_set()
            return dialog
        self._preferences_dialog = PreferencesDialog(
            self,
            self.config_store,
            self.connection.serial,
            on_saved=self._on_preferences_saved,
            mode=self.config_store.mode,
        )
        return self._preferences_dialog

    # -- XROM libraries (GitHub issue #53) ------------------------------------

    def _apply_xrom_libraries(self):
        """Makes the XROM libraries arranged for the current mode active:
        reads the shipped and the user's library files, works out the
        active set (the mode's built-in library, then the enabled ones in
        order) and rebuilds the instruction-name registry with it. Returns
        the files that couldn't be loaded, which are logged; the manager
        lists them."""
        paths = user_library_paths(self.config_store.xrom_directory)
        libraries, errors = load_libraries(user_paths=paths)
        for error in errors:
            logger.warning("XROM library not loaded: %s", error)
        self.xrom_active = active_set(
            libraries,
            self.mode.value,
            self.config_store.enabled_xrom_library_ids(self.mode),
        )
        if self.xrom_active.missing:
            logger.warning(
                "Enabled XROM libraries not found: %s",
                ", ".join(self.xrom_active.missing),
            )
        set_active_libraries(self.xrom_active.libraries)
        reference = getattr(self, "_mnemonics_reference", None)
        if reference is not None and reference.winfo_exists():
            reference.refresh_rows()
        return errors

    def show_xrom_libraries(self):
        return XromLibrariesDialog(
            self, self.config_store, self.mode, on_saved=self._on_xrom_libraries_saved
        )

    def _on_xrom_libraries_saved(self, entries):
        """Saves the manager's arrangement for the current mode, applies it
        and redraws the tabs, so names follow at once. If the active set
        changed and its libraries clash on an XROM number, says so once."""
        before = [library.id for library in self.xrom_active.libraries]
        self.config_store.set_xrom_library_entries(self.mode, entries)
        self.persist_config("the XROM libraries")
        self._apply_xrom_libraries()
        self.render_tabs()
        active = self.xrom_active.libraries
        plugins = [library for library in active if not library.builtin_on]
        self.set_status(
            f"XROM libraries: {len(plugins)} plug-in "
            f"{'library' if len(plugins) == 1 else 'libraries'} active."
        )
        if [library.id for library in active] == before:
            return
        warnings = [f for f in find_conflicts(active) if f.level == XROM_WARNING]
        if warnings:
            messagebox.showwarning(
                "XROM Libraries",
                "\n\n".join(str(warning) for warning in warnings),
                parent=self,
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
            self.persist_config("the DM41L/DM41X mode")
            self._refresh_mode_indicator()

        ctk.set_appearance_mode(self.config_store.appearance_mode)
        _setup_logging(self.config_store)
        # Hex View, Data Registers, XM Files, Programs and Alarms use native
        # ttk.Treeview widgets, which CTk's theme engine has no hook into:
        # set_appearance_mode() above doesn't recolor their rows, so each
        # is told to recompute and re-apply its colors. Overview, Flags
        # and Key Assignments are CTk widgets whose render() picks the
        # current colors every time, so re-rendering (below) is enough.
        self.hex_view_tab.refresh_theme()
        self.data_registers_tab.refresh_theme()
        self.xm_files_tab.refresh_theme()
        self.program_tab.refresh_theme()
        self.alarms_tab.refresh_theme()
        self.render_tabs()

    # -- Shutdown -----------------------------------------------------------

    def on_close(self):
        if self._closing:
            return  # e.g. Cmd+Q delivered by two routes at once
        if not self.confirm_discard("quit"):
            return
        self._closing = True
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
            self._resize_after_id = None
        self._remember_window_size()
        self.connection.close()
        self.destroy()


def _handle_startup_file_arg(app):
    """Opens a state file passed on the command line at launch, if any --
    the Windows/Linux half of "double-click a .dm41 file to open it": file
    associations on those platforms launch the app with the file's path as
    an argument. macOS instead delivers this via an "Open Document"
    AppleEvent (see `DM41ExplorerApp._on_mac_open_document`), which
    doesn't go through sys.argv at all, but this also covers running the
    app directly from a shell with a file argument on any platform.

    Ignores anything that looks like a flag (starts with "-") rather than
    a path, since this app has no other command-line options of its own to
    parse -- a stray flag shouldn't be treated as a file to open.
    """
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
