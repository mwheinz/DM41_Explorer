"""
Overview tab: status registers, the R00/.END. partition, and a summary of
how memory is divided up. Flags live in their own tab (gui/flags_tab.py).
"""

import logging
from tkinter import messagebox
import customtkinter as ctk

from memory import (
    Memory,
    DM41MemoryError,
    DeviceProfile,
    PRIMARY_DATA_END,
    MIN_SANE_R00,
)
from gui.scroll_support import bind_touchpad_scroll
from gui.tab_common import MONOSPACE_FONT_FAMILY, CARD_KWARGS

logger = logging.getLogger(__name__)

# Data registers, programs, key-assignments, and alarm storage all live at/
# above the start of Key Assignments; see docs/memory.md. Data registers
# occupy the space between the register addressed by the status register
# field R00. Program storage exists between R00-1 and the register pointed
# to by status register field ".END.". If key assignments exist, they start
# at the bottom of this span and extend towards ".END.". If alarms exist,
# they occupy the space immediately above the key assignments, still below
# ".END.". Note that key assignments, alarms, and programs are all
# optional -- they may not exist.
#
# Key Assignments and Alarms both sit within this same span, packed
# immediately above one another with no gap -- they are NOT part of
# "unused" program memory. _render_summary() below subtracts both out of
# its "Unused program memory" figure, and _render_partition() shows their
# bounds directly (GitHub issue #23 -- neither used to be accounted for
# or shown at all). Both methods now source every one of these boundaries
# from Memory.regions() (issue #25) -- by key ("key", "alarms", "unused",
# "program", "data") -- rather than computing them independently, so this
# module no longer needs to know any of the raw address math itself.

# Raw structural XM capacity comes from the memory's device profile
# (DeviceProfile.xm_raw_registers): each region's usable span is (lo, hi] --
# lo itself is that region's reserved link/pointer register (0x40 for region
# 0, 0x201 for region 1, 0x301 for a DM41X's region 2), not available for
# file storage (see memory/xm_file.py's ExtendedMemory docstring). It is a
# fixed, dump-independent ceiling, not something read from the dump itself
# -- but it is NOT the same number a real calculator's EMDIR command
# reports (see xm_total_registers() below).

# Registers an XM file consumes beyond its own declared data: one header
# register and one name register, packed directly above its data (see
# XMFile.num_registers' docstring in memory/xm_file.py -- that property
# only counts the file's data segments, not this per-file overhead).
XM_FILE_OVERHEAD_REGISTERS = 2

# EMDIR's "registers available" is the profile's raw capacity minus two
# more kinds of overhead that aren't tied to any one file. One register is always
# spent on the FF-filled sentinel that marks where free space starts
# (see memory/constants.py's eom_register() and
# ExtendedMemory.list_files()) -- that's XM_EOM_SENTINEL_REGISTERS.
# EMDIR's own count is then defined as "how many registers a file
# created right now could use", which reserves the 2-register header+
# name overhead of that hypothetical next file on top of the sentinel --
# that's XM_NEXT_FILE_RESERVE_REGISTERS (deliberately the same value as
# XM_FILE_OVERHEAD_REGISTERS above, but conceptually distinct: this one
# is reserved capacity, not registers a real file has claimed).
#
# Per "HP-41 Advanced Programming Tips" p.29 (docs/pdfs/
# hp41-adv-prog-tips.pdf): for an N-device XM system, available =
# raw - N (one link register per device, already excluded from
# the raw capacity) - 1 (the shared FF sentinel), and EMDIR itself
# reports 2 less than that. Confirmed against a real DM41L: a
# freshly-cleared XM (0 files) reports 362 registers available via
# EMDIR, matching raw capacity 365 - 1 (sentinel) - 2 (EMDIR's
# next-file reserve) for the DM41L's 2-device configuration (the
# built-in Extended Functions Module plus one Extended Memory module).
XM_EOM_SENTINEL_REGISTERS = 1
XM_NEXT_FILE_RESERVE_REGISTERS = 2


def xm_total_registers(profile: DeviceProfile) -> int:
    """What EMDIR reports on `profile`'s calculator with extended memory
    empty: 362 on a DM41L, and 600 on a DM41X (confirmed on a real DM41X,
    docs/dm41x_explorer_plan.md S8)."""
    return (
        profile.xm_raw_registers
        - XM_EOM_SENTINEL_REGISTERS
        - XM_NEXT_FILE_RESERVE_REGISTERS
    )


