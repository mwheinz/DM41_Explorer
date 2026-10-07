"""
Modal dialog for adding or editing a Data or ASCII extended-memory file.

Program-type XM files can be viewed and removed in the XM Files tab, but
not created or edited here -- there's still real research to do on program
storage before it's safe to write program bytes from this tool.
"""

import logging
import platform
from tkinter import messagebox
import customtkinter as ctk

from memory import (
    ExtendedMemory,
    parse_data_line,
    decode_trigraphs,
    NAME_MIN_CHAR,
    NAME_MAX_CHAR,
)
from gui.dialog_common import build_dialog_button_row, scaled
from gui.tab_common import MONOSPACE_FONT_FAMILY

logger = logging.getLogger(__name__)

PLATFORM_SYSTEM = platform.system()


class XMFileDialog(ctk.CTkToplevel):
    """
    Blocking modal dialog to add a new Data/ASCII XM file, or edit an
    existing one's name/content. `on_save(name, file_type, kwargs)` is
    called with arguments suitable for `ExtendedMemory.add_file()` if the
    user saves.

    `existing`, if given, is an XMFile already in memory -- its content is
    used as the default, and its type is locked (changing a file's type in
    place isn't meaningful). `initial`, if given instead, is a plain dict
    ({"name":, "file_type": "Data"|"ASCII", "content": "line1\\nline2..."})
    used to prefill a *new* file (e.g. from Import File... -- see
    XMFilesTab._import_file()); unlike `existing`, the type stays editable
    since an imported file's intended type isn't known for certain. At
    most one of `existing`/`initial` should be given.
    """

    def __init__(self, master, on_save, *, existing=None, initial=None):
        super().__init__(master)
        self._on_save = on_save
        self._editing = existing is not None

        self.title("Edit XM File" if self._editing else "Add XM File")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        if PLATFORM_SYSTEM == "Darwin" and hasattr(master, "_menubar"):
            self.config(menu=master._menubar)

        ctk.CTkLabel(
            self, text="Name (1-7 characters, ASCII 32-101, no trigraphs):"
        ).pack(anchor="w", padx=16, pady=(16, 4))
        if existing:
            name_default = existing.name.rstrip()
        elif initial:
            name_default = initial.get("name", "")
        else:
            name_default = ""
        self._name_var = ctk.StringVar(value=name_default)
        ctk.CTkEntry(self, textvariable=self._name_var, width=scaled(320)).pack(
            anchor="w", padx=16
        )

        ctk.CTkLabel(self, text="Type:").pack(anchor="w", padx=16, pady=(12, 4))
        if existing:
            type_default = (
                "ASCII" if existing.file_type == ExtendedMemory.TYPE_ASCII else "Data"
            )
        elif initial:
            type_default = initial.get("file_type", "Data")
        else:
            type_default = "Data"
        self._type_var = ctk.StringVar(value=type_default)
        type_menu = ctk.CTkOptionMenu(
            self,
            values=["Data", "ASCII"],
            variable=self._type_var,
            command=self._on_type_changed,
        )
        type_menu.pack(anchor="w", padx=16, fill="x")
        if self._editing:
            # Changing a file's type in place isn't meaningful -- content
            # format is type-specific -- so lock it while editing. An
            # imported-but-not-yet-added file (`initial`) keeps it
            # editable, since its intended type is just a guess.
            type_menu.configure(state="disabled")

        self._data_label = ctk.CTkLabel(
            self,
            text=(
                "Data (one value per line: a number, 1-6 characters of "
                "text (trigraphs allowed -- see docs/trigraphs.md), or "
                "0x + 14 hex digits for raw content):"
            ),
            justify="left",
            wraplength=scaled(320),
        )
        self._ascii_label = ctk.CTkLabel(
            self,
            text=(
                "Records (one per line, 1-254 characters each -- "
                "trigraphs allowed, see docs/trigraphs.md):"
            ),
            justify="left",
            wraplength=scaled(320),
        )

        # For `initial` (the Import File... flow), both boxes are
        # pre-filled with the *same* raw lines, regardless of which type
        # was guessed -- see XMFilesTab._import_file()/_guess_file_type().
        # Otherwise switching the type dropdown after import would silently
        # empty out whichever box wasn't showing (real bug: a text file
        # guessed/left as "Data" would show a normal-looking Data editor,
        # but switching to ASCII to fix it wiped the imported content,
        # since only the box matching the *original* guess ever got the
        # text). `existing` (editing a real file already in memory) keeps
        # the old single-box behavior -- its type is locked, so there's
        # nothing to switch to anyway.
        data_default = ""
        if existing and existing.file_type == ExtendedMemory.TYPE_DATA:
            data_default = "\n".join(existing.get_data_lines())
        elif initial:
            data_default = initial.get("content", "")
        self._data_box = ctk.CTkTextbox(
            self, width=scaled(320), height=scaled(140), font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY)
        )
        if data_default:
            self._data_box.insert("1.0", data_default)

        ascii_default = ""
        if existing and existing.file_type == ExtendedMemory.TYPE_ASCII:
            ascii_default = "\n".join(existing.get_records())
        elif initial:
            ascii_default = initial.get("content", "")
        self._ascii_box = ctk.CTkTextbox(
            self, width=scaled(320), height=scaled(140), font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY)
        )
        if ascii_default:
            self._ascii_box.insert("1.0", ascii_default)

        self._content_row = 6
        self._on_type_changed(self._type_var.get())

        build_dialog_button_row(
            self,
            primary_text="Save",
            on_primary=self._on_save_clicked,
            pack_kwargs={"padx": 16, "pady": (12, 16), "fill": "x", "side": "bottom"},
        )

    def _on_type_changed(self, value):
        self._data_label.pack_forget()
        self._data_box.pack_forget()
        self._ascii_label.pack_forget()
        self._ascii_box.pack_forget()
        if value == "Data":
            self._data_label.pack(anchor="w", padx=16, pady=(12, 4))
            self._data_box.pack(anchor="w", padx=16, fill="both", expand=True)
        else:
            self._ascii_label.pack(anchor="w", padx=16, pady=(12, 4))
            self._ascii_box.pack(anchor="w", padx=16, fill="both", expand=True)

    def _on_save_clicked(self):
        name = self._name_var.get().strip()
        if not name or len(name) > 7:
            logger.warning("Invalid XM file name %r: must be 1-7 characters.", name)
            messagebox.showerror("Invalid Name", "File name must be 1-7 characters.")
            return
        for ch in name:
            if not NAME_MIN_CHAR <= ord(ch) <= NAME_MAX_CHAR:
                logger.warning(
                    "Invalid XM file name %r: %r (code %d) outside %d-%d.",
                    name,
                    ch,
                    ord(ch),
                    NAME_MIN_CHAR,
                    NAME_MAX_CHAR,
                )
                messagebox.showerror(
                    "Invalid Name",
                    f"File name contains {ch!r} (code {ord(ch)}), outside "
                    f"the allowed range {NAME_MIN_CHAR}-{NAME_MAX_CHAR} "
                    "(ASCII space through lowercase 'e'). Unlike file "
                    "content, names don't support trigraphs.",
                )
                return

        file_type_str = self._type_var.get()
        try:
            if file_type_str == "Data":
                raw = self._data_box.get("1.0", "end").rstrip("\n")
                lines = [line for line in raw.split("\n") if line != ""]
                if not lines:
                    raise ValueError("Enter at least one line.")
                # Validate every line up front (with its 1-based line
                # number in the error) rather than letting add_file()'s
                # own parse_data_line() call raise first -- that would
                # report a valid-sounding error with no indication of
                # *which* line was wrong.
                for i, line in enumerate(lines, start=1):
                    try:
                        parse_data_line(line)
                    except ValueError as e:
                        raise ValueError(f"Line {i} ({line!r}): {e}") from e
                self._on_save(name, ExtendedMemory.TYPE_DATA, {"data_lines": lines})
            else:
                raw = self._ascii_box.get("1.0", "end").rstrip("\n")
                records = [line for line in raw.split("\n") if line != ""]
                if not records:
                    raise ValueError("Enter at least one record.")
                for i, r in enumerate(records, start=1):
                    try:
                        decoded = decode_trigraphs(r)
                    except ValueError as e:
                        raise ValueError(f"Line {i} ({r!r}): {e}") from e
                    if len(decoded) > 254:
                        raise ValueError(
                            f"Line {i} ({r[:20]!r}...): decodes to "
                            f"{len(decoded)} characters, longer than 254."
                        )
                self._on_save(name, ExtendedMemory.TYPE_ASCII, {"records": records})
        except (ValueError, UnicodeEncodeError) as e:
            logger.warning("Invalid XM file content for %r: %s", name, e)
            messagebox.showerror("Invalid Content", str(e))
            return

        self.destroy()
