"""
XM Files tab: view, add, edit, remove, import, and export extended-memory
files.

Uses a native ttk.Treeview rather than one CustomTkinter widget per row --
the same performance fix as gui/data_registers_tab.py (see that module's
docstring for the full story): building 5-8 CTk widgets per row (one per
column, plus a per-row Export/Edit/Remove button each) got noticeably slow
once a state had more than a few dozen XM files, and CTk widgets don't scale
to that count the way a native table does -- GitHub issue #21. Per-row
Export/Edit/Remove buttons are gone; those three actions now live in the
header (like Add File/Import File already did) and act on whichever row is
currently selected, matching the pattern gui/data_registers_tab.py already
established for its own "Edit..." button.

ttk.Treeview also already receives macOS trackpad scroll events correctly
on its own (see gui/scroll_support.py's docstring), so this incidentally
should also fix GitHub issue #20 (touchpad scrolling dead in this tab),
which was specific to the CTkScrollableFrame/Canvas this tab used to use.
"""

import logging
from pathlib import Path
from tkinter import filedialog, messagebox
import customtkinter as ctk

from memory import Memory, ExtendedMemory, DM41MemoryError, parse_data_line
from gui.xm_file_dialog import XMFileDialog
from gui.tab_common import (
    DANGER_BUTTON_KWARGS,
    fit_columns,
    build_caption_label,
    build_tab_header,
    build_tab_treeview,
    style_treeview,
    apply_row_tags,
    highlight_selected_row,
    read_text_file_via_dialog,
    clear_tree_for_render,
)

logger = logging.getLogger(__name__)

# File types Export/Edit are meaningful for -- Program files' on-disk
# format isn't part of this feature yet (see XMFileDialog's module
# docstring). Shared between _update_action_buttons() (dynamic
# enable/disable) and _edit_selected()'s own defensive check.
_EDITABLE_FILE_TYPES = (ExtendedMemory.TYPE_DATA, ExtendedMemory.TYPE_ASCII)

# Distinct ttk style name for this tab's Treeview/scrollbar -- see
# gui/tab_common.py's `style_treeview()` docstring for why this tab needs
# its own, separately-named ttk style rather than reusing the plain
# "Treeview" style Data Registers configures.
_TREE_STYLE = "XMFiles.Treeview"

_TREE_COLUMNS = [
    ("name", "Name", 90, False),
    ("type", "Type", 105, False),
    ("header", "Header", 90, False),
    ("registers", "Size", 70, False),
    ("preview", "Preview", 180, True),
]


def _guess_file_type(lines: list) -> str:
    """Best-effort default type for a freshly-imported file's content:
    "Data" only if *every* line actually parses as a valid DATA line (see
    registers.parse_data_line()); "ASCII" otherwise, since ASCII only
    requires 1-254 characters per line -- real text (e.g. anything with a
    line longer than 6 characters that isn't a plain number) almost never
    also happens to be valid DATA content, but the reverse doesn't hold at
    all: a file of short numbers/words is completely ambiguous between the
    two, so this can only ever be a starting guess -- the type stays
    editable in the dialog (see XMFileDialog's `initial=` handling, which
    pre-fills both the Data and ASCII editors with the same lines so
    switching the guess doesn't lose anything)."""
    if not lines:
        return "Data"
    try:
        for line in lines:
            parse_data_line(line)
    except ValueError:
        return "ASCII"
    return "Data"


def _xm_file_to_add_kwargs(f):
    """The (name, file_type, kwargs) ExtendedMemory.add_file() needs to
    recreate Data/ASCII file `f` exactly as it currently is -- used by
    _save_new_or_edited_file() to restore a file it had to remove before
    discovering the replacement was invalid. Only covers the two types
    _EDITABLE_FILE_TYPES allows editing in the first place (Program files
    never reach this path)."""
    name = f.name.rstrip()
    if f.file_type == ExtendedMemory.TYPE_DATA:
        return name, f.file_type, {"data_lines": f.get_data_lines()}
    return name, f.file_type, {"records": f.get_records()}


