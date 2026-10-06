"""
Flags tab: the 56 user/system flags (register d, 0x0e), editable, with
names loaded live from docs/flags.md.
"""

import logging
import customtkinter as ctk

from memory import Memory
from gui.flags_doc import load_flag_names
from gui.scroll_support import bind_touchpad_scroll
from gui.tab_common import build_tab_header, CARD_KWARGS

logger = logging.getLogger(__name__)

FLAG_COUNT = 56


class FlagsTab(ctk.CTkFrame):
    """Renders the 56 flags as checkboxes. Call `render(memory)` whenever
    the buffer changes."""

    def __init__(self, master, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self._memory: Memory = None
        self._on_change = on_change
        self._flag_names = load_flag_names()
        self._flag_vars = {}
        self._suspend_flag_callbacks = False

        _, self._header_label = build_tab_header(self)

        self._body = ctk.CTkScrollableFrame(self, **CARD_KWARGS)
        self._body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        bind_touchpad_scroll(self._body)

    def _notify_change(self):
        if self._on_change:
            self._on_change()

    def render(self, memory: Memory):
        self._memory = memory
        for widget in self._body.winfo_children():
            widget.destroy()
        self._flag_vars = {}

        if memory is None:
            self._header_label.configure(text="(no memory state loaded)")
            return

        try:
            current = memory.status_registers.get_all_flags()
        except Exception as e:
            logger.warning("Could not decode flags: %s", e)
            self._header_label.configure(text=f"Could not decode flags: {e}")
            return

        self._header_label.configure(
            text="HP41 Flags: Note that some flags"
            " will change value on calculator"
            " restart. Others have no effect on"
            " the emulator."
        )

        self._suspend_flag_callbacks = True
        columns = 4
        # Fill down each column before starting the next (0-13 in column 0,
        # 14-27 in column 1, ...) rather than filling across each row, so
        # flag numbers read top-to-bottom in a column like a list.
        rows = -(-FLAG_COUNT // columns)  # ceil division
        for n in range(FLAG_COUNT):
            var = ctk.BooleanVar(value=current[n])
            self._flag_vars[n] = var
            label = f"{n:02d} {self._flag_names.get(n, '')}"
            cb = ctk.CTkCheckBox(
                self._body,
                text=label,
                variable=var,
                command=lambda n=n: self._on_flag_toggled(n),
            )
            c, r = divmod(n, rows)
            cb.grid(row=r, column=c, sticky="w", padx=8, pady=2)
        self._suspend_flag_callbacks = False

    def _on_flag_toggled(self, n: int):
        if self._suspend_flag_callbacks or self._memory is None:
            return
        value = self._flag_vars[n].get()
        self._memory.status_registers.set_flag(n, value)
        logger.info("Flag %02d set to %s", n, value)
        self._notify_change()
