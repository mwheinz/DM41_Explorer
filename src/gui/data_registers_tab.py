"""
Data Registers tab: view and edit main data memory (R00 through 0x1ff).

Uses a native ttk.Treeview rather than one CustomTkinter widget per cell.
A state can have up to ~319 data registers (if R00 sits right after Key
Assignments at 0xc1); building 4-5 CTk widgets per row for that many rows
took several seconds just to construct, and CTk widgets don't scale to
that count the way a native table does -- see the app's startup-speed
notes (gui/app.py) for the full story. ttk.Treeview also already receives
macOS trackpad scroll events correctly on its own, so it doesn't need the
CTkScrollableFrame workaround in gui/scroll_support.py.
"""

import logging
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
import customtkinter as ctk

from memory import (
    Memory,
    Register,
    format_data_line,
    parse_data_line,
)
from gui.register_edit_dialog import RegisterEditDialog
from gui.register_range_dialog import RegisterRangeDialog, RegisterImportLocationDialog
from gui.tab_common import (
    build_tab_header,
    build_tree_with_scrollbar,
    style_treeview,
    apply_row_tags,
    clear_selected_row_tag,
    read_text_file_via_dialog,
)

logger = logging.getLogger(__name__)

_TREE_COLUMNS = [
    ("reg", "Reg", 55, False),
    ("addr", "Addr", 65, False),
    ("hex", "Hex", 170, False),
    ("value", "Value", 170, True),
]


