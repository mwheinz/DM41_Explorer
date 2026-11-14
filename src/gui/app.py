"""
DM41_Explorer: a CustomTkinter GUI for DM41-series calculator memory states.

"""

import logging
import logging.handlers
import sys
from pathlib import Path
import platform
from tkinter import messagebox
from typing import NamedTuple, Optional

import customtkinter as ctk

from memory import Memory

from config import ProjectConfig
from gui import fonts
from gui.fonts import font_for
from gui.connection import Connection
from gui.contrast import SECONDARY_TEXT, WARNING_TEXT, improve_theme_contrast
from gui.device_mode import ModeSwitcher
from gui.menus import MenuBar
from gui.screens import screen_rectangles
from gui.state_files import StateFiles
from gui.tools_actions import XromLibraries
from gui.window_geometry import (
    geometry_string,
    initial_window_size,
    minimum_window_size,
    parse_geometry,
    parse_position,
    restored_position,
)
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
        # writing the preference back (see ModeSwitcher.offer_switch_for_load).
        # Everything that needs a memory map asks self.profile, never the
        # config and never the connection.
        self.mode = self.config_store.mode
        self.mode_switcher = ModeSwitcher(self)
        # The XROM libraries arranged for that mode, before anything shows
        # an instruction name (GitHub issue #53).
        self.xrom_libraries = XromLibraries(self)
        xrom_errors = self.xrom_libraries.apply()

        self.connection = Connection(self)
        self.memory = Memory(profile=self.profile)
        self.memory_source = None
        self.state_files = StateFiles(self)

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
            self.createcommand(
                "::tk::mac::OpenDocument", self.state_files.open_documents
            )
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

    def refresh_mode_indicator(self):
        self._mode_label.configure(text=self._mode_status_text())

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
            kwargs = {"on_change": self.on_memory_changed} if spec.editable else {}
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

    def on_memory_changed(self):
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

    def show_saved_as(self, path):
        """Makes `path` the open buffer's file once the buffer has been
        written there, and shows it as saved."""
        self.memory_source = Path(path)
        self._modified_label.configure(text="")
        self._update_source_label()

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

    def refresh_mnemonics_reference(self):
        """Brings the open mnemonics reference, if any, up to date with the
        instruction names, e.g. after the XROM libraries changed."""
        reference = getattr(self, "_mnemonics_reference", None)
        if reference is not None and reference.winfo_exists():
            reference.refresh_rows()

    def _on_preferences_saved(self, requested_mode=None):
        """Re-applies everything the dialog may have changed.

        `requested_mode` is the mode the user chose there, which goes
        through ModeSwitcher.apply_preference() and its confirmation. The
        dialog's other settings are already saved by the time this runs,
        so cancelling the switch keeps those and only the mode change is
        dropped."""
        self.mode_switcher.apply_preference(requested_mode)

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
    AppleEvent (see `StateFiles.open_documents`), which
    doesn't go through sys.argv at all, but this also covers running the
    app directly from a shell with a file argument on any platform.

    Ignores anything that looks like a flag (starts with "-") rather than
    a path, since this app has no other command-line options of its own to
    parse -- a stray flag shouldn't be treated as a file to open.
    """
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        app.state_files.open(sys.argv[1])


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
