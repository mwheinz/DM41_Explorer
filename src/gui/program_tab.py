"""
Programs tab: a read-only listing of the real, END-delimited programs in
program memory -- one row per program, matching how many programs CAT 1
would actually show, NOT one row per global label or per raw chain marker.
See docs/program.md sec 5.3 for the full derivation and memory.py's
Memory.list_programs() / Program / ProgramLabel for the implementation
this renders (built on top of the raw chain walk,
Memory.list_global_chain() / ProgramInfo, described in sec 5).

A program is delimited by an explicit plain END marker, never by a global
label: an HP-41 program can have zero, one, or several labels (the Labels
column lists them, comma-joined, or shows "(unlabelled)" if it has none),
and the single newest program in memory doesn't need an explicit END of
its own at all -- the permanent `.END.` sentinel can close it out instead.
This replaces an earlier version of this tab that showed one row per raw
chain link (LBL/END/.END.) with no notion of "a program" at all, built on
an earlier version of the grouping logic that also miscounted programs by
mistaking the zero-padding bytes kept in front of the permanent `.END.`
marker (for register alignment -- see docs/program.md sec 5.1) for a small
extra unnamed program. Both were caught by the user's own real-hardware
CAT 1 comparisons (tests/data/unlabelled.dm41, tests/data/twolabels.dm41).

Uses a native ttk.Treeview rather than one CustomTkinter widget per row --
the same performance/consistency fix as gui/data_registers_tab.py and
gui/xm_files_tab.py (see either module's docstring for the full story;
GitHub issues #21/#22). This tab does not let you edit program memory, so
unlike those two there's no double-click-to-edit -- but a selected row can
be exported to a standalone HP-41 program file (RAW, DAT, PPC, or TXT
format -- see memory/program_files.py), so there is one selection-driven
action button, "Export...".

Export uses Memory.get_program_bytes() (memory/memory.py) -- the exact
bytes CAT 1 would report for that program, from its own first instruction
through its own terminator (an explicit END, or the permanent `.END.` for
the single newest program). A program with more than one global label
(see tests/data/twolabels.dm41) exports as the single physical block its
row represents, starting at its oldest label's own header -- not as
separate per-label slices.

Import is the write side, not selection-driven -- a standalone RAW/DAT/
PPC/TXT file is always spliced in as the newest program
(Memory.import_program(), memory/memory.py), the only place it can safely
go without a much riskier "insert in the middle of the chain" algorithm
(see that method's own docstring for the full splicing rules -- converting
a `.END.`-terminated newest program into a real closing END,
register-aligning a fresh `.END.`, etc.). Any key assignment baked into an
imported label's own header is always cleared on the way in, and a
duplicate global label name blocks the import outright -- both deliberate
project decisions, not defaults `import_program()` picked on its own. A
TXT import goes through one extra step first, memory/program_files.py's
own `decode_program_txt()` (in turn memory/program_text.py's compiler,
docs/program_text_io_plan.md) -- everything past that point (splicing,
duplicate-name/key-assignment handling) is identical to RAW/DAT/PPC,
since all four formats converge on the same instruction bytes.

Remove (GitHub issue #6 -- "add the ability to remove programs", the last
of "add"/"edit"/"remove" to land, after Export/Import already covered
"add"/"edit") IS selection-driven, unlike Import: it deletes the selected
row's own program (Memory.remove_program()) and closes up whatever gap
that leaves behind, so every other program stays exactly as contiguous as
before -- this works for any program, not just the newest, unlike Import.
Any key assignment(s) the removed program's own label(s) held are cleared
as part of the removal (see remove_program()'s own docstring); the
confirmation dialog says so up front rather than only after the fact.
"""

import logging
from pathlib import Path
from tkinter import filedialog, messagebox
import customtkinter as ctk

from memory import (
    Memory,
    DM41MemoryError,
    encode_program_raw,
    encode_program_dat,
    encode_program_ppc,
    encode_program_txt,
    decode_program_raw,
    decode_program_dat,
    decode_program_txt,
)
from gui.tab_common import (
    build_tab_header,
    build_tab_treeview,
    build_caption_label,
    style_treeview,
    apply_row_tags,
    highlight_selected_row,
    clear_tree_for_render,
)

