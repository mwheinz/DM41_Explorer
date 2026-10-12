"""
Tools > XROM Libraries...: which XROM libraries are active in the current
mode, and in what order (GitHub issue #53).

The mode's built-in library is pinned as the first row: it is in the
calculator, so it is always active. Below it is every other library the
app knows -- those that come with DM41_Explorer, the user's own (in the
folder next to the preferences file), and any the preferences still name
but that no longer exist ("missing", kept until removed) -- in the user's
order, which stands in for the calculator's page order when a name is
looked up (memory/xrom_library.name_search_order()). Ticking a row makes it
active; Up and Down change its priority.

Add... imports a .mod, an hp41uc .txt list or a library .json into the
user's folder (memory/xrom_import.py); Remove deletes a user library's
file, or drops a missing entry. Both take effect on disk at once, since
they are file operations; the arrangement itself is only saved by Save.
The conflict panel shows what the ticked libraries clash on, as it would
be after Save.

The dialog doesn't touch the registry or the preferences file itself: it
hands the arrangement to `on_saved(entries)`, (id, enabled) pairs highest
priority first, and the caller applies it. If a file was added or removed,
`on_saved` is called on Cancel too, with the arrangement as it was, so the
caller re-reads the folder.
"""

import logging
import platform
from tkinter import filedialog, messagebox

import customtkinter as ctk

from gui.contrast import SECONDARY_TEXT, WARNING_TEXT
from gui.dialog_common import build_dialog_button_row, fit_dialog, scaled
from gui.tab_common import MONOSPACE_FONT_FAMILY, ui_font
from memory.xrom_import import (
    convert_file,
    save_user_library,
    unique_id,
    user_library_paths,
)
from memory.xrom_library import (
    WARNING,
    XromLibraryError,
    active_set,
    builtin_library,
    find_conflicts,
    load_libraries,
    xrom_label,
)

logger = logging.getLogger(__name__)

PLATFORM_SYSTEM = platform.system()

# The size the dialog opens at, at the default font size.
DIALOG_WIDTH = 620
LIST_HEIGHT = 260

FILE_TYPES = [
    ("XROM libraries and modules", "*.mod *.MOD *.json *.txt"),
    ("Module images", "*.mod *.MOD"),
    ("hp41uc XROM lists", "*.txt"),
    ("XROM library files", "*.json"),
]


class _Row:
    """One library in the list: its id, the library (None if missing) and
    whether it is ticked."""

    def __init__(self, library_id, library, enabled):
        self.id = library_id
        self.library = library
        self.enabled = enabled


def describe(library) -> str:
    """ "XROM 22, 24 · comes with DM41_Explorer" -- a row's second line."""
    numbers = ", ".join(str(n) for n in library.module_numbers)
    origin = "comes with DM41_Explorer" if library.shipped else "your library"
    return f"XROM {numbers} · {origin}"


