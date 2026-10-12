"""
Preferences dialog, in three tabs (GitHub issue #43):

  General     the DM41L/DM41X mode, and logging (level, directory)
  Appearance  light/dark, color theme, contrast
  Fonts       the four font roles (GitHub issue #61)
  Connection  default serial port, baud rate, console timeout

The mode is the odd one out: every other setting here is written straight
to the config when Save is clicked, but changing mode erases the open
memory state, so it needs a confirmation this dialog is the wrong place
for. It is reported to the caller through `on_saved(requested_mode=...)`
instead, and the application decides (see gui/app.py's set_mode()). The
value shown is therefore always the *saved* mode, never a session-only
override from an auto-switch on load -- the status bar is where that is
shown.
"""

import logging
import platform
from pathlib import Path
from tkinter import filedialog, messagebox
import customtkinter as ctk

from config import FONT_ROLES
from memory import DeviceMode
from gui import fonts
from gui.fonts import font_for
from gui.dialog_common import build_dialog_button_row, center_on_master, scaled
from gui.font_family_dialog import FontFamilyDialog
from gui.contrast import SECONDARY_TEXT, WARNING_TEXT

logger = logging.getLogger(__name__)

PLATFORM_SYSTEM = platform.system()

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# CustomTkinter's built-in color themes (customtkinter/assets/themes/*.json).
# tests/test_preferences_themes.py checks each one is really there.
COLOR_THEMES = ["blue", "dark-blue", "gold", "green"]

FONT_DEFAULT_LABEL = "System Default"
FONT_SIZE_DEFAULT_LABEL = "Default"
FONT_SIZES = ["8", "9", "10", "11", "12", "13", "14", "16", "18", "20", "24"]