logger = logging.getLogger(__name__)

# Distinct ttk style name for this tab's Treeview/scrollbar -- see
# gui/tab_common.py's `style_treeview()` docstring for why this must NOT
# be the "Treeview"/"XMFiles.Treeview" style names the other two
# Treeview-based tabs use.
_TREE_STYLE = "Programs.Treeview"

_TREE_COLUMNS = [
    ("labels", "Labels", 220, True),
    ("address", "Address", 120, False),
    ("length", "Length", 120, False),
    ("key_assignment", "Key ASNs", 220, False),
]


class ProgramTab(ctk.CTkFrame):
    """Renders the real, END-delimited programs in a Memory object's
    program memory. Call `render(memory)` whenever the buffer changes."""

    def __init__(self, master, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self._memory: Memory = None
        self._programs: list = []
        self._on_change = on_change

        header, self._header_label = build_tab_header(self)
        ctk.CTkButton(
            header,
            text="Export...",
            width=90,
            command=self._export_selected,
        ).pack(side="right", padx=(0, 8))
        ctk.CTkButton(
            header,
            text="Import...",
            width=90,
            command=self._import_program,
        ).pack(side="right", padx=(0, 8))
        ctk.CTkButton(
            header,
            text="Remove",
            width=90,
            fg_color="#a03e3e",
            hover_color="#832f2f",
            command=self._remove_selected,
        ).pack(side="right", padx=(0, 8))

        self._caption = build_caption_label(
            self,
            "Each row indicates a separate HP41 program, separated by "
            "explicit END markers. The \"Label\" column lists the "
            "global labels contained in the program and each program may "
            "have zero or more global labels as identifiers.",
        )

        _, self._tree = build_tab_treeview(self, _TREE_COLUMNS, style=_TREE_STYLE)

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_selected)

    def refresh_theme(self):
        """Re-applies theme-dependent ttk styling/colors after
        ctk.set_appearance_mode() changes elsewhere (e.g. Preferences) --
        see gui/data_registers_tab.py's identical method for why this is
        needed at all (CustomTkinter's theme engine has no hook into
        ttk). This tab used to not need an equivalent call (its old
        CTkScrollableFrame render() recomputed stripe color fresh every
        time) -- now that it's a ttk.Treeview too, it does."""
        self._stripe_bg = style_treeview(_TREE_STYLE)
        apply_row_tags(self._tree, self._stripe_bg)

    def _on_tree_selected(self, event=None):  # pylint: disable=unused-argument
        """Gives the selected row a visible highlight, the same way (and
        for the same reason) as gui/tab_common.py's
        `highlight_selected_row()` docstring (GitHub issue #22) explains.
        Purely cosmetic here (this tab has no selection-driven action),
        kept for visual consistency with the other two tables."""
        highlight_selected_row(self._tree)

    def render(self, memory: Memory):
        self._memory = memory
        self._programs = []
        if clear_tree_for_render(self._tree, self._header_label, memory):
            return

        try:
            programs = memory.programs.list_programs()
        except Exception as e:
            logger.warning("Could not list programs: %s", e)
            self._header_label.configure(text=f"Could not list programs: {e}")
            return

        self._programs = programs

        if not programs:
            self._header_label.configure(text="Programs in memory: 0")
            return

        self._header_label.configure(text=f"Programs in memory: {len(programs)}")

        for pos, program in enumerate(programs):
            self._tree.insert(
                "",
                "end",
                iid=str(pos),
                values=(
                    program.names_label,
                    program.address_label,
                    program.length_label,
                    self._key_assignment_text(program),
                ),
                tags=("oddrow",) if pos % 2 else (),
            )

    @staticmethod
    def _key_assignment_text(program) -> str:
        # An unlabelled program has no key-assignment byte at all (that
        # lives in a global label's own header -- sec 4.6/5.2). A program
        # with one label shows its assignment plainly; one with several
        # (tests/data/twolabels.dm41) shows each label's own status,
        # since each label's header holds an independent key byte.
        if not program.labels:
            return ""
        if len(program.labels) == 1:
            return program.labels[0].key_assignment_text
        return ", ".join(
            f"{label.key_assignment_text}" for label in program.labels
        )

    def _selected_program(self):
        """The Program for the currently-selected row, or None if
        nothing (or something that's since gone stale, e.g. after a
        reload) is selected."""
        selection = self._tree.selection()
        if not selection:
            return None
        try:
            pos = int(selection[0])
        except ValueError:
            return None
        if pos < 0 or pos >= len(self._programs):
            return None
        return self._programs[pos]

    _EXPORT_FORMATS = {
        ".raw": ("RAW", encode_program_raw),
        ".dat": ("DAT", encode_program_dat),
        ".ppc": ("PPC", encode_program_ppc),
        ".txt": ("TXT", encode_program_txt),
    }

    def _export_selected(self):
        """Exports the selected row's own program bytes
        (Memory.get_program_bytes() -- this module's docstring) as a
        standalone HP-41 program file, in whichever of RAW/DAT (hp41uc's
        own formats), PPC (not an hp41uc format), or TXT (a plain-text
        keystroke listing, memory/program_text.py -- also not an hp41uc
        *file* format of its own, but hp41uc-style text either way; see
        memory/program_files.py) the chosen filename's extension picks.
        Works the same for a labelled program or an unlabelled one (only
        local labels, or none at all)."""
        if self._memory is None:
            messagebox.showwarning(
                "No Program Memory", "Load or start a memory buffer first."
            )
            return

        program = self._selected_program()
        if program is None:
            messagebox.showinfo("No Selection", "Select a program row first.")
            return

        try:
            instruction_bytes = self._memory.programs.get_program_bytes(program)
        except (ValueError, DM41MemoryError) as e:
            logger.warning(
                "Could not read program bytes for %r: %s", program.names_label, e
            )
            messagebox.showerror("Could Not Export", str(e))
            return

        first_label = program.labels[0].name if program.is_named else None
        program_label = (
            first_label.strip()
            if first_label
            else f"unlabelled program @ {program.address_label}"
        )
        default_stub = (
            first_label.strip()
            if first_label
            else f"unlabelled_{program.start_addr:03x}_{program.start_offset}"
        )
        default_name = f"{default_stub}.raw"
        path = filedialog.asksaveasfilename(
            initialfile=default_name,
            defaultextension=".raw",
            filetypes=[
                ("RAW files", "*.raw"),
                ("DAT files", "*.dat"),
                ("PPC files", "*.ppc"),
                ("TXT files", "*.txt"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        extension = Path(path).suffix.lower()
        format_label, encoder = self._EXPORT_FORMATS.get(
            extension, self._EXPORT_FORMATS[".raw"]
        )

        try:
            file_bytes = encoder(instruction_bytes)
            Path(path).write_bytes(file_bytes)
        except (OSError, ValueError) as e:
            logger.warning(
                "Could not export program %r to %s: %s", program_label, path, e
            )
            messagebox.showerror("Could Not Export", str(e))
            return

        logger.info(
            "Exported program %r (%d bytes) as %s to %s",
            program_label,
            len(instruction_bytes),
            format_label,
            path,
        )
        messagebox.showinfo(
            "Exported",
            f"{program_label} ({len(instruction_bytes)} bytes) "
            f"written as {format_label} to {path}",
        )

    def _notify_change(self):
        if self._on_change:
            self._on_change()

    _IMPORT_FORMATS = {
        ".raw": ("RAW", decode_program_raw),
        ".dat": ("DAT", decode_program_dat),
        ".ppc": ("PPC", decode_program_dat),
        ".txt": ("TXT", decode_program_txt),
    }

    def _import_program(self):
        """Reads a standalone HP-41 program file (RAW, DAT, PPC, or TXT,
        whichever the chosen filename's extension picks --
        memory/program_files.py)
        and splices it into program memory as the newest program
        (Memory.import_program()). Not selection-driven -- there's no
        "insert at this row" concept for an Import, only "add as the
        newest program" (see this module's own docstring)."""
        if self._memory is None:
            messagebox.showwarning(
                "No Program Memory", "Load or start a memory buffer first."
            )
            return

        path = filedialog.askopenfilename(
            filetypes=[
                ("RAW files", "*.raw"),
                ("DAT files", "*.dat"),
                ("PPC files", "*.ppc"),
                ("TXT files", "*.txt"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        extension = Path(path).suffix.lower()
        decoder_entry = self._IMPORT_FORMATS.get(extension)
        if decoder_entry is None:
            messagebox.showerror(
                "Could Not Import",
                f"{Path(path).name}: unrecognized file extension "
                f"{extension or '(none)'!r} -- expected .raw, .dat, .ppc, or .txt.",
            )
            return
        format_label, decoder = decoder_entry

        try:
            file_bytes = Path(path).read_bytes()
            instruction_bytes = decoder(file_bytes)
        except (OSError, DM41MemoryError) as e:
            logger.warning("Could not read %s program file %s: %s", format_label, path, e)
            messagebox.showerror("Could Not Import", str(e))
            return

        try:
            imported = self._memory.programs.import_program(instruction_bytes)
            self._memory.programs.repack()
        except (ValueError, DM41MemoryError) as e:
            logger.warning("Could not import program from %s: %s", path, e)
            messagebox.showerror("Could Not Import", str(e))
            return

        # repack()'s own docstring: it rebuilds program memory from a
        # forward opcode scan specifically so "a global label that's
        # physically present but not currently chain-linked ... becomes
        # visible again" -- exactly the case for a TXT import, since this
        # project's own compiler (matching hp41uc's own, see
        # memory/program_text.py's decode_program_txt() docstring) always
        # emits zero/unlinked chain markers, and RAW/DAT read from an
        # hp41uc-compiled file (e.g. tests/data/tower.raw) can carry the
        # same thing over from that file. `imported`, captured right
        # after import_program() but before that repair runs, can
        # therefore be stale -- reporting "(unlabelled)" for a program
        # repack() then correctly re-recognizes by name. Re-fetch the
        # newest program from the freshly-rebuilt list instead of
        # trusting the pre-repack object for the log/success message
        # below (import_program()'s own contract: always spliced in as
        # the newest program, so it's always list_programs()'s last
        # entry once repack() finishes).
        imported = self._memory.programs.list_programs()[-1]

        logger.info(
            "Imported program %r (%d bytes) as %s from %s",
            imported.names_label,
            imported.length,
            format_label,
            path,
        )
        self._notify_change()
        self.render(self._memory)
        messagebox.showinfo(
            "Imported",
            f"{imported.names_label} ({imported.length} bytes) "
            f"imported from {path} as the newest program.",
        )

    def _remove_selected(self):
        """Header-button version of Remove, acting on whatever row is
        currently selected -- same pattern as gui/xm_files_tab.py's own
        _remove_selected()/_remove_file(). Unlike Import, this is
        selection-driven: any program can be removed, not just the
        newest one (Memory.remove_program() closes up the gap that
        leaves behind, wherever it is -- see that method's docstring)."""
        if self._memory is None:
            messagebox.showwarning(
                "No Program Memory", "Load or start a memory buffer first."
            )
            return

        program = self._selected_program()
        if program is None:
            messagebox.showinfo("No Selection", "Select a program row first.")
            return
        self._remove_program(program)

    def _remove_program(self, program):
        """Confirms and removes `program` (Memory.remove_program()) --
        the write-side counterpart to _export_selected(). Mentions any
        key assignment(s) the program's own label(s) hold up front in the
        confirmation, since removing it clears them (see
        Memory.remove_program()'s own docstring)."""
        assignment_note = ""
        if any(label.key_assignment for label in program.labels):
            assignment_note = (
                "\n\nIts key assignment(s) will be cleared as part of this."
            )
        if not messagebox.askyesno(
            "Remove Program",
            f"Remove {program.names_label} ({program.length_label}) from "
            f"program memory?{assignment_note}",
        ):
            return

        try:
            self._memory.programs.remove_program(program)
        except (ValueError, DM41MemoryError) as e:
            logger.warning(
                "Could not remove program %r: %s", program.names_label, e
            )
            messagebox.showerror("Could Not Remove Program", str(e))
            return

        logger.info(
            "Program removed: %r (%s)", program.names_label, program.length_label
        )
        self._notify_change()
        self.render(self._memory)
        messagebox.showinfo(
            "Removed",
            f"{program.names_label} ({program.length_label}) removed "
            "from program memory.",
        )