class XromLibrariesDialog(ctk.CTkToplevel):
    """The XROM library manager. `config` is the ProjectConfig (for the
    saved arrangement and the user's folder) and `mode` the DeviceMode in
    force; see the module docstring for `on_saved`."""

    def __init__(self, master, config, mode, on_saved):
        super().__init__(master)
        self.title(f"XROM Libraries ({mode.value} Mode)")
        self.transient(master)
        if PLATFORM_SYSTEM == "Darwin" and hasattr(master, "_menubar"):
            self.config(menu=master._menubar)

        self._config = config
        self._mode = mode
        self._on_saved = on_saved
        self._original_entries = config.xrom_library_entries(mode)
        self._files_changed = False
        self._selected = None
        self._load()
        self._rows = self._initial_rows()

        intro = (
            f"Libraries ticked here name the XROM functions of modules in "
            f"{mode.value} mode. Higher in the list is found first when a "
            "name is looked up, as the calculator searches its pages."
        )
        if not mode.profile.loads_modules:
            intro += (
                f"\n\nA {mode.value} cannot load modules: a library ticked "
                "here only names the XROMs in programs and keys."
            )
        self._intro = ctk.CTkLabel(
            self, text=intro, justify="left", wraplength=scaled(DIALOG_WIDTH - 40)
        )
        self._intro.pack(padx=16, pady=(16, 8), anchor="w")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(padx=16, fill="both", expand=True)
        self._list = ctk.CTkScrollableFrame(body, height=scaled(LIST_HEIGHT))
        self._list.pack(side="left", fill="both", expand=True)
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(side="left", fill="y", padx=(8, 0))
        self._buttons = {}
        for text, command in (
            ("Up", lambda: self._move(-1)),
            ("Down", lambda: self._move(1)),
            ("Add...", self._add),
            ("Remove", self._remove),
            ("Details", self._details),
        ):
            button = ctk.CTkButton(
                buttons, text=text, width=scaled(90), command=command
            )
            button.pack(pady=(0, 6))
            self._buttons[text] = button

        ctk.CTkLabel(self, text="Conflicts:", font=ui_font(weight="bold")).pack(
            padx=16, pady=(12, 2), anchor="w"
        )
        self._conflicts = ctk.CTkTextbox(self, height=scaled(130), wrap="word")
        self._conflicts.pack(padx=16, fill="x")

        build_dialog_button_row(
            self,
            primary_text="Save",
            on_primary=self._save,
            on_cancel=self._cancel,
            pack_kwargs={"padx": 16, "pady": (12, 16), "fill": "x"},
        )
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _event: self._cancel())

        self._refresh()
        fit_dialog(self, DIALOG_WIDTH)
        self.grab_set()

    # -- The libraries ------------------------------------------------------

    def _load(self):
        """Reads every library from disk: the shipped ones and the user's."""
        paths = user_library_paths(self._config.xrom_directory)
        self._libraries, self._load_errors = load_libraries(user_paths=paths)
        self._builtin = builtin_library(self._libraries, self._mode.value)
        for error in self._load_errors:
            logger.warning("XROM library not loaded: %s", error)

    def _pluggable(self):
        """The libraries a row can offer: not a built-in one, whichever
        model it is built into -- the other model's ROMs are not a module
        anyone plugs in."""
        return [lib for lib in self._libraries if not lib.builtin_on]

    def _initial_rows(self):
        """The saved arrangement first, in its order, then every other
        library unticked, by title."""
        by_id = {lib.id: lib for lib in self._libraries}
        builtin_id = self._builtin.id if self._builtin else None
        rows, seen = [], {builtin_id}
        for library_id, enabled in self._original_entries:
            if library_id in seen:
                continue
            seen.add(library_id)
            rows.append(_Row(library_id, by_id.get(library_id), enabled))
        others = [lib for lib in self._pluggable() if lib.id not in seen]
        for library in sorted(others, key=lambda lib: lib.title.lower()):
            rows.append(_Row(library.id, library, False))
        return rows

    def _relink(self):
        """After the folder changed: point each row at its library again
        (a removed one becomes missing) and add any new library, ticked,
        at the end -- the user has just imported it to use it."""
        by_id = {lib.id: lib for lib in self._libraries}
        for row in self._rows:
            row.library = by_id.get(row.id)
        known = {row.id for row in self._rows}
        if self._builtin:
            known.add(self._builtin.id)
        for library in self._pluggable():
            if library.id not in known:
                self._rows.append(_Row(library.id, library, True))

    def entries(self):
        """The arrangement as it stands: (id, enabled), highest priority
        first."""
        return [(row.id, row.enabled) for row in self._rows]

    # -- Drawing --------------------------------------------------------------

    def _refresh(self):
        for child in self._list.winfo_children():
            child.destroy()
        if self._builtin:
            self._add_row_widgets(None, self._builtin.title, "built in · always active")
        for index, row in enumerate(self._rows):
            if row.library is None:
                title, detail = row.id, "missing: no library with this id"
            else:
                title, detail = row.library.title, describe(row.library)
            self._add_row_widgets(index, title, detail)
        self._update_buttons()
        self._show_conflicts()

    def _add_row_widgets(self, index, title, detail):
        """One row: a checkbox and a second line. `index` is None for the
        pinned built-in row, which can't be unticked, moved or selected."""
        selected = index is not None and index == self._selected
        frame = ctk.CTkFrame(
            self._list,
            fg_color=("gray80", "gray28") if selected else "transparent",
            corner_radius=6,
        )
        frame.pack(fill="x", pady=1)
        box = ctk.CTkCheckBox(frame, text=title)
        box.pack(anchor="w", padx=6, pady=(4, 0))
        if index is None:
            box.select()
            box.configure(state="disabled")
        else:
            row = self._rows[index]
            if row.enabled:
                box.select()
            box.configure(command=lambda i=index: self._toggle(i))
        note = ctk.CTkLabel(
            frame,
            text=detail,
            font=ui_font(-2),
            text_color=WARNING_TEXT if detail.startswith("missing") else SECONDARY_TEXT,
        )
        note.pack(anchor="w", padx=34, pady=(0, 4))
        if index is not None:
            for widget in (frame, note):
                widget.bind("<Button-1>", lambda _e, i=index: self._select(i))

    def _update_buttons(self):
        row = self._selected_row()
        states = {
            "Up": row is not None and self._selected > 0,
            "Down": row is not None and self._selected < len(self._rows) - 1,
            "Add...": True,
            "Remove": row is not None
            and (row.library is None or not row.library.shipped),
            "Details": row is not None and row.library is not None,
        }
        for text, enabled in states.items():
            self._buttons[text].configure(state="normal" if enabled else "disabled")

    def _show_conflicts(self):
        """What the ticked libraries clash on, warnings first, plus any
        file that couldn't be loaded."""
        enabled = [row.id for row in self._rows if row.enabled]
        active = active_set(self._libraries, self._mode.value, enabled)
        lines = [f"Not loaded: {error}" for error in self._load_errors]
        findings = find_conflicts(active.libraries)
        lines += [f"Warning: {f}" for f in findings if f.level == WARNING]
        lines += [str(f) for f in findings if f.level != WARNING]
        if active.missing:
            lines.append("Ticked but missing: " + ", ".join(active.missing))
        text = "\n\n".join(lines) or "No conflicts."
        self._conflicts.configure(state="normal")
        self._conflicts.delete("1.0", "end")
        self._conflicts.insert("1.0", text)
        self._conflicts.configure(state="disabled")

    # -- Actions --------------------------------------------------------------

    def _selected_row(self):
        if self._selected is None or not 0 <= self._selected < len(self._rows):
            return None
        return self._rows[self._selected]

    def _select(self, index):
        self._selected = index
        self._refresh()

    def _toggle(self, index):
        self._rows[index].enabled = not self._rows[index].enabled
        self._selected = index
        self._refresh()

    def _move(self, step):
        index = self._selected
        if index is None or not 0 <= index + step < len(self._rows):
            return
        rows = self._rows
        rows[index], rows[index + step] = rows[index + step], rows[index]
        self._selected = index + step
        self._refresh()

    def _add(self):
        path = filedialog.askopenfilename(
            parent=self, title="Add XROM Library", filetypes=FILE_TYPES
        )
        if path:
            self.import_file(path)

    def import_file(self, path):
        """Converts `path` into a library in the user's folder, settling an
        id that is already taken, and lists it, ticked. Returns the new
        library's id, or None if nothing was added."""
        try:
            data, warnings = convert_file(path)
        except XromLibraryError as e:
            messagebox.showerror("Cannot Add Library", str(e), parent=self)
            return None
        notes = list(warnings)
        if data.get("builtin_on"):
            data["builtin_on"] = []
            notes.append(
                "It said it was built into a calculator; as a library you "
                "add, it is a plug-in module instead."
            )
        taken = {lib.id for lib in self._libraries} | {row.id for row in self._rows}
        wanted = data["id"]
        clash = next((lib for lib in self._libraries if lib.id == wanted), None)
        if clash is not None and (clash.shipped or clash.builtin_on):
            data["id"] = unique_id(wanted, taken)
            notes.append(
                f'"{wanted}" is the id of {clash.title}, which comes with '
                f'DM41_Explorer, so this library was added as "{data["id"]}".'
            )
        elif clash is not None:
            answer = messagebox.askyesnocancel(
                "Library Already Added",
                f'You already have a library with the id "{wanted}" '
                f"({clash.title}).\n\nReplace it with this one? Choose No to "
                "add this one as a separate library.",
                parent=self,
            )
            if answer is None:
                return None
            if not answer:
                data["id"] = unique_id(wanted, taken)
        try:
            save_user_library(data, self._config.xrom_directory)
        except OSError as e:
            messagebox.showerror(
                "Cannot Add Library",
                f"The library could not be saved in "
                f"{self._config.xrom_directory}: {e.strerror}",
                parent=self,
            )
            return None
        logger.info("Imported XROM library %s from %s", data["id"], path)
        self._files_changed = True
        self._load()
        self._relink()
        self._selected = next(
            (i for i, row in enumerate(self._rows) if row.id == data["id"]), None
        )
        self._refresh()
        if notes:
            messagebox.showinfo(
                "Library Added",
                f"Added {data['title']}.\n\n" + "\n\n".join(notes),
                parent=self,
            )
        return data["id"]

    def _remove(self):
        row = self._selected_row()
        if row is None:
            return
        if row.library is not None:
            if row.library.shipped:
                return
            path = row.library.path
            if not messagebox.askyesno(
                "Remove Library",
                f"Remove {row.library.title}?\n\nIts file, {path.name}, will "
                f"be deleted from {path.parent}.",
                parent=self,
            ):
                return
            try:
                path.unlink()
            except OSError as e:
                messagebox.showerror(
                    "Cannot Remove Library",
                    f"{path.name} could not be deleted: {e.strerror}",
                    parent=self,
                )
                return
            logger.info("Removed XROM library %s (%s)", row.id, path)
            self._files_changed = True
            self._load()
        del self._rows[self._selected]
        self._relink()
        self._selected = None
        self._refresh()

    def _details(self):
        """Opens the selected library's details; returns the window."""
        row = self._selected_row()
        if row is not None and row.library is not None:
            return LibraryDetailsDialog(self, row.library)
        return None

    def _save(self):
        self._on_saved(self.entries())
        self.destroy()

    def _cancel(self):
        if self._files_changed:
            self._on_saved(self._original_entries)
        self.destroy()