class DataRegistersTab(ctk.CTkFrame):
    """Renders the main-data-memory register table for a Memory object.
    Call `render(memory)` whenever the buffer changes."""

    def __init__(self, master, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self._memory: Memory = None
        self._on_change = on_change

        header, self._header_label = build_tab_header(
            self,
            button_kwargs={
                "text": "Edit...",
                "width": 130,
                "command": self._edit_selected,
            },
        )
        ctk.CTkButton(
            header,
            text="Export...",
            width=90,
            command=self._export_registers,
        ).pack(side="right", padx=(0, 8))
        ctk.CTkButton(
            header,
            text="Import...",
            width=90,
            command=self._import_registers,
        ).pack(side="right", padx=(0, 8))

        table_frame = ctk.CTkFrame(self, fg_color="transparent")
        table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._stripe_bg = style_treeview()

        # The register list is split across two side-by-side tables (the
        # first half of registers on the left, the rest on the right)
        # instead of one long single-column list. A single "Value" column
        # doesn't need anywhere near the width a Treeview gives it by
        # default, so one long column wastes horizontal space and forces
        # far more scrolling than necessary; two columns use that leftover
        # width to roughly halve the amount of scrolling instead.
        left_frame = ctk.CTkFrame(table_frame, fg_color="transparent")
        left_frame.pack(side="left", fill="both", expand=True)
        right_frame = ctk.CTkFrame(table_frame, fg_color="transparent")
        right_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))

        self._tree_left, _ = build_tree_with_scrollbar(left_frame, _TREE_COLUMNS)
        self._tree_right, _ = build_tree_with_scrollbar(right_frame, _TREE_COLUMNS)
        self._trees = (self._tree_left, self._tree_right)
        apply_row_tags(self._trees, self._stripe_bg)

        for tree in self._trees:
            tree.bind("<Double-1>", lambda e: self._edit_selected())
            # Keep selection exclusive across both tables -- selecting a
            # row in one should clear whatever was selected in the other,
            # so "Edit Selected" always has exactly one unambiguous target.
            tree.bind(
                "<<TreeviewSelect>>",
                lambda e, t=tree: self._on_tree_selected(t),
            )

    def refresh_theme(self):
        """Re-applies theme-dependent ttk styling/colors after
        ctk.set_appearance_mode() changes elsewhere (e.g. Preferences --
        see gui/app.py's _on_preferences_saved()).

        __init__ used to call gui/tab_common.py's `style_treeview()` once,
        at construction time, and nothing ever re-invoked it on a live
        theme change -- CTk's own theme engine has no hook into ttk (see
        that function's docstring). That's why this tab used to need a
        full restart to follow a dark/light switch. This recomputes
        _stripe_bg and re-applies the "oddrow" tag on both trees, in
        place -- tag_configure updates propagate to already-rendered
        rows automatically, no need to re-render()."""
        self._stripe_bg = style_treeview()
        apply_row_tags(self._trees, self._stripe_bg)

    def _notify_change(self):
        if self._on_change:
            self._on_change()

    def _on_tree_selected(self, selected_tree: ttk.Treeview):
        """Gives the selected row a visible highlight -- GitHub issue #22.

        ttk.Treeview has a built-in "selected" state background (set via
        `style.map()` in gui/tab_common.py's `style_treeview()`), but a
        per-item tag's background overrides it regardless of selection
        state, which is why the highlight never showed: every row here
        carries either the "oddrow" zebra-stripe tag or the default
        (untagged) style, and the tag always won.

        The fix is NOT to give "selectedrow" priority over "oddrow" by
        listing it first -- an earlier version of this fix tried that,
        and it turns out *which* tag wins when two tags on the same item
        both set a background is itself inconsistent across Tk builds
        (confirmed: reliably "selectedrow" on this project's Linux/Xvfb
        dev environment, but reliably "oddrow" -- grey background, barely
        readable white text -- on a real Mac). So this never lets the two
        compete in the first place: while a row is selected it carries
        *only* the "selectedrow" tag (no "oddrow" alongside it), and its
        background matches the "selected" state color from `style.map()`
        above, so it doesn't matter which of those two agreeing sources
        wins either. "oddrow" is restored on deselect by recomputing that
        row's position parity from its current index in the tree
        (gui/tab_common.py's `clear_selected_row_tag()`), rather than
        caching it -- no state to go stale across a render() teardown/
        rebuild. This tab's own version of the fix (unlike
        gui/program_tab.py's/gui/xm_files_tab.py's single-table
        `highlight_selected_row()` use) has to clear the tag across
        *both* side-by-side tables before re-tagging just the one that
        was actually clicked, hence calling `clear_selected_row_tag()`
        directly here instead.
        """
        selection = selected_tree.selection()
        if not selection:
            return

        # Clear "selectedrow" off whichever row currently has it (if
        # any), on either table, restoring it to a *single* plain tag
        # recomputed from its position -- cheaper than tracking it and
        # always correct even right after a render() rebuilt every row.
        for tree in self._trees:
            clear_selected_row_tag(tree)

        # Keep selection exclusive across both tables -- selecting a row
        # in one should clear whatever was selected in the other, so
        # "Edit Selected" always has exactly one unambiguous target.
        for tree in self._trees:
            if tree is not selected_tree:
                tree.selection_remove(*tree.selection())

        new_iid = selection[0]
        selected_tree.item(new_iid, tags=("selectedrow",))

    def render(self, memory: Memory):
        self._memory = memory
        for tree in self._trees:
            tree.delete(*tree.get_children())

        if memory is None:
            self._header_label.configure(text="(no memory state loaded)")
            return

        # The DataMemory region derives its own extent from R00 live, and
        # reports itself empty when the state has no sane R00/.END.
        # partition at all -- so this doesn't need its own R00/MIN_SANE_R00
        # arithmetic or its own defensive try/except around decoding
        # register c.
        data = memory.data_memory
        if data.is_empty:
            self._header_label.configure(
                text="No data registers yet -- start a new buffer or load/read a state first."
            )
            return

        count = data.count
        self._header_label.configure(
            text=(
                f"Data registers: 0x{data.start:03x}-0x{data.end:03x} "
                f"({count} registers, R00-R{count - 1:02d})"
            )
        )

        # First half of the registers goes in the left table, the rest in
        # the right one; the left table gets the extra register when the
        # count is odd.
        split = -(-count // 2)  # ceil(count / 2)

        # Alternating-row shading is tracked per table (not by the global
        # register index `i`) so both tables' top rows start on the same
        # shade regardless of whether `split` happens to be odd or even.
        row_pos = {id(self._tree_left): 0, id(self._tree_right): 0}

        for i, addr in enumerate(data):
            tree = self._tree_left if i < split else self._tree_right
            pos = row_pos[id(tree)]
            row_pos[id(tree)] = pos + 1
            register = memory.get_register(addr)
            tree.insert(
                "",
                "end",
                iid=str(addr),
                values=(
                    f"R{i:02d}",
                    f"0x{addr:03x}",
                    register.get_hex(),
                    str(register),
                ),
                tags=("oddrow",) if pos % 2 else (),
            )

    def _current_range(self):
        """Returns (r00, count) for the currently-displayed data
        registers, or None if there's no memory loaded and no data
        register partition -- the same DataMemory region render() uses,
        shared here so Export/Import don't each re-derive it."""
        if self._memory is None:
            return None
        data = self._memory.data_memory
        if data.is_empty:
            return None
        return data.start, data.count

    def _export_registers(self):
        """Prompts for which sub-range of the currently-displayed data
        registers to export (default: all of them -- GitHub issue #15),
        then writes that range as one DATA-format line each (see
        registers.format_data_line()), in R00..end order -- GitHub issue
        #11."""
        current_range = self._current_range()
        if current_range is None:
            messagebox.showwarning(
                "No Data Registers", "Load or start a memory buffer first."
            )
            return
        r00, count = current_range

        def do_export(start: int, end: int):
            path = filedialog.asksaveasfilename(
                initialfile="registers.txt",
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            )
            if not path:
                return
            export_count = end - start + 1
            try:
                lines = [
                    format_data_line(self._memory.get_register(addr))
                    for addr in range(r00 + start, r00 + end + 1)
                ]
                Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")
            except (OSError, UnicodeEncodeError) as e:
                logger.warning("Could not export data registers to %s: %s", path, e)
                messagebox.showerror("Could Not Export", str(e))
                return
            logger.info(
                "Exported %d data registers (R%02d-R%02d) to %s",
                export_count,
                start,
                end,
                path,
            )
            messagebox.showinfo(
                "Exported",
                f"{export_count} data register(s) (R{start:02d}-R{end:02d}) "
                f"written to {path}",
            )

        RegisterRangeDialog(self, count, do_export)

    def _import_registers(self):
        """Reads a DATA-format file (see registers.parse_data_line()) and
        prompts for which currently-displayed register the file's data
        should start overwriting from -- GitHub issue #14. The file no
        longer has to cover every displayed register; it just has to fit
        starting from the chosen location, and this does not resize main
        memory (see GitHub issue #11 for the original all-registers-only
        behavior this replaces)."""
        current_range = self._current_range()
        if current_range is None:
            messagebox.showwarning(
                "No Data Registers", "Load or start a memory buffer first."
            )
            return
        r00, count = current_range

        path, content = read_text_file_via_dialog("register import", logger)
        if path is None:
            return

        # Every line matters here -- each maps to one specific register
        # address -- so, unlike the XM file dialogs, blank lines aren't
        # silently skipped; a blank line just fails to parse like any
        # other bad line would.
        lines = content.rstrip("\n").split("\n") if content.strip("\n") else []
        if not lines:
            messagebox.showerror(
                "Empty File", f"{path} has no register data to import."
            )
            return

        new_registers = []
        for i, line in enumerate(lines, start=1):
            try:
                new_registers.append(parse_data_line(line))
            except ValueError as e:
                logger.error("Could not import %s: line %d (%r): %s", path, i, line, e)
                messagebox.showerror("Invalid Content", f"Line {i} ({line!r}): {e}")
                return

        import_count = len(new_registers)

        def do_import(start: int):
            end = start + import_count - 1
            if not messagebox.askyesno(
                "Import Data Registers",
                f"Overwrite {import_count} data register(s) "
                f"(R{start:02d}-R{end:02d}) with the contents of {path}?",
            ):
                return

            for addr, reg in zip(range(r00 + start, r00 + end + 1), new_registers):
                self._memory.set_register(addr, reg)
            logger.info(
                "Imported %d data registers (R%02d-R%02d) from %s",
                import_count,
                start,
                end,
                path,
            )
            self._notify_change()
            self.render(self._memory)

        RegisterImportLocationDialog(self, count, import_count, do_import)

    def _selected_addr(self):
        for tree in self._trees:
            selection = tree.selection()
            if selection:
                return int(selection[0]), tree
        return None, None

    def _edit_selected(self):
        if self._memory is None:
            return
        addr, _ = self._selected_addr()
        if addr is None:
            messagebox.showinfo("No Selection", "Select a register row first.")
            return
        self._edit_register(addr)

    def _edit_register(self, addr: int):
        register = self._memory.get_register(addr)

        def save(new_register: Register):
            self._memory.set_register(addr, new_register)
            logger.info("Register 0x%03x edited: %s", addr, new_register)
            self._notify_change()
            self.render(self._memory)
            tree = (
                self._tree_left
                if str(addr) in self._tree_left.get_children()
                else self._tree_right
            )
            tree.selection_set(str(addr))
            tree.see(str(addr))

        RegisterEditDialog(self, addr, register, save)
