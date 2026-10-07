"""
Modal dialog for editing a single data register as a number, short alpha
text, or raw hex.
"""

import logging
import platform
from tkinter import messagebox
import customtkinter as ctk

from memory import Register
from gui.dialog_common import build_dialog_button_row, scaled
from gui.tab_common import MONOSPACE_FONT_FAMILY

logger = logging.getLogger(__name__)

PLATFORM_SYSTEM = platform.system()


def _decode_text_register(register: Register) -> str:
    """
    Best-effort default for the Text tab: decodes a register using the same
    "byte 0 == 0x10 means alpha text" convention Register.__str__ already
    uses for display, rather than Register.get_ascii().

    get_ascii() renders *every* non-printable byte -- including real 0x00
    padding -- as a literal "." character, so it never actually contains a
    "\\x00"; a naive .rstrip("\\x00").strip(".") on its output can't tell
    "padding, shown as dots" apart from a genuine leading/trailing period in
    the text (since "." is itself printable ASCII), and would silently drop
    real periods. Decoding straight from the raw bytes -- skipping only
    actual zero bytes, matching Register.__str__ -- avoids that.
    """
    raw = register.get_bytes()
    if not raw or raw[0] != 0x10:
        return ""
    return "".join(chr(b) for b in raw[1:] if b != 0)


class RegisterEditDialog(ctk.CTkToplevel):
    """
    Blocking modal dialog to edit one 7-byte register. `on_save(register)`
    is called with the new Register if the user saves; the dialog itself
    doesn't touch the Memory object, so callers stay in control of when
    (and whether) the edit takes effect.
    """

    def __init__(self, master, addr: int, register: Register, on_save):
        super().__init__(master)
        self.title(f"Edit Register 0x{addr:03x}")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        if PLATFORM_SYSTEM == "Darwin" and hasattr(master, "_menubar"):
            self.config(menu=master._menubar)

        self._on_save = on_save
        self._addr = addr

        ctk.CTkLabel(
            self, text=f"Register 0x{addr:03x}", font=ctk.CTkFont(weight="bold")
        ).pack(padx=16, pady=(16, 4), anchor="w")

        tabs = ctk.CTkTabview(self, width=scaled(360))
        tabs.pack(padx=16, pady=8, fill="both", expand=True)
        tabs.add("Number")
        tabs.add("Text")
        tabs.add("Hex")

        try:
            number_default = f"{register.get_bcd_number():.8g}"
        except ValueError:
            number_default = ""
        self._number_var = ctk.StringVar(value=number_default)
        ctk.CTkLabel(tabs.tab("Number"), text="BCD number:").pack(
            anchor="w", padx=8, pady=(12, 4)
        )
        ctk.CTkEntry(
            tabs.tab("Number"),
            textvariable=self._number_var,
            font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
        ).pack(anchor="w", padx=8, fill="x")

        text_default = _decode_text_register(register)
        self._text_var = ctk.StringVar(value=text_default)
        ctk.CTkLabel(tabs.tab("Text"), text="Alpha text (max 6 characters):").pack(
            anchor="w", padx=8, pady=(12, 4)
        )
        ctk.CTkEntry(
            tabs.tab("Text"),
            textvariable=self._text_var,
            font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
        ).pack(anchor="w", padx=8, fill="x")

        self._hex_var = ctk.StringVar(value=register.get_hex())
        ctk.CTkLabel(tabs.tab("Hex"), text="Raw hex (14 characters):").pack(
            anchor="w", padx=8, pady=(12, 4)
        )
        ctk.CTkEntry(
            tabs.tab("Hex"),
            textvariable=self._hex_var,
            font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
        ).pack(anchor="w", padx=8, fill="x")

        self._tabs = tabs

        build_dialog_button_row(
            self,
            primary_text="Save",
            on_primary=self._on_save_clicked,
            pack_kwargs={"padx": 16, "pady": (8, 16), "fill": "x"},
        )

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _on_save_clicked(self):
        which = self._tabs.get()
        try:
            if which == "Number":
                value = float(self._number_var.get())
                reg = Register(size=7)
                reg.set_bcd_number(value)
            elif which == "Text":
                text = self._text_var.get()
                if len(text) > 6:
                    raise ValueError(
                        f"Alpha data registers hold at most 6 characters (got {len(text)})."
                    )
                text.encode("ascii")
                data = bytearray(7)
                data[0] = 0x10
                padded = text.rjust(6, "\x00")
                data[1:7] = padded.encode("ascii")
                reg = Register(data=bytes(data))
            else:  # Hex
                reg = Register.from_hex(self._hex_var.get())
                if reg.size != 7:
                    raise ValueError(
                        f"Expected 14 hex characters (7 bytes), got {reg.size} bytes."
                    )
        except (ValueError, UnicodeEncodeError) as e:
            logger.warning("Invalid register value on %s tab: %s", which, e)
            messagebox.showerror("Invalid Value", str(e))
            return

        self._on_save(reg)
        self.destroy()
