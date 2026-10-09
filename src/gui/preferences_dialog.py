"""
Preferences dialog, in three tabs (GitHub issue #43):

  General     the DM41L/DM41X mode, and logging (level, directory)
  Appearance  light/dark, color theme, contrast, font
  Connection  default serial port, baud rate, console timeout

The mode is the odd one out: every other setting here is written straight
to the config when Save is clicked, but changing mode erases the open
memory state, so it needs a confirmation this dialog is the wrong place
for. It is reported to the caller through `on_saved(requested_mode=...)`
instead, and the application decides (see gui/app.py's set_mode()). The
value shown is therefore always the *saved* mode, never a session-only
override from an auto-switch on load -- the status bar is where that is
shown (Mike, 2026-10-09).
"""

import logging
import platform
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter.font as tkfont
import customtkinter as ctk

from memory import DeviceMode
from gui.dialog_common import build_dialog_button_row, scaled
from gui.contrast import SECONDARY_TEXT, WARNING_TEXT
from gui.tab_common import ui_font

logger = logging.getLogger(__name__)

PLATFORM_SYSTEM = platform.system()

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# CustomTkinter's built-in color themes (customtkinter/assets/themes/*.json).
# tests/test_preferences_themes.py checks each one is really there.
COLOR_THEMES = ["blue", "dark-blue", "gold", "green"]

FONT_DEFAULT_LABEL = "System Default"
FONT_SIZE_DEFAULT_LABEL = "Default"
FONT_SIZES = ["9", "10", "11", "12", "13", "14", "16", "18", "20"]

# The mode selector's choices, in the order they are offered. Taken from
# DeviceMode so a model added later cannot be left out of the dialog.
MODE_VALUES = [mode.value for mode in DeviceMode]

# What each mode means, shown under the selector. Kept short: the full
# consequence (the open state is erased) is in the confirmation the
# application shows when the mode actually changes.
MODE_HINTS = {
    DeviceMode.DM41L: (
        "DM41L: serial connection, 362 extended-memory registers,\n"
        "and only the functions an HP-41CX has built in."
    ),
    DeviceMode.DM41X: (
        "DM41X: no serial connection, 600 extended-memory registers,\n"
        "and the DM41X's own functions as well."
    ),
}


