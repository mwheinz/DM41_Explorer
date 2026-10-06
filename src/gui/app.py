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
    DM41X,
    ERROR,
    WARNING,
    Memory,
    check_profile_fit,
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
STATE_FILETYPES = [
    ("DM41 memory state", ("*.dm41", "*.d41")),
    ("All files", "*.*"),
]


class DM41ExplorerApp(ctk.CTk):
    '''Main application window.'''

    def __init__(self):
        super().__init__()

        self.config_store = ProjectConfig()
        try:
            self.config_store.load()
        except Exception as e:
            messagebox.showerror("Unable to load preferences",
                                  str(e))

        _setup_logging(self.config_store)

        ctk.set_appearance_mode(self.config_store.appearance_mode)
        ctk.set_default_color_theme(self.config_store.color_theme)
        _apply_font_prefs(self.config_store)

        self.title("DM41_Explorer")
        self.geometry("1080x768")

        self.serial = SerialManager(error_callback=self._handle_serial_error)
        self.engine = CommandEngine(self.serial)
        self.memory = Memory()
        self.memory_source = None
        self._command_pending = False

        self._build_layout()
        self._menubar = self._build_menus()
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

        self._start_engine_pump()
        self._render_tabs()

        # Nothing touches the serial port until the user chooses
        # Connect / Reconnect... (the app starts offline).

    def _handle_serial_error(self, msg: str):
        logger.error("Serial error: %s", msg)
        self._set_status("Not connected")
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

    # -- Layout ---------------------------------------------------------

    def _build_layout(self):
        # Status bar first, pinned to the bottom, so the tab view above it
        # gets whatever space is left -- packing order (not just `side`)
        # is what keeps it pinned regardless of window resizing.
        status_bar = ctk.CTkFrame(self)
        status_bar.pack(side="bottom", fill="x", padx=8, pady=(0, 8))

        self._status_label = ctk.CTkLabel(
            status_bar, text="Not connected", font=ctk.CTkFont(size=13)
        )
        self._status_label.pack(side="left", padx=8, pady=6)
        self._modified_label = ctk.CTkLabel(
            status_bar, text="", font=ctk.CTkFont(size=13), text_color="#d9822b"
        )
        self._modified_label.pack(side="left", padx=8, pady=6)
        self._battery_label = ctk.CTkLabel(
            status_bar, text="", font=ctk.CTkFont(size=13)
        )
        self._battery_label.pack(side="right", padx=16, pady=6)
        self._calc_time_label = ctk.CTkLabel(
            status_bar, text="", font=ctk.CTkFont(size=13)
        )
        self._calc_time_label.pack(side="right", padx=16, pady=6)
        self._source_label = ctk.CTkLabel(
            status_bar,
            text="(new, unsaved buffer)",
            font=ctk.CTkFont(size=13),
            text_color="gray60",
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
            self._set_status("Not connected")

    def show_connect_dialog(self):
        self._prompt_for_port(None)

    def disconnect(self):
        if not self.serial.is_connected:
            return
        self.serial.disconnect()
        self._set_status("Not connected")
        self._battery_label.configure(text="")
        self._calc_time_label.configure(text="")

    def _connect_and_verify(self, port: str):
        self._set_status(f"Connecting to {port}...")
        success, message = self.serial.connect(
            port, baudrate=self.config_store.baudrate
        )
        if not success:
            self._set_status("Not connected")
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
        self._set_status("Not connected")
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
            self.memory = Memory.from_string(state)
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
        self._set_status("Not connected")
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
        self.memory = Memory()
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
        # A file keeps its own extension: the text format is the same.
        extension = (
            self.memory_source.suffix
            if self.memory_source and self.memory_source.suffix in STATE_EXTENSIONS
            else ".dm41"
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
        whether to prompt at all) differs by caller. This always
        overwrites the current buffer unconditionally.'''
        try:
            # Every state file opens with the DM41X profile, a superset of
            # the DM41L's, so a large state displays and saves without loss.
            self.memory = Memory.from_file(path, profile=DM41X)
            self.memory_source = Path(path)
            self._modified_label.configure(text="")
            self._update_source_label()
            self._render_tabs()
            name = Path(path).name
            self._set_status(f"Loaded state from {name}")
            logger.info("State loaded from %s", path)
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
        if not self.serial.is_connected:
            messagebox.showwarning("Not Connected", "Connect to the DM41L first.")
            return

        # A state can hold things the calculator on the other end does not
        # have (every file opens with the DM41X profile). The connection
        # declares which model that is; see memory/profile_fit.py.
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
            self, self.config_store, self.serial, on_saved=self._on_preferences_saved
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

    def _on_preferences_saved(self):
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
        if self.memory.modified and not messagebox.askyesno(
            "Unsaved Changes", "Discard unsaved changes and quit?"
        ):
            return
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