class LibraryDetailsDialog(ctk.CTkToplevel):
    """A library's details and every function in it, by number."""

    def __init__(self, master, library):
        super().__init__(master)
        self.title(library.title)
        self.transient(master)
        if PLATFORM_SYSTEM == "Darwin" and hasattr(master.master, "_menubar"):
            self.config(menu=master.master._menubar)

        lines = [library.title, ""]
        for label, value in (
            ("Id", library.id),
            ("Version", library.version),
            ("Part number", library.part_number),
            ("Author", library.author),
            ("Source", library.source),
            ("File", str(library.path) if library.path else None),
        ):
            if value:
                lines.append(f"{label}: {value}")
        lines.append("")
        for function in library.functions():
            kind = "section header" if function.header else function.type or ""
            lines.append(
                f"{xrom_label(function.module, function.function)}  "
                f"{function.name:<12} {kind}".rstrip()
            )
        text = ctk.CTkTextbox(
            self,
            width=scaled(460),
            height=scaled(420),
            font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
        )
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")
        text.pack(padx=16, pady=(16, 8), fill="both", expand=True)
        ctk.CTkButton(self, text="Close", width=scaled(90), command=self.destroy).pack(
            pady=(0, 16)
        )
        self.bind("<Escape>", lambda _event: self.destroy())
        fit_dialog(self, 500, 520, content=False)