class XMFilesTab(ctk.CTkFrame):
    """Renders the extended-memory file list for a Memory object. Call
    `render(memory)` whenever the buffer changes."""

    def __init__(self, master, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self._memory: Memory = None
        self._on_change = on_change

        header, self._header_label = build_tab_header(
            self,
            button_kwargs={"text": "Add File", "width": 100, "command": self._add_file},
        )
        ctk.CTkButton(
            header, text="Import File", width=110, command=self._import_file
        ).pack(side="right", padx=(0, 8))
        export_button = ctk.CTkButton(
            header, text="Export...", width=90, command=self._export_selected
        )
        export_button.pack(side="right", padx=(0, 8))
        edit_button = ctk.CTkButton(
            header, text="Edit", width=90, command=self._edit_selected
        )
        edit_button.pack(side="right", padx=(0, 8))
        remove_button = ctk.CTkButton(
            header,
            text="Remove",
            width=90,
            **DANGER_BUTTON_KWARGS,
            command=self._remove_selected,
        )
        remove_button.pack(side="right", padx=(0, 8))

        self._caption = build_caption_label(
            self,
            "Each row indicates a separate XM file. "
            "Files are listed in the order they occur in extended memory. "
            "Data and ASCII files can be imported, exported, or edited "
            "right in DM41 Explorer but Program files cannot be altered "
            "at this time.",
        )
        _, self._tree = build_tab_treeview(self, _TREE_COLUMNS, style=_TREE_STYLE)

        self._tree.bind("<Double-1>", lambda e: self._edit_selected())
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_selected)

        # Closures over the three header buttons -- see
        # _update_action_buttons()'s docstring for why this is a single
        # stored callable rather than three separate self._export_button /
        # self._edit_button / self._remove_button instance attributes
        # (this class is already at the repo's own pylint
        # max-attributes=7 ceiling; see data_registers_tab.py's own
        # GitHub issue #22 fix for the same constraint).
        def update_action_buttons(selected_file):
            can_edit = (
                selected_file is not None
                and selected_file.file_type in _EDITABLE_FILE_TYPES
            )
            export_button.configure(state="normal" if can_edit else "disabled")
            edit_button.configure(state="normal" if can_edit else "disabled")
            remove_button.configure(
                state="normal" if selected_file is not None else "disabled"
            )

        self._update_action_buttons = update_action_buttons
        self._update_action_buttons(None)

    def refresh_theme(self):
        """Re-applies theme-dependent ttk styling/colors after
        ctk.set_appearance_mode() changes elsewhere (e.g. Preferences) --
        see gui/data_registers_tab.py's identical method for why this is
        needed at all (CustomTkinter's theme engine has no hook into
        ttk)."""
        self._stripe_bg = style_treeview(_TREE_STYLE)
        apply_row_tags(self._tree, self._stripe_bg)

    def _notify_change(self):
        if self._on_change:
            self._on_change()

    def _xm(self) -> ExtendedMemory:
        return self._memory.extended_memory

    def _on_tree_selected(self, event=None):  # pylint: disable=unused-argument
        """Gives the selected row a visible highlight, the same way (and
        for the same reason) as gui/tab_common.py's
        `highlight_selected_row()` docstring (GitHub issue #22) explains.
        This tab only has one table (not two side by side like Data
        Registers), so there's no cross-tree exclusivity to manage --
        just this table's own selection."""
        highlight_selected_row(self._tree)
        self._update_action_buttons(self._selected_file())

    def _selected_header_addr(self):
        selection = self._tree.selection()
        return int(selection[0]) if selection else None

    def _selected_file(self):
        """The XMFile currently selected in the table, re-fetched fresh
        from Memory (not cached) -- same reasoning as the old
        `_edit_file()`'s own lookup-by-header_addr: a file's exact
        attributes can only be trusted as of the last render(), and
        header_addr is this tab's stable per-file identity (also used as
        each row's Treeview iid)."""
        addr = self._selected_header_addr()
        if addr is None or self._memory is None:
            return None
        files = self._xm().list_files()
        return next((f for f in files if f.header_addr == addr), None)

    def render(self, memory: Memory):
        self._memory = memory
        if clear_tree_for_render(self._tree, self._header_label, memory):
            self._update_action_buttons(None)
            return

        try:
            files = self._xm().list_files()
        except DM41MemoryError as e:
            logger.warning("Could not list XM files: %s", e)
            self._header_label.configure(text=f"Could not list XM files: {e}")
            self._update_action_buttons(None)
            return

        self._header_label.configure(text=f"Extended-memory files: {len(files)}")

        for pos, f in enumerate(files):
            #span_note = " (spans regions)" if f.spans_regions else ""
            self._tree.insert(
                "",
                "end",
                iid=str(f.header_addr),
                values=(
                    f.name.rstrip(),
                    f.type_label,
                    f"0x{f.header_addr:03x}",
                    #f"{f.num_registers}{span_note}",
                    f"{f.num_registers}",
                    self._preview_for(f),
                ),
                tags=("oddrow",) if pos % 2 else (),
            )
        fit_columns(self._tree, _TREE_COLUMNS)
        self._update_action_buttons(None)

    @staticmethod
    def _preview_for(f) -> str:
        try:
            if f.file_type == ExtendedMemory.TYPE_DATA:
                lines = f.get_data_lines()
                text = ", ".join(lines[:6])
                if len(lines) > 6:
                    text += f", ... ({len(lines)} total)"
                return text
            if f.file_type == ExtendedMemory.TYPE_ASCII:
                records = f.get_records()
                text = ", ".join(repr(r) for r in records[:4])
                if len(records) > 4:
                    text += f", ... ({len(records)} total)"
                return text
            if f.file_type == ExtendedMemory.TYPE_PROGRAM:
                checksum = f.checksum_valid
                status = (
                    "valid"
                    if checksum
                    else "INVALID" if checksum is False else "unknown"
                )
                return f"{f.byte_length} instruction bytes, checksum {status}"
            if f.is_other_type:
                return f"type {f.file_type}: {f.num_registers} registers (not decoded)"
        except Exception as e:
            # Expected for a file whose content doesn't fit the shape its
            # own type nibble claims (e.g. a corrupt or hand-edited state)
            # -- shown inline in the Preview column rather than a popup,
            # so this is a DEBUG detail, not a WARNING-worthy event.
            logger.debug("Could not decode preview for XM file: %s", e)
            return f"(could not decode: {e})"
        return ""

    # -- Add / Edit / Remove ------------------------------------------------

    def _save_new_or_edited_file(
        self, name, file_type, kwargs, *, replacing_addr=None, replacing_file=None
    ):
        """Shared save path for Add, Import, and Edit -- all three end up
        calling ExtendedMemory.add_file() with the same kind of kwargs
        (see XMFileDialog); Edit additionally removes the file being
        replaced first (see _edit_file()).

        `replacing_file` (the pre-edit XMFile, when this is an edit) is
        used only to roll back a rejected replacement below -- the
        remove-then-add order itself is required, not just historical:
        add_file()'s duplicate-name check only allows editing a file
        under its own unchanged name because the old entry is already
        gone by the time that check runs (see add_file()'s docstring).
        So a bad edit (an over-length line, a name that fails validation,
        no room left, ...) leaves the old file already removed when
        add_file() raises; without restoring it here, the buffer would be
        missing a file the user never asked to remove, while this tab's
        Treeview still showed its old (now-stale) row and header_addr.

        The restore content is captured *before* anything is touched
        below, not read lazily from `replacing_file` inside the except
        block: XMFile.get_data_lines()/get_records() re-read live
        registers at `replacing_file.segments` on every call, and
        remove_file()'s rebuild overwrites exactly those addresses with
        whatever files remain -- reading them after the fact would
        silently "restore" a neighboring file's content instead."""
        xm = self._xm()
        restore = _xm_file_to_add_kwargs(replacing_file) if replacing_file else None
        try:
            if replacing_addr is not None:
                # Editing is implemented as remove-then-add (see
                # ExtendedMemory.remove_file()'s docstring): there's no
                # in-place content resize, so the edited file is rebuilt
                # fresh and ends up positioned after whatever files
                # remain, rather than keeping its original slot.
                xm.remove_file(replacing_addr)
            xm.add_file(name, file_type, **kwargs)
        except (ValueError, DM41MemoryError) as e:
            verb = "save" if replacing_addr is not None else "add"
            logger.warning("Could not %s XM file %r: %s", verb, name, e)
            if restore is not None:
                try:
                    restore_name, restore_type, restore_kwargs = restore
                    xm.add_file(restore_name, restore_type, **restore_kwargs)
                except (ValueError, DM41MemoryError) as restore_error:
                    logger.error(
                        "Could not restore XM file %r after a failed edit "
                        "-- it has been lost: %s",
                        restore[0],
                        restore_error,
                    )
                # Re-render either way: whatever extended memory now
                # actually contains, the Treeview's rows (keyed by each
                # file's header_addr) must match it before the user can
                # select anything again.
                self.render(self._memory)
            messagebox.showerror(f"Could Not {verb.title()} File", str(e))
            return
        logger.info(
            "XM file %s: %r (%s)",
            "edited" if replacing_addr is not None else "added",
            name,
            file_type,
        )
        self._notify_change()
        self.render(self._memory)

    def _add_file(self):
        if self._memory is None:
            messagebox.showwarning(
                "No Memory Loaded", "Load or start a memory buffer first."
            )
            return

        XMFileDialog(self, self._save_new_or_edited_file)

    def _import_file(self):
        """Reads a plain-text file from disk and opens it pre-filled in
        the Add dialog (Data or ASCII, per the DATA/ASCII line formats --
        see registers.parse_data_line()) so the user can review/adjust the
        name and type before it's actually added. GitHub issue #11."""
        if self._memory is None:
            messagebox.showwarning(
                "No Memory Loaded", "Load or start a memory buffer first."
            )
            return

        path, content = read_text_file_via_dialog("XM file import", logger)
        if path is None:
            return

        default_name = Path(path).stem[:7] or "IMPORT"
        stripped_content = content.rstrip("\n")
        guessed_type = _guess_file_type(
            [line for line in stripped_content.split("\n") if line != ""]
        )
        # The guessed type just picks which editor is shown first --
        # XMFileDialog pre-fills *both* the Data and ASCII boxes with this
        # same content when given `initial=`, so switching the type
        # dropdown afterward re-labels the same lines rather than clearing
        # whichever box wasn't showing.
        XMFileDialog(
            self,
            self._save_new_or_edited_file,
            initial={
                "name": default_name,
                "file_type": guessed_type,
                "content": stripped_content,
            },
        )

    def _export_file(self, f):
        default_name = f"{f.name.rstrip()}.txt"
        path = filedialog.asksaveasfilename(
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            if f.file_type == ExtendedMemory.TYPE_DATA:
                lines = f.get_data_lines()
            elif f.file_type == ExtendedMemory.TYPE_ASCII:
                lines = f.get_records()
            else:
                raise ValueError(f"Export isn't supported for {f.type_label} files.")
            Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")
        except (OSError, ValueError, UnicodeEncodeError) as e:
            logger.warning("Could not export XM file %r to %s: %s", f.name, path, e)
            messagebox.showerror("Could Not Export File", str(e))
            return
        logger.info("XM file %r exported to %s", f.name, path)
        messagebox.showinfo("Exported", f"{f.name.rstrip()!r} written to {path}")

    def _edit_file(self, header_addr: int):
        xm = self._xm()
        files = xm.list_files()
        existing = next((f for f in files if f.header_addr == header_addr), None)
        if existing is None:
            logger.warning(
                "Edit requested for XM file at 0x%03x, but it no longer exists.",
                header_addr,
            )
            messagebox.showerror("Not Found", "That file no longer exists.")
            self.render(self._memory)
            return

        def save(name, file_type, kwargs):
            self._save_new_or_edited_file(
                name,
                file_type,
                kwargs,
                replacing_addr=header_addr,
                replacing_file=existing,
            )

        XMFileDialog(self, save, existing=existing)

    def _remove_file(self, header_addr: int, name: str):
        if not messagebox.askyesno(
            "Remove XM File", f"Remove {name.rstrip()!r} from extended memory?"
        ):
            return
        try:
            self._xm().remove_file(header_addr)
        except DM41MemoryError as e:
            logger.warning("Could not remove XM file %r: %s", name, e)
            messagebox.showerror("Could Not Remove File", str(e))
            return
        logger.info("XM file removed: %r", name)
        self._notify_change()
        self.render(self._memory)

    # -- Header-button versions of Edit/Export/Remove, acting on whatever
    # row is currently selected. The buttons themselves are dynamically
    # enabled/disabled by _update_action_buttons() based on the selection
    # (and, for Export/Edit, the selected file's type), so the "No
    # Selection"/"Not Editable" messageboxes below are a defensive
    # fallback -- e.g. double-click (bound to _edit_selected() too)
    # bypasses button state entirely, and a selection can in principle
    # change between a click and this callback running.

    def _edit_selected(self):
        f = self._selected_file()
        if f is None:
            messagebox.showinfo("No Selection", "Select an XM file first.")
            return
        if f.file_type not in _EDITABLE_FILE_TYPES:
            # Not just a nicety: XMFileDialog doesn't itself guard against
            # a Program-type `existing` file -- it would silently show an
            # empty "Data" editor (Program isn't Data or ASCII, so none of
            # its content prefill branches match), and saving that would
            # overwrite the real program with nothing. The disabled Edit
            # button already prevents reaching this for a mouse click, but
            # double-click doesn't check button state, so this method
            # needs its own guard regardless.
            messagebox.showinfo(
                "Not Editable", f"{f.type_label} files can't be edited here yet."
            )
            return
        self._edit_file(f.header_addr)

    def _export_selected(self):
        f = self._selected_file()
        if f is None:
            messagebox.showinfo("No Selection", "Select an XM file first.")
            return
        self._export_file(f)

    def _remove_selected(self):
        f = self._selected_file()
        if f is None:
            messagebox.showinfo("No Selection", "Select an XM file first.")
            return
        self._remove_file(f.header_addr, f.name)
