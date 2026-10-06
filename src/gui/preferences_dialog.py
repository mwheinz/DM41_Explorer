"""
Preferences dialog: default serial port/baud/timeout, logging, appearance,
and font.
"""

import logging
import platform
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter.font as tkfont
import customtkinter as ctk

from gui.dialog_common import build_dialog_button_row

logger = logging.getLogger(__name__)

PLATFORM_SYSTEM = platform.system()

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

FONT_DEFAULT_LABEL = "System Default"
FONT_SIZE_DEFAULT_LABEL = "Default"
FONT_SIZES = ["9", "10", "11", "12", "13", "14", "16", "18", "20"]


class PreferencesDialog(ctk.CTkToplevel):
    """
    Modal preferences dialog. Changes are applied and saved immediately
    when "Save" is clicked; `on_saved` is invoked afterward so the caller
    can refresh anything that depends on the config.
    """

    def __init__(self, master, config, serial_manager, on_saved=None):
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

        tabs = ctk.CTkTabview(self)
        tabs.pack(padx=16, pady=16, fill="both", expand=True)
        tabs.add("Connection")
        tabs.add("Logging & Appearance")

        self._build_connection_tab(tabs.tab("Connection"))
        self._build_logging_tab(tabs.tab("Logging & Appearance"))

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

    # -- Logging & appearance tab ----------------------------------------

    def _build_logging_tab(self, tab):
        ctk.CTkLabel(tab, text="Log level:").pack(anchor="w", padx=8, pady=(12, 4))
        self._log_level_var = ctk.StringVar(value=self._config.logging_level.upper())
        ctk.CTkOptionMenu(tab, values=LOG_LEVELS, variable=self._log_level_var).pack(
            anchor="w", padx=8, fill="x"
        )

        ctk.CTkLabel(tab, text="Appearance mode:").pack(
            anchor="w", padx=8, pady=(16, 4)
        )
        self._appearance_var = ctk.StringVar(value=self._config.appearance_mode)
        ctk.CTkOptionMenu(
            tab, values=["System", "Light", "Dark"], variable=self._appearance_var
        ).pack(anchor="w", padx=8, fill="x")

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
            size_col, values=sizes, variable=self._font_size_var, width=90
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkLabel(
            tab,
            text="Font changes take effect after restarting DM41_Explorer.",
            text_color="#d9822b",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=8, pady=(4, 0))

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
            dir_row, text="Browse...", width=90, command=self._pick_log_directory
        ).pack(side="left", padx=(8, 0))

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
            self._on_saved()

        self.grab_release()
        self.destroy()
