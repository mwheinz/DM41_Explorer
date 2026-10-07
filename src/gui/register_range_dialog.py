"""
Modal dialogs for picking which data registers an Export/Import operation
should touch -- GitHub issues #14 (import destination) and #15 (export
range).

Both dialogs follow the same `on_confirm(...)` callback convention as
RegisterEditDialog/XMFileDialog elsewhere in gui/: the dialog itself never
touches Memory or the filesystem, it just validates the register-number
math and hands the caller back plain ints to act on.
"""

import logging
import platform
from tkinter import messagebox
import customtkinter as ctk

from gui.dialog_common import build_dialog_button_row, scaled

logger = logging.getLogger(__name__)

PLATFORM_SYSTEM = platform.system()


class RegisterRangeDialog(ctk.CTkToplevel):
    """
    Blocking modal dialog to pick a sub-range of the currently-displayed
    data registers to export (GitHub issue #15). `on_confirm(start, end)`
    is called with 0-based Rxx indices (inclusive) if the user confirms --
    callers add their own r00 to get real memory addresses. Defaults to
    the full range (R00 through the last displayed register), so hitting
    Export with no changes reproduces the old "export everything" behavior.
    """

    def __init__(self, master, count: int, on_confirm):
        super().__init__(master)
        self._count = count
        self._on_confirm = on_confirm

        self.title("Export Data Registers")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        if PLATFORM_SYSTEM == "Darwin" and hasattr(master, "_menubar"):
            self.config(menu=master._menubar)

        last = count - 1
        ctk.CTkLabel(
            self, text=f"Registers R00-R{last:02d} are available ({count} total)."
        ).pack(anchor="w", padx=16, pady=(16, 8))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(anchor="w", padx=16, pady=(0, 16))
        ctk.CTkLabel(row, text="Export from R").grid(row=0, column=0)
        self._start_var = ctk.StringVar(value="0")
        ctk.CTkEntry(row, textvariable=self._start_var, width=scaled(50)).grid(row=0, column=1)
        ctk.CTkLabel(row, text=" through R").grid(row=0, column=2)
        self._end_var = ctk.StringVar(value=str(last))
        ctk.CTkEntry(row, textvariable=self._end_var, width=scaled(50)).grid(row=0, column=3)

        build_dialog_button_row(
            self,
            primary_text="Export",
            on_primary=self._on_confirm_clicked,
            pack_kwargs={"padx": 16, "pady": (0, 16), "fill": "x"},
        )

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _on_confirm_clicked(self):
        try:
            start = int(self._start_var.get())
            end = int(self._end_var.get())
        except ValueError:
            logger.warning(
                "Invalid export range entry: start=%r end=%r",
                self._start_var.get(),
                self._end_var.get(),
            )
            messagebox.showerror(
                "Invalid Range", "Enter whole register numbers (e.g. 0 and 34)."
            )
            return

        last = self._count - 1
        if not (0 <= start <= end <= last):
            logger.warning(
                "Export range R%02d-R%02d out of bounds (valid: R00-R%02d)",
                start,
                end,
                last,
            )
            messagebox.showerror(
                "Invalid Range",
                f"Enter a range between R00 and R{last:02d}, with the start "
                "register no greater than the end register.",
            )
            return

        self._on_confirm(start, end)
        self.destroy()


class RegisterImportLocationDialog(ctk.CTkToplevel):
    """
    Blocking modal dialog to pick where imported register data should
    land (GitHub issue #14): the file's line count is fixed by its
    content, so the only thing to ask for is the starting register --
    `on_confirm(start)` is called with its 0-based Rxx index if the user
    confirms and the resulting range fits within the currently-displayed
    registers.
    """

    def __init__(self, master, count: int, import_count: int, on_confirm):
        super().__init__(master)
        self._count = count
        self._import_count = import_count
        self._on_confirm = on_confirm

        self.title("Import Data Registers")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        if PLATFORM_SYSTEM == "Darwin" and hasattr(master, "_menubar"):
            self.config(menu=master._menubar)

        last = count - 1
        ctk.CTkLabel(
            self,
            text=(
                f"File contains {import_count} register(s). Destination has "
                f"R00-R{last:02d} ({count} registers)."
            ),
            justify="left",
            wraplength=scaled(320),
        ).pack(anchor="w", padx=16, pady=(16, 8))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(anchor="w", padx=16, pady=(0, 16))
        ctk.CTkLabel(row, text="Import starting at R").grid(row=0, column=0)
        self._start_var = ctk.StringVar(value="0")
        ctk.CTkEntry(row, textvariable=self._start_var, width=scaled(50)).grid(row=0, column=1)

        build_dialog_button_row(
            self,
            primary_text="Import",
            on_primary=self._on_confirm_clicked,
            pack_kwargs={"padx": 16, "pady": (0, 16), "fill": "x"},
        )

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _on_confirm_clicked(self):
        try:
            start = int(self._start_var.get())
        except ValueError:
            logger.warning(
                "Invalid import location entry: start=%r", self._start_var.get()
            )
            messagebox.showerror(
                "Invalid Location", "Enter a whole register number (e.g. 5)."
            )
            return

        last = self._count - 1
        end = start + self._import_count - 1
        if not (0 <= start and end <= last):
            logger.warning(
                "Import destination R%02d-R%02d out of bounds (valid: R00-R%02d)",
                start,
                end,
                last,
            )
            messagebox.showerror(
                "Invalid Location",
                f"{self._import_count} register(s) starting at R{start:02d} "
                f"would run through R{end:02d}, past the last available "
                f"register R{last:02d}.",
            )
            return

        self._on_confirm(start)
        self.destroy()