# Each font role's row on the Fonts tab: its name, and the sample text
# drawn in the font chosen, so the effect shows before restarting.
FONT_ROLE_ROWS = {
    "ui": ("Application", "Labels, buttons and tables"),
    "small": ("Small text", "Captions, hints and key labels"),
    "title": ("Headings", "Section and dialog headings"),
    "mono": ("Monospace", "0x1A2B  XROM 25,00  ALPHA"),
}

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
        tabs.add("Fonts")
        tabs.add("Connection")

        self._build_general_tab(tabs.tab("General"))
        self._build_appearance_tab(tabs.tab("Appearance"))
        self._build_fonts_tab(tabs.tab("Fonts"))
        self._build_connection_tab(tabs.tab("Connection"))

        build_dialog_button_row(
            self,
            primary_text="Save",
            on_primary=self._on_save,
            pack_kwargs={"padx": 16, "pady": (0, 16), "fill": "x"},
        )
        # In the middle of the window it belongs to (GitHub issue #64).
        center_on_master(self)

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
            font=font_for("small"),
        )
        self._mode_hint.pack(anchor="w", padx=8, pady=(4, 0))
        ctk.CTkLabel(
            tab,
            text="Changing the model starts a new, empty memory state.",
            justify="left",
            text_color=WARNING_TEXT,
            font=font_for("small"),
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
            dir_row,
            text="Browse...",
            width=scaled(90),
            command=self._pick_log_directory,
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
        ctk.CTkOptionMenu(tab, values=themes, variable=self._color_theme_var).pack(
            anchor="w", padx=8, fill="x"
        )

        self._high_contrast_var = ctk.BooleanVar(value=self._config.high_contrast)
        ctk.CTkCheckBox(
            tab,
            text="Higher-contrast colors (easier to read)",
            variable=self._high_contrast_var,
        ).pack(anchor="w", padx=8, pady=(12, 0))

        ctk.CTkLabel(
            tab,
            text="Color theme and contrast changes\n"
            "take effect after restarting DM41_Explorer.",
            justify="left",
            text_color=WARNING_TEXT,
            font=font_for("small"),
        ).pack(anchor="w", padx=8, pady=(4, 0))

    def _pick_log_directory(self):
        chosen = filedialog.askdirectory(
            title="Choose Log Directory",
            initialdir=self._log_dir_var.get() or str(Path.home()),
        )
        if chosen:
            self._log_dir_var.set(chosen)

    # -- Fonts tab --------------------------------------------------------

    def _build_fonts_tab(self, tab):
        """One row per font role (GitHub issue #61): family, size, bold,
        and a sample in the font chosen. "System Default" and "Default"
        store "" and 0, so the role follows this platform's default."""
        self._font_defaults = fonts.platform_defaults(self)
        self._installed_families = fonts.installed_families(self)
        self._font_vars = {}
        self._font_samples = {}

        grid = ctk.CTkFrame(tab, fg_color="transparent")
        grid.pack(anchor="w", padx=8, pady=(12, 0), fill="x")
        grid.grid_columnconfigure(1, weight=1)
        for column, heading in enumerate(("", "Family", "Size", "")):
            ctk.CTkLabel(grid, text=heading, font=font_for("small")).grid(
                row=0, column=column, sticky="w", padx=(0, 8)
            )

        for index, role in enumerate(FONT_ROLES):
            name, sample_text = FONT_ROLE_ROWS[role]
            family, size, bold = self._config.font_role(role)
            row = 1 + 2 * index

            ctk.CTkLabel(grid, text=f"{name}:").grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=(8, 0)
            )
            # A button opening a list, not a drop-down menu: Tk menus can't
            # scroll on Linux -- see gui/font_family_dialog.py.
            family_var = ctk.StringVar(value=family or FONT_DEFAULT_LABEL)
            ctk.CTkButton(
                grid,
                textvariable=family_var,
                anchor="w",
                command=lambda role=role: self._choose_font_family(role),
            ).grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))

            size_choice = str(size) if size else FONT_SIZE_DEFAULT_LABEL
            sizes = [FONT_SIZE_DEFAULT_LABEL] + FONT_SIZES
            if size_choice not in sizes:
                sizes.append(size_choice)
            size_var = ctk.StringVar(value=size_choice)
            ctk.CTkOptionMenu(
                grid, values=sizes, variable=size_var, width=scaled(90)
            ).grid(row=row, column=2, sticky="w", padx=(0, 8), pady=(8, 0))

            bold_var = ctk.BooleanVar(value=bold)
            ctk.CTkCheckBox(grid, text="Bold", variable=bold_var).grid(
                row=row, column=3, sticky="w", pady=(8, 0)
            )

            # Redrawn in the chosen font by _update_font_samples().
            sample_font = font_for(role)
            ctk.CTkLabel(
                grid,
                text=sample_text,
                font=sample_font,
                text_color=SECONDARY_TEXT,
                anchor="w",
            ).grid(row=row + 1, column=1, columnspan=3, sticky="w")

            self._font_vars[role] = (family_var, size_var, bold_var)
            self._font_samples[role] = sample_font
            for var in (family_var, size_var, bold_var):
                var.trace_add("write", lambda *_: self._update_font_samples())

        ctk.CTkButton(
            tab,
            text="Restore Defaults",
            width=scaled(140),
            command=self._restore_default_fonts,
        ).pack(anchor="w", padx=8, pady=(16, 0))
        ctk.CTkLabel(
            tab,
            text="Font changes take effect after restarting DM41_Explorer.",
            justify="left",
            text_color=WARNING_TEXT,
            font=font_for("small"),
        ).pack(anchor="w", padx=8, pady=(4, 0))

        self._update_font_samples()

    def _choose_font_family(self, role):
        """Opens the family picker for `role`'s row. The mono row lists
        only fixed-width families; a family not installed here (from
        another machine's preferences file) stays listed while chosen."""
        family_var = self._font_vars[role][0]
        current = family_var.get()
        if role == "mono":
            families = fonts.monospace_families(self)
            note = "Only fixed-width fonts are listed."
        else:
            families = list(self._installed_families)
            note = None
        if current != FONT_DEFAULT_LABEL and current not in families:
            families.append(current)
        name, _sample = FONT_ROLE_ROWS[role]
        return FontFamilyDialog(
            self,
            f"{name} Font",
            families,
            default_label=FONT_DEFAULT_LABEL,
            current=current,
            on_chosen=family_var.set,
            note=note,
        )

    def _chosen_font_roles(self) -> dict:
        """{role: (family, size, bold)} as the Fonts tab stands, in the
        form the preferences file stores. A size that isn't a number
        (only possible by hand-editing the file) counts as the default."""
        chosen = {}
        for role, (family_var, size_var, bold_var) in self._font_vars.items():
            family = family_var.get()
            size = size_var.get()
            chosen[role] = (
                "" if family == FONT_DEFAULT_LABEL else family,
                int(size) if size.isdigit() else 0,
                bool(bold_var.get()),
            )
        return chosen

    def _update_font_samples(self):
        """Redraws each sample in the font its role would have after a
        restart -- so a role left at its defaults shows the ui role's
        effect on it, as it will be."""
        resolved = fonts.resolve_roles(
            self._chosen_font_roles(), self._font_defaults, self._installed_families
        )
        for role, sample_font in self._font_samples.items():
            spec = resolved[role]
            sample_font.configure(
                family=spec.family,
                size=spec.size,
                weight="bold" if spec.bold else "normal",
            )

    def _restore_default_fonts(self):
        """Sets every role back to this platform's default. Nothing is
        stored until Save."""
        for role, (family_var, size_var, bold_var) in self._font_vars.items():
            _family, _size, bold = self._config.DEFAULT_PREFS[f"font_{role}"]
            family_var.set(FONT_DEFAULT_LABEL)
            size_var.set(FONT_SIZE_DEFAULT_LABEL)
            bold_var.set(bold)

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
        for role, (family, size, bold) in self._chosen_font_roles().items():
            self._config.set_font_role(role, family, size, bold)
        self._config.log_directory = log_dir_str

        try:
            self._config.save()
            logger.info("Preferences saved.")
        except Exception as e:
            messagebox.showerror("Preferences", f"Unable to save preferences {e}")
            logger.warning("Unable to save preferences %s", str(e))

        if self._on_saved:
            # The mode is reported, not written: changing it erases the
            # open state, so the application confirms it first and writes
            # the preference only if the user goes ahead.
            self._on_saved(requested_mode=self.requested_mode)

        self.grab_release()
        self.destroy()
