"""
Programs tab: a read-only listing of the real, END-delimited programs in
program memory -- one row per program, matching how many programs CAT 1
would actually show, NOT one row per global label or per raw chain marker.
See docs/program.md sec 5.3 for the full derivation and
ProgramMemory.list_programs() / Program / ProgramLabel for the implementation
this renders (built on top of the raw chain walk,
ProgramMemory.list_global_chain() / ProgramInfo, described in sec 5).

A program is delimited by an explicit plain END marker, never by a global
label: an HP-41 program can have zero, one, or several labels (the Labels
column lists them, comma-joined, or shows "(unlabelled)" if it has none),
and the single newest program in memory doesn't need an explicit END of
its own at all -- the permanent `.END.` sentinel can close it out instead.

Uses a native ttk.Treeview rather than one CustomTkinter widget per row,
like gui/data_registers_tab.py and gui/xm_files_tab.py, for speed with
many rows. There's no double-click-to-edit here -- but the selected
rows can be exported to a standalone HP-41 program file (RAW, DAT, PPC,
or TXT format -- see memory/program_files.py), so there is one
selection-driven action button, "Export...". Several rows can be
selected (Shift/Cmd/Ctrl-click); they go into one file, oldest first,
the way a multi-program file is read back on Import (#71).

Export uses ProgramMemory.get_program_bytes() (memory/program_memory.py) -- the exact
bytes CAT 1 would report for that program, from its own first instruction
through its own terminator (an explicit END, or the permanent `.END.` for
the single newest program). A program with more than one global label
(see tests/data/twolabels.dm41) exports as the single physical block its
row represents, starting at its oldest label's own header -- not as
separate per-label slices.

Import is the write side, not selection-driven -- a standalone RAW/DAT/
PPC/TXT file is always spliced in as the newest program
(ProgramMemory.import_program()), the one place it can go without the
much riskier job of inserting into the middle of the chain (see that
method's docstring for the splicing rules). Any key assignment in an
imported label's header is cleared on the way in, and a duplicate global
label name blocks the import outright. A TXT file is first compiled to
instruction bytes (memory/program_files.py's `decode_program_txt()`,
backed by memory/program_text.py); from there all four formats are
handled identically. A file may hold several programs, each closed by
its own END -- a whole application, say (#71); they are imported in
file order, all or none (ProgramMemory.import_programs()).

Remove (GitHub issue #6) IS selection-driven, unlike Import: it deletes
the selected row's program (one row only) (ProgramMemory.remove_program()) and closes up
the gap it leaves, so every other program stays contiguous -- this works
for any program, not just the newest.
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
    split_programs,
)
from gui.tab_common import (
    DANGER_BUTTON_KWARGS,
    fit_columns,
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
            **DANGER_BUTTON_KWARGS,
            command=self._remove_selected,
        ).pack(side="right", padx=(0, 8))

        self._caption = build_caption_label(
            self,
            "Each row indicates a separate HP41 program, separated by "
            'explicit END markers. The "Label" column lists the '
            "global labels contained in the program and each program may "
            "have zero or more global labels as identifiers.",
        )

        _, self._tree = build_tab_treeview(
            self, _TREE_COLUMNS, style=_TREE_STYLE, selectmode="extended"
        )

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_selected)

    def refresh_theme(self):
        """Re-applies theme-dependent ttk styling/colors after
        ctk.set_appearance_mode() changes elsewhere (e.g. Preferences) --
        see gui/data_registers_tab.py's identical method for why this is
        needed at all (CustomTkinter's theme engine has no hook into
        ttk)."""
        self._stripe_bg = style_treeview(_TREE_STYLE)
        apply_row_tags(self._tree, self._stripe_bg)

    def _on_tree_selected(self, event=None):
        """Gives the selected row a visible highlight, the same way (and
        for the same reason) as gui/tab_common.py's
        `highlight_selected_row()` docstring (GitHub issue #22) explains.
        Purely cosmetic: it marks the rows Export/Remove act on."""
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
        fit_columns(self._tree, _TREE_COLUMNS)

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
        return ", ".join(f"{label.key_assignment_text}" for label in program.labels)

    def _selected_programs(self) -> list:
        """The Programs for the selected rows, in memory order (oldest
        first) whatever order they were clicked in. Rows that have since
        gone stale (e.g. after a reload) are left out."""
        positions = set()
        for iid in self._tree.selection():
            try:
                pos = int(iid)
            except ValueError:
                continue
            if 0 <= pos < len(self._programs):
                positions.add(pos)
        return [self._programs[pos] for pos in sorted(positions)]

    _EXPORT_FORMATS = {
        ".raw": ("RAW", encode_program_raw),
        ".dat": ("DAT", encode_program_dat),
        ".ppc": ("PPC", encode_program_ppc),
        ".txt": ("TXT", encode_program_txt),
    }

    def _export_selected(self):
        """Exports the selected rows' own program bytes
        (ProgramMemory.get_program_bytes() -- this module's docstring),
        oldest first, as one standalone HP-41 program file, in whichever of RAW/DAT (hp41uc's
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

        programs = self._selected_programs()
        if not programs:
            messagebox.showinfo("No Selection", "Select a program row first.")
            return

        chunks = []
        for program in programs:
            try:
                chunks.append(self._memory.programs.get_program_bytes(program))
            except (ValueError, DM41MemoryError) as e:
                logger.warning(
                    "Could not read program bytes for %r: %s", program.names_label, e
                )
                messagebox.showerror("Could Not Export", str(e))
                return
        instruction_bytes = b"".join(chunks)

        labels = [self._export_label(program) for program in programs]
        program_label = (
            labels[0]
            if len(labels) == 1
            else f"{len(labels)} programs ({', '.join(labels)})"
        )
        # The file is named after the oldest selected program.
        program = programs[0]
        first_label = program.labels[0].name if program.is_named else None
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

    @staticmethod
    def _export_label(program) -> str:
        """How the Export log and messages name `program`."""
        if program.is_named:
            return program.labels[0].name.strip()
        return f"unlabelled program @ {program.address_label}"

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
        and splices each program in it into program memory, in file order,
        as the newest (ProgramMemory.import_programs(), #71). Not selection-driven -- there's no
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
            programs = split_programs(decoder(file_bytes))
        except (OSError, DM41MemoryError) as e:
            logger.warning(
                "Could not read %s program file %s: %s", format_label, path, e
            )
            messagebox.showerror("Could Not Import", str(e))
            return

        try:
            # One transaction: if repack() rejects the result, the import
            # is undone too, matching the "Could Not Import" message.
            with self._memory.transaction():
                self._memory.programs.import_programs(programs)
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
        # same thing over from that file. import_program()'s own return
        # value, from before that repair runs, can therefore be stale --
        # reporting "(unlabelled)" for a program repack() then correctly
        # re-recognizes by name. Take the newest program from the
        # freshly-rebuilt list instead for the log/success message
        # below (import_program()'s own contract: always spliced in as
        # the newest program, so the imported ones are always
        # list_programs()'s last entries once repack() finishes).
        imported = self._memory.programs.list_programs()[-len(programs) :]
        summary = ", ".join(f"{p.names_label} ({p.length} bytes)" for p in imported)

        logger.info("Imported %s as %s from %s", summary, format_label, path)
        self._notify_change()
        self.render(self._memory)
        if len(imported) == 1:
            message = f"{summary} imported from {path} as the newest program."
        else:
            message = (
                f"{len(imported)} programs imported from {path} as the newest "
                f"programs: {summary}."
            )
        messagebox.showinfo("Imported", message)

    def _remove_selected(self):
        """Header-button version of Remove, acting on whatever row is
        currently selected -- same pattern as gui/xm_files_tab.py's own
        _remove_selected()/_remove_file(). Unlike Import, this is
        selection-driven: any program can be removed, not just the
        newest one (ProgramMemory.remove_program() closes up the gap that
        leaves behind, wherever it is -- see that method's docstring)."""
        if self._memory is None:
            messagebox.showwarning(
                "No Program Memory", "Load or start a memory buffer first."
            )
            return

        programs = self._selected_programs()
        if not programs:
            messagebox.showinfo("No Selection", "Select a program row first.")
            return
        if len(programs) > 1:
            messagebox.showinfo(
                "Select One Program", "Select a single program row to remove."
            )
            return
        self._remove_program(programs[0])

    def _remove_program(self, program):
        """Confirms and removes `program` (ProgramMemory.remove_program()) --
        the write-side counterpart to _export_selected(). Mentions any
        key assignment(s) the program's own label(s) hold up front in the
        confirmation, since removing it clears them (see
        ProgramMemory.remove_program()'s own docstring)."""
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
            logger.warning("Could not remove program %r: %s", program.names_label, e)
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