class OverviewTab(ctk.CTkScrollableFrame):
    """Renders a Memory object's status registers, R00/.END. partition, and
    a register-usage summary. Call `render(memory)` whenever the buffer
    changes."""

    def __init__(self, master, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self._memory: Memory = None
        self._on_change = on_change
        self._r00_var = None

        bind_touchpad_scroll(self)

        self.columnconfigure(0, weight=1, uniform="col")
        self.columnconfigure(1, weight=1, uniform="col")

        self._stack_frame = self._make_card(0, 0, "Stack & Alpha Registers")
        self._summary_frame = self._make_card(0, 1, "Memory Summary")
        self._system_frame = self._make_card(1, 0, "System Registers")
        self._partition_frame = self._make_card(1, 1, "Memory Partitions")

    def _make_card(self, row, column, title):
        """Creates a bordered/tinted 'card' frame at (row, column) with a
        bold title, used to visually separate sections from each other."""
        card = ctk.CTkFrame(self, **CARD_KWARGS)
        card.grid(row=row, column=column, sticky="nsew", padx=8, pady=4)
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(10, 6)
        )
        return card

    def _notify_change(self):
        if self._on_change:
            self._on_change()

    # -- Top-level render ---------------------------------------------------

    def render(self, memory: Memory):
        self._memory = memory
        self._render_stack_registers()
        self._render_system_registers()
        self._render_partition()
        self._render_summary()

    # -- Status registers -----------------------------------------------

    def _clear_below_title(self, frame):
        # Row 0 holds the card title -- leave it, clear everything else.
        for widget in frame.winfo_children():
            info = widget.grid_info()
            if info and int(info.get("row", 0)) > 0:
                widget.destroy()

    def _render_grid_rows(self, frame, rows, start_row=1, columns=2):
        for i, (label, value) in enumerate(rows):
            r, c = divmod(i, columns)
            cell = ctk.CTkFrame(frame, fg_color="transparent")
            cell.grid(row=start_row + r, column=c, sticky="w", padx=10, pady=0)
            ctk.CTkLabel(cell, text=f"{label}:", width=90, anchor="w").pack(side="left")
            ctk.CTkLabel(
                cell,
                text=value,
                font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
                anchor="w",
            ).pack(side="left")
        return start_row + (-(-len(rows) // columns) if rows else 0)  # ceil div

    def _render_stack_registers(self):
        self._clear_below_title(self._stack_frame)

        if self._memory is None:
            ctk.CTkLabel(self._stack_frame, text="(no memory dump loaded)").grid(
                row=1, column=0, padx=10, pady=(0, 10), sticky="w"
            )
            return

        sr = self._memory.status_registers

        stack_rows = [
            ("T", f"{sr.T().get_bcd_number():.8g}"),
            ("Z", f"{sr.Z().get_bcd_number():.8g}"),
            ("Y", f"{sr.Y().get_bcd_number():.8g}"),
            ("X", f"{sr.X().get_bcd_number():.8g}"),
            ("LastX", f"{sr.LastX().get_bcd_number():.8g}"),
        ]
        next_row = self._render_grid_rows(
            self._stack_frame, stack_rows, start_row=1, columns=2
        )

        ctk.CTkLabel(self._stack_frame, text="Alpha (M-P, combined):", anchor="w").grid(
            row=next_row, column=0, sticky="w", padx=10, pady=(8, 0)
        )
        ctk.CTkLabel(
            self._stack_frame,
            text=repr(str(sr.alpha)),
            font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
            anchor="w",
        ).grid(
            row=next_row, column=1, columnspan=4, sticky="w", padx=10,
            pady=(8,0)
        )

        mnop_rows = [
            ("M", repr(sr.M().get_ascii())),
            ("N", repr(sr.N().get_ascii())),
            ("O", repr(sr.O().get_ascii())),
            ("P", repr(sr.P().get_ascii())),
        ]
        self._render_grid_rows(
            self._stack_frame, mnop_rows, start_row=next_row + 2, columns=2
        )
        # Bottom padding on the last row.
        ctk.CTkFrame(self._stack_frame, fg_color="transparent", height=6).grid(
            row=next_row + 4, column=0
        )

    def _render_system_registers(self):
        self._clear_below_title(self._system_frame)

        if self._memory is None:
            ctk.CTkLabel(self._system_frame, text="(no memory dump loaded)").grid(
                row=1, column=0, padx=10, pady=(0, 10), sticky="w"
            )
            return

        sr = self._memory.status_registers

        rows = [
            ("Q (scratch)", sr.Q().get_hex()),
            ("R (Append)", sr.R().get_hex()),
            ("a (Ret. stack)", sr.a().get_hex()),
            ("b (Ret. stack)", sr.b().get_hex()),
            ("c", sr.c().get_hex()),
            ("d (flags)", sr.d().get_hex()),
            ("e", sr.e().get_hex()),
        ]
        self._render_grid_rows(self._system_frame, rows, start_row=1, columns=2)

    # -- R00 / .END. / SREG partition ------------------------------------

    def _render_partition(self):
        self._clear_below_title(self._partition_frame)

        if self._memory is None:
            return

        try:
            r00 = self._memory.status_registers.R00()
            dot_end = self._memory.status_registers.DotEnd()
            sigma_reg = self._memory.status_registers.SigmaReg()
        except Exception as e:
            logger.warning("Could not decode register c: %s", e)
            ctk.CTkLabel(
                self._partition_frame, text=f"Could not decode register c: {e}"
            ).grid(row=1, column=0, padx=10, pady=(0, 10), sticky="w")
            return

        if r00 < MIN_SANE_R00:
            ctk.CTkLabel(
                self._partition_frame,
                text="No dump loaded yet -- start a new buffer or load/read a dump first.",
                text_color="gray60",
            ).grid(row=1, column=0, columnspan=3, padx=10, pady=(0, 10), sticky="w")
            return

        ctk.CTkLabel(self._partition_frame, text="R00 (data register 00):").grid(
            row=1, column=0, sticky="w", padx=10, pady=(2, 10)
        )
        self._r00_var = ctk.StringVar(value=f"0x{r00:03x}")
        entry = ctk.CTkEntry(
            self._partition_frame,
            textvariable=self._r00_var,
            width=100,
            font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
        )
        entry.grid(row=1, column=1, sticky="w", padx=10, pady=(2, 10))
        ctk.CTkButton(
            self._partition_frame, text="Apply", width=70, command=self._apply_r00
        ).grid(row=1, column=2, sticky="w", padx=(0, 10), pady=(2, 10))

        ctk.CTkLabel(self._partition_frame, text=".END.:").grid(
            row=2, column=0, sticky="w", padx=10, pady=2
        )
        ctk.CTkLabel(
            self._partition_frame,
            text=f"0x{dot_end:03x}",
            font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
        ).grid(row=2, column=1, columnspan=2, sticky="w", padx=10, pady=2)

        ctk.CTkLabel(self._partition_frame, text="ΣREG address:").grid(
            row=3, column=0, sticky="w", padx=10, pady=2
        )
        ctk.CTkLabel(
            self._partition_frame,
            text=f"0x{sigma_reg:03x}",
            font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
        ).grid(row=3, column=1, sticky="w", padx=10, pady=2)

        # Key Assignments and Alarms sit back-to-back, below whatever's left
        # for Program storage -- see this module's header comment. Shown as
        # their own partitions (not just folded into the Memory Summary
        # card's register counts) so the boundary between them is visible
        # here too, matching how R00/.END. are shown as boundaries above
        # (GitHub issue #23). Both spans come straight from
        # Memory.regions() (issue #25) -- RegionSpan's start/end are
        # inclusive, so _format_partition_span (which takes an exclusive
        # end) is passed `span.end + 1`.
        spans = {span.key: span for span in self._memory.regions()}
        key_span = spans["key"]
        alarms_span = spans["alarms"]

        ctk.CTkLabel(self._partition_frame, text="Key Assignments:").grid(
            row=4, column=0, sticky="w", padx=10, pady=2
        )
        ctk.CTkLabel(
            self._partition_frame,
            text=self._format_partition_span(key_span.start, key_span.end + 1),
            font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
        ).grid(row=4, column=1, columnspan=2, sticky="w", padx=10, pady=2)

        ctk.CTkLabel(self._partition_frame, text="Alarms:").grid(
            row=5, column=0, sticky="w", padx=10, pady=(2, 10)
        )
        ctk.CTkLabel(
            self._partition_frame,
            text=self._format_partition_span(alarms_span.start, alarms_span.end + 1),
            font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
        ).grid(row=5, column=1, columnspan=2, sticky="w", padx=10, pady=(2, 10))

    @staticmethod
    def _format_partition_span(start: int, end: int) -> str:
        """Formats a [start, end) register span for the Memory Partitions
        card -- 'none' when the span is empty (start == end, i.e. that
        partition has nothing in it), otherwise its address range and
        register count. Takes a half-open (exclusive end) span, matching
        this method's original signature -- callers sourcing a RegionSpan
        (inclusive end) from Memory.regions() pass `span.end + 1`."""
        if end <= start:
            return "(none)"
        count = end - start
        return f"0x{start:03x}-0x{end - 1:03x} ({count} register{'s' if count != 1 else ''})"

    def _apply_r00(self):
        text = self._r00_var.get().strip()
        try:
            value = int(text, 16)
        except ValueError:
            logger.warning("Invalid R00 entry: %r", text)
            messagebox.showerror("Invalid R00", f"'{text}' is not a valid hex address.")
            return

        if not messagebox.askyesno(
            "Change R00",
            "This directly rewrites the R00 partition pointer. It does NOT "
            "move, clear, or resize any register contents on either side of "
            "the new boundary -- it can expose stale program bytes as data, "
            "or hide real data registers behind the new program-memory "
            "boundary. Continue?",
        ):
            return

        try:
            self._memory.status_registers.set_R00(value)
        except ValueError as e:
            logger.warning("Could not set R00 to 0x%03x: %s", value, e)
            messagebox.showerror("Invalid R00", str(e))
            return

        logger.info("R00 changed to 0x%03x", value)
        self._notify_change()
        self.render(self._memory)

    # -- Memory usage summary ---------------------------------------------

    def _xm_summary_texts(self):
        """Returns (files_text, used_text, free_text) for the Memory
        Summary card's Extended-memory rows. Factored out of
        _render_summary() so that method stays under pylint's
        max-locals=20 -- this XM used/free percentage math alone needs
        about eight locals, and _render_summary() also now has to track
        the Key Assignments/Alarms register counts (GitHub issue #23)."""
        try:
            xm = self._memory.extended_memory
            xm_total = xm_total_registers(self._memory.profile)
            xm_files = xm.list_files()
            xm_used = sum(
                f.num_registers + XM_FILE_OVERHEAD_REGISTERS for f in xm_files
            )
            # xm_used can legitimately exceed the profile's total: that
            # figure already has EMDIR's next-file reserve subtracted
            # out (see xm_total_registers()), and a real file's own
            # header+name overhead is exactly what eats into that
            # reserve once the file actually exists. Clamp the
            # free/percentage figures rather than showing negative
            # numbers -- a real DM41L would report 0 free (and refuse
            # new files), never a negative count.
            xm_free = max(0, xm_total - xm_used)
            xm_used_pct = min(100, round(100 * xm_used / xm_total))
            return (
                str(len(xm_files)),
                f"{xm_used}/{xm_total} registers ({xm_used_pct}%)",
                f"{xm_free}/{xm_total} registers ({100 - xm_used_pct}%)",
            )
        except DM41MemoryError as e:
            logger.warning("Could not list XM files for summary: %s", e)
            return f"could not be listed ({e})", "unknown", "unknown"

    def _render_summary(self):
        self._clear_below_title(self._summary_frame)

        if self._memory is None:
            return

        # Memory.has_program_partition() is the single source of truth
        # for whether this dump has a sane R00/.END. partition, replacing
        # this method's old separate R00()/DotEnd()/MIN_SANE_R00 check
        # (GitHub issue #25). It is also exactly what decides whether the
        # "program"/"data" spans appear in regions()'s output below, so
        # the two can't disagree. Very slightly stricter than the old
        # check (which only looked at R00, not R00 vs .END.), but that old
        # combination -- a sane R00 with .END. above it -- was already not
        # a state _render_partition() treats as a normal partition either,
        # so no real dump should ever notice the difference.
        spans = {span.key: span for span in self._memory.regions()}
        has_partition = self._memory.has_program_partition()

        xm_text, xm_used_text, xm_free_text = self._xm_summary_texts()

        rows = []
        if has_partition:
            # Key Assignments and Alarms both live within the same span as
            # "unused" program memory (see this module's header comment) --
            # registers either one has actually claimed are not free, so
            # both are subtracted out of "Unused program memory" below
            # rather than counted as available space (GitHub issue #23).
            key_assignment_regs = spans["key"].count
            alarm_regs = spans["alarms"].count
            reserved = spans["data"].count
            consumed = spans["program"].count
            available = spans["unused"].count
            rows.append(
                (
                    "User memory locations",
                    f"00-{reserved-1} (R00-0x{PRIMARY_DATA_END:03x})",
                )
            )
            rows.append(("Program storage consumed", f"{consumed} registers"))
            rows.append(("Key assignments", f"{key_assignment_regs} registers"))
            rows.append(("Alarms", f"{alarm_regs} registers"))
            rows.append(
                (
                    "Unused program memory",
                    f"{available} registers (approx.)",
                )
            )
        rows.append(("Extended-memory files", xm_text))
        rows.append(("XM memory used", xm_used_text))
        rows.append(("XM memory free", xm_free_text))

        for i, (label, value) in enumerate(rows, start=1):
            ctk.CTkLabel(self._summary_frame, text=f"{label}:", anchor="w").grid(
                row=i, column=0, sticky="nw", padx=10, pady=2
            )
            ctk.CTkLabel(
                self._summary_frame,
                text=value,
                anchor="w",
                justify="left",
                wraplength=280,
                font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
            ).grid(row=i, column=1, sticky="w", padx=10, pady=2)
        ctk.CTkFrame(self._summary_frame, fg_color="transparent", height=6).grid(
            row=len(rows) + 1, column=0
        )