class PreferencesDialog(ctk.CTkToplevel):
    """
    Modal preferences dialog. Changes are applied and saved immediately
    when "Save" is clicked; `on_saved` is invoked afterward so the caller
    can refresh anything that depends on the config.
    """

    def __init__(self, master, config, serial_manager, on_saved=None, mode=None):
        super().__init__(master)
        self.title("Preferences")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        if PLATFORM_SYSTEM == "Darwin" and hasattr(master, "_menubar"):
            self.config(menu=master._menubar)

        self._config = config
        self._serial_manager = serial_manager
        self._on_saved = on_saved
        # The SAVED mode, which the caller passes in; falling back to the
        # config means a caller that does not pass one still shows the
        # right thing rather than nothing.
        self._saved_mode = DeviceMode.from_value(
            mode if mode is not None else config.mode
        )

        tabs = ctk.CTkTabview(self)
        tabs.pack(padx=16, pady=16, fill="both", expand=True)
        # Issue #43: mode and logging first, then Appearance, then
        # Connection. CTkTabview shows whichever was added first.
        tabs.add("General")
        tabs.add("Appearance")
        tabs.add("Connection")

        self._build_general_tab(tabs.tab("General"))
        self._build_appearance_tab(tabs.tab("Appearance"))
        self._build_connection_tab(tabs.tab("Connection"))

        build_dialog_button_row(
            self,
            primary_text="Save",
            on_primary=self._on_save,
            pack_kwargs={"padx": 16, "pady": (0, 16), "fill": "x"},
        )

    # -- Connection tab -------------------------------------------------

    def _build_connection_tab(self, tab):
        ctk.CTkLabel(tab, text="Default serial port:").pack(
            anchor="w", padx=8, pady=(12, 4)
        )

        ports = self._serial_manager.get_available_ports()
        current_port = self._config.serial_port
        options = (
            ports
            if current_port in ports
            else ([current_port] + ports if current_port else ports)
        )
        if not options:
            options = [""]

        self._port_var = ctk.StringVar(value=current_port or options[0])
        self._port_menu = ctk.CTkOptionMenu(
            tab, values=options, variable=self._port_var
        )
        self._port_menu.pack(anchor="w", padx=8, pady=(0, 12), fill="x")

        ctk.CTkButton(tab, text="Rescan Ports", command=self._rescan_ports).pack(
            anchor="w", padx=8
        )

        ctk.CTkLabel(tab, text="Baud rate:").pack(anchor="w", padx=8, pady=(16, 4))
        self._baud_var = ctk.StringVar(value=str(self._config.baudrate))
        ctk.CTkOptionMenu(
            tab,
            values=["9600", "19200", "38400", "57600", "115200"],
            variable=self._baud_var,
        ).pack(anchor="w", padx=8, fill="x")

        ctk.CTkLabel(tab, text="Calculator console timeout (minutes):").pack(
            anchor="w", padx=8, pady=(16, 4)
        )
        self._timeout_var = ctk.StringVar(
            value=str(self._config.console_timeout_minutes)
        )
        ctk.CTkOptionMenu(
            tab,
            values=["1", "2", "3", "4", "5"],
            variable=self._timeout_var,
        ).pack(anchor="w", padx=8, fill="x")

    def _rescan_ports(self):
        ports = self._serial_manager.get_available_ports()
        if ports:
            self._port_menu.configure(values=ports)
            self._port_var.set(ports[0])

    # -- General tab (mode, logging) --------------------------------------

    def _build_general_tab(self, tab):
        ctk.CTkLabel(tab, text="Calculator model:").pack(
            anchor="w", padx=8, pady=(12, 4)
        )
        self._mode_var = ctk.StringVar(value=self._saved_mode.value)
        ctk.CTkOptionMenu(
            tab,
            values=MODE_VALUES,
            variable=self._mode_var,
            command=self._on_mode_chosen,
        ).pack(anchor="w", padx=8, fill="x")
        self._mode_hint = ctk.CTkLabel(
            tab,
            text=MODE_HINTS[self._saved_mode],
            justify="left",
            text_color=SECONDARY_TEXT,
            font=ui_font(-2),
        )
        self._mode_hint.pack(anchor="w", padx=8, pady=(4, 0))
        ctk.CTkLabel(
            tab,
            text="Changing the model starts a new, empty memory state.",
            justify="left",
            text_color=WARNING_TEXT,
            font=ui_font(-2),
        ).pack(anchor="w", padx=8, pady=(4, 0))

        ctk.CTkLabel(tab, text="Log level:").pack(anchor="w", padx=8, pady=(16, 4))
        self._log_level_var = ctk.StringVar(value=self._config.logging_level.upper())
        ctk.CTkOptionMenu(tab, values=LOG_LEVELS, variable=self._log_level_var).pack(
            anchor="w", padx=8, fill="x"
        )

        ctk.CTkLabel(tab, text="Log file directory:").pack(
            anchor="w", padx=8, pady=(16, 4)
        )
        dir_row = ctk.CTkFrame(tab, fg_color="transparent")
        dir_row.pack(anchor="w", padx=8, fill="x")

        self._log_dir_var = ctk.StringVar(value=str(self._config.log_directory))
        ctk.CTkEntry(dir_row, textvariable=self._log_dir_var).pack(
            side="left", fill="x", expand=True
        )
        ctk.CTkButton(
            dir_row, text="Browse...", width=scaled(90), command=self._pick_log_directory
        ).pack(side="left", padx=(8, 0))

    def _on_mode_chosen(self, value):
        """Keeps the hint under the selector in step with it. Nothing is
        applied here: the mode only changes when Save is clicked, and then
        only after the application's own confirmation."""
        self._mode_hint.configure(
            text=MODE_HINTS[DeviceMode.from_value(value, self._saved_mode)]
        )

    @property
    def requested_mode(self) -> DeviceMode:
        """The mode currently selected in the dialog."""
        return DeviceMode.from_value(self._mode_var.get(), self._saved_mode)

    # -- Appearance tab ---------------------------------------------------

    def _build_appearance_tab(self, tab):
        ctk.CTkLabel(tab, text="Appearance mode:").pack(
            anchor="w", padx=8, pady=(12, 4)
        )
        self._appearance_var = ctk.StringVar(value=self._config.appearance_mode)
        ctk.CTkOptionMenu(
            tab, values=["System", "Light", "Dark"], variable=self._appearance_var
        ).pack(anchor="w", padx=8, fill="x")

        ctk.CTkLabel(tab, text="Color theme:").pack(anchor="w", padx=8, pady=(16, 4))
        themes = list(COLOR_THEMES)
        current_theme = self._config.color_theme
        if current_theme not in themes:
            # e.g. the path of a theme file typed into the preferences file
            themes.append(current_theme)
        self._color_theme_var = ctk.StringVar(value=current_theme)
        ctk.CTkOptionMenu(
            tab, values=themes, variable=self._color_theme_var
        ).pack(anchor="w", padx=8, fill="x")

        self._high_contrast_var = ctk.BooleanVar(value=self._config.high_contrast)
        ctk.CTkCheckBox(
            tab,
            text="Higher-contrast colors (easier to read)",
            variable=self._high_contrast_var,
        ).pack(anchor="w", padx=8, pady=(12, 0))

        font_row = ctk.CTkFrame(tab, fg_color="transparent")
        font_row.pack(anchor="w", padx=8, pady=(16, 0), fill="x")
        font_row.grid_columnconfigure(0, weight=1)
        font_row.grid_columnconfigure(1, weight=0)

        family_col = ctk.CTkFrame(font_row, fg_color="transparent")
        family_col.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(family_col, text="Application font:").pack(anchor="w")
        families = [FONT_DEFAULT_LABEL] + self._get_font_families()
        current_family = self._config.font_family or FONT_DEFAULT_LABEL
        if current_family not in families:
            families.append(current_family)
        self._font_family_var = ctk.StringVar(value=current_family)
        ctk.CTkOptionMenu(
            family_col, values=families, variable=self._font_family_var
        ).pack(anchor="w", pady=(4, 0), fill="x")

        size_col = ctk.CTkFrame(font_row, fg_color="transparent")
        size_col.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ctk.CTkLabel(size_col, text="Size:").pack(anchor="w")
        sizes = [FONT_SIZE_DEFAULT_LABEL] + FONT_SIZES
        current_size = (
            str(self._config.font_size)
            if self._config.font_size
            else FONT_SIZE_DEFAULT_LABEL
        )
        if current_size not in sizes:
            sizes.append(current_size)
        self._font_size_var = ctk.StringVar(value=current_size)
        ctk.CTkOptionMenu(
            size_col, values=sizes, variable=self._font_size_var, width=scaled(90)
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkLabel(
            tab,
            text="Font, color theme and contrast changes\n"
            "take effect after restarting DM41_Explorer.",
            justify="left",
            text_color=WARNING_TEXT,
            font=ui_font(-2),
        ).pack(anchor="w", padx=8, pady=(4, 0))

    def _pick_log_directory(self):
        chosen = filedialog.askdirectory(
            title="Choose Log Directory",
            initialdir=self._log_dir_var.get() or str(Path.home()),
        )
        if chosen:
            self._log_dir_var.set(chosen)

    @staticmethod
    def _get_font_families() -> list:
        """Returns the system's installed font family names, sorted and
        de-duplicated (font backends commonly report the same family
        multiple times across styles/weights). Vertical-writing CJK
        families (Tk lists these with a "@" prefix) are dropped since
        they're not meant for horizontal UI text."""
        families = {f for f in tkfont.families() if not f.startswith("@")}
        return sorted(families, key=str.casefold)

    # -- Save -------------------------------------------------------------

    def _on_save(self):
        log_dir_str = self._log_dir_var.get().strip() or str(Path.home())
        try:
            Path(log_dir_str).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Could not use log directory %r: %s", log_dir_str, e)
            messagebox.showerror(
                "Invalid Log Directory",
                f"Could not use '{log_dir_str}' for logs: {e}",
            )
            return

        self._config.serial_port = self._port_var.get()
        try:
            self._config.baudrate = int(self._baud_var.get())
            self._config.console_timeout_minutes = int(self._timeout_var.get())
        except ValueError as e:
            # Silently discarded, matching this dialog's existing
            # behavior (no messagebox for these two fields) -- logged so
            # a preference that silently failed to save is at least
            # visible after the fact instead of leaving no trace at all.
            logger.warning("Invalid baud rate or console timeout, not saved: %s", e)
        self._config.logging_level = self._log_level_var.get()
        self._config.appearance_mode = self._appearance_var.get()
        self._config.color_theme = self._color_theme_var.get()
        self._config.high_contrast = self._high_contrast_var.get()
        family_choice = self._font_family_var.get()
        self._config.font_family = (
            "" if family_choice == FONT_DEFAULT_LABEL else family_choice
        )
        size_choice = self._font_size_var.get()
        try:
            self._config.font_size = (
                0 if size_choice == FONT_SIZE_DEFAULT_LABEL else int(size_choice)
            )
        except ValueError as e:
            logger.warning("Invalid font size %r, not saved: %s", size_choice, e)
        self._config.log_directory = log_dir_str

        try:
            self._config.save()
            logger.info("Preferences saved.")
        except Exception as e:
            messagebox.showerror("Preferences",
                f"Unable to save preferences {e}")
            logger.warning("Unable to save preferences %s", str(e))

        if self._on_saved:
            # The mode is reported, not written: changing it erases the
            # open state, so the application confirms it first and writes
            # the preference only if the user goes ahead.
            self._on_saved(requested_mode=self.requested_mode)

        self.grab_release()
        self.destroy()
