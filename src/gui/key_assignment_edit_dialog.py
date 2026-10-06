"""
Modal dialog for editing (or clearing) a single key's assignment, from
gui/key_assignments_tab.py -- see that module's docstring for the overall
tab design. This dialog itself never touches the Memory object; it hands
the resolved assignment back to the caller via `on_save`, and signals a
delete via `on_delete`, exactly like RegisterEditDialog.

Three tabs, matching the two independent storage mechanisms docs/
key_assignments.md sec 4.1 describes: "Function" and "Raw Hex" both write
a built-in/peripheral function into the Key Assignment Registers (sec
4.2), while "Program" assigns a user's global label instead (sec 4.6) --
a completely different storage location (the label's own header) with
different constraints (a program can hold only one key assignment at a
time, so picking one here MOVES it if it's already on another key).
Because the caller now has two different kinds of value to act on,
`on_save(kind, value)` is called with `kind` = `"function"` (value: an
int or (byte1, byte2) tuple, same shape as before) or `"program"` (value:
the chosen global label's name).
"""

import logging
import platform
from tkinter import messagebox
import customtkinter as ctk

from gui.dialog_common import build_dialog_button_row
from gui.tab_common import MONOSPACE_FONT_FAMILY
from memory.mnemonics import (
    assignable_display_names,
    is_dm41x_only,
    key_bytes_for,
    resolve,
)

logger = logging.getLogger(__name__)

PLATFORM_SYSTEM = platform.system()

# Shown under the Function box when the chosen function is one the DM41X
# added to the HP-41CX set. Assigning it is allowed on any model's state.
_DM41X_ONLY_HINT = "DM41X only: a DM41L does not have this function."

# Shown under the title when the key is assigned but its key flag is clear,
# as LKAOFF leaves the top two rows of keys. Saving here sets the flag.
_FLAG_CLEAR_NOTE = (
    "This key's flag is clear, so the calculator treats it as unassigned "
    "(as after LKAOFF). Saving an assignment sets the flag."
)

# Every assignable function's HP-41 display name, alphabetically --
# single-byte and XROM/peripheral functions merged into one list, since
# the picker doesn't need to distinguish them; memory/mnemonics.py
# resolves whichever encoding a chosen name actually needs.
_ALL_FUNCTION_NAMES = assignable_display_names()


def _hex_for_assignment(assignment) -> str:
    """Best-effort raw-hex default for the Hex tab: the entry's function
    byte(s) as plain hex, no separators, or "" if there's no assignment to
    default from."""
    if assignment is None:
        return ""
    if assignment["fn_byte2"] is None:
        return f"{assignment['fn_byte1']:02X}"
    return f"{assignment['fn_byte1']:02X}{assignment['fn_byte2']:02X}"


class KeyAssignmentEditDialog(ctk.CTkToplevel):
    """
    Blocking modal dialog to assign, reassign, or delete one key's
    (unshifted or shifted) function or global program. `on_save(kind,
    value)` is called if the user saves -- `kind` is `"function"` (value:
    an int or (byte1, byte2) tuple) or `"program"` (value: a global
    label's name string); `on_delete()` is called instead if the user
    clicks Delete. Neither callback is invoked on Cancel. The dialog
    doesn't touch the Memory object itself -- callers stay in control of
    when (and whether) the edit takes effect, same as RegisterEditDialog.

    `assignment` is the Key Assignment Register entry currently on this
    key (get_key_assignment()'s dict shape), or None. `program_assignment`
    is the global label currently on this key instead (a ProgramInfo, per
    get_program_for_key()), or None -- the two are mutually exclusive in
    practice (sec 4.7's lookup order means a Key Assignment Register entry
    always shadows a global-label one), but this dialog only ever receives
    whichever one is actually authoritative for display purposes; callers
    should pass `assignment` when it's set and only fall back to
    `program_assignment` otherwise, matching the real lookup order.
    `program_names` lists every assignable global label (from
    list_global_chain()) for the Program tab's picker, alphabetical.
    `flag_clear` is True when the key is assigned but its KEYFLAGS bit is
    clear (KeyAssignments.flag_clear_assignments()): the dialog says so.
    """

    def __init__(
        self,
        master,
        key_number: int,
        shifted: bool,
        assignment,
        program_assignment,
        program_names,
        on_save,
        on_delete,
        flag_clear=False,
    ):
        super().__init__(master)
        shift_label = "shifted" if shifted else "unshifted"
        self.title(f"Edit Key {key_number:02d} ({shift_label})")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        if PLATFORM_SYSTEM == "Darwin" and hasattr(master, "_menubar"):
            self.config(menu=master._menubar)

        self._on_save = on_save
        self._on_delete = on_delete
        self._has_existing_assignment = (
            assignment is not None or program_assignment is not None
        )
        self._program_names = sorted(program_names)

        if assignment is not None:
            current_text = f"Currently assigned: {assignment['name']}"
        elif program_assignment is not None:
            current_text = f'Currently assigned: program "{program_assignment.name}"'
        else:
            current_text = "Currently unassigned."
        ctk.CTkLabel(
            self,
            text=f"Key {key_number:02d} ({shift_label}) -- {current_text}",
            font=ctk.CTkFont(weight="bold"),
            wraplength=340,
            justify="left",
        ).pack(padx=16, pady=(16, 4), anchor="w")
        if flag_clear:
            ctk.CTkLabel(
                self,
                text=_FLAG_CLEAR_NOTE,
                text_color="gray50",
                wraplength=340,
                justify="left",
            ).pack(padx=16, pady=(0, 4), anchor="w")

        tabs = ctk.CTkTabview(self, width=360)
        tabs.pack(padx=16, pady=8, fill="both", expand=True)
        tabs.add("Function")
        tabs.add("Program")
        tabs.add("Raw Hex")

        # -- Function tab: pick a named built-in/peripheral function -----
        default_name = (
            assignment["name"]
            if assignment is not None and assignment["name"] in _ALL_FUNCTION_NAMES
            else (_ALL_FUNCTION_NAMES[0] if _ALL_FUNCTION_NAMES else "")
        )
        self._function_var = ctk.StringVar(value=default_name)
        ctk.CTkLabel(tabs.tab("Function"), text="Function:").pack(
            anchor="w", padx=8, pady=(12, 4)
        )
        ctk.CTkComboBox(
            tabs.tab("Function"),
            values=_ALL_FUNCTION_NAMES,
            variable=self._function_var,
            width=300,
        ).pack(anchor="w", padx=8, fill="x")
        # Typing and picking both change the variable, so one trace covers
        # both.
        self._dm41x_hint = ctk.CTkLabel(
            tabs.tab("Function"),
            text="",
            text_color="gray50",
            wraplength=320,
            justify="left",
        )
        self._dm41x_hint.pack(anchor="w", padx=8, pady=(8, 0))
        self._function_var.trace_add("write", self._update_dm41x_hint)
        self._update_dm41x_hint()

        # -- Raw Hex tab: 2 hex digits (single-byte) or 4 (XROM 2-byte) --
        self._hex_var = ctk.StringVar(value=_hex_for_assignment(assignment))
        ctk.CTkLabel(
            tabs.tab("Raw Hex"),
            text="Raw function byte(s): 2 hex digits (e.g. 40) for a "
            "built-in function, or 4 (e.g. A681) for an XROM/"
            "peripheral function.",
            wraplength=320,
            justify="left",
        ).pack(anchor="w", padx=8, pady=(12, 4))
        ctk.CTkEntry(
            tabs.tab("Raw Hex"),
            textvariable=self._hex_var,
            font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
        ).pack(anchor="w", padx=8, fill="x")

        # -- Program tab: pick a global label (sec 4.6) -------------------
        # A program can hold only one key assignment, so picking one here
        # (if it's already on a different key) moves it -- worth saying up
        # front rather than as a surprise after Save.
        if self._program_names:
            default_program = (
                program_assignment.name
                if program_assignment is not None
                and program_assignment.name in self._program_names
                else self._program_names[0]
            )
            self._program_var = ctk.StringVar(value=default_program)
            ctk.CTkLabel(
                tabs.tab("Program"),
                text="Global program (a program can be on only one key -- "
                "picking one already assigned elsewhere moves it here):",
                wraplength=320,
                justify="left",
            ).pack(anchor="w", padx=8, pady=(12, 4))
            ctk.CTkComboBox(
                tabs.tab("Program"),
                values=self._program_names,
                variable=self._program_var,
                width=300,
            ).pack(anchor="w", padx=8, fill="x")
        else:
            self._program_var = None
            ctk.CTkLabel(
                tabs.tab("Program"),
                text="This state has no global programs to assign.",
                text_color="gray50",
                wraplength=320,
                justify="left",
            ).pack(anchor="w", padx=8, pady=(12, 4))

        # Whichever tab already matches the current assignment (if any)
        # opens first -- an unknown/raw-hex assignment (fn2 present but no
        # matching function name, or a name outside the known list) should
        # land on Raw Hex rather than silently defaulting to some
        # unrelated function.
        if assignment is not None and assignment["name"] not in _ALL_FUNCTION_NAMES:
            tabs.set("Raw Hex")
        elif assignment is None and program_assignment is not None:
            tabs.set("Program")
        self._tabs = tabs

        extra_buttons = []
        if self._has_existing_assignment:
            extra_buttons.append(("Delete", self._on_delete_clicked))

        build_dialog_button_row(
            self,
            primary_text="Save",
            on_primary=self._on_save_clicked,
            extra_buttons=extra_buttons or None,
            pack_kwargs={"padx": 16, "pady": (8, 16), "fill": "x"},
        )

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _update_dm41x_hint(self, *_trace_args):
        """Says so under the Function box when the function in it is one
        only a DM41X has. Any spelling import accepts counts, as on Save."""
        try:
            op = resolve(self._function_var.get().strip(), programmable_only=False)
        except ValueError:
            op = None  # empty, half-typed or unknown: Save reports it
        dm41x_only = op is not None and is_dm41x_only(op)
        self._dm41x_hint.configure(text=_DM41X_ONLY_HINT if dm41x_only else "")

    def _on_save_clicked(self):
        which = self._tabs.get()
        try:
            if which == "Function":
                name = self._function_var.get().strip()
                if not name:
                    raise ValueError("Choose a function.")
                # Typed input doesn't have to match the display name's
                # exact spelling or case: any spelling program-text import
                # accepts works here too ("sigma+", "x^2", "p->r", "\\EREG";
                # docs/mnemonic_dialects_plan.md). Keyboard-only functions
                # (CAT, SST, ...) are assignable, so programmable_only is
                # off. An unknown name raises with "did you mean" hints.
                op = resolve(name, programmable_only=False)
                kind, value = "function", key_bytes_for(op)
            elif which == "Raw Hex":
                text = self._hex_var.get().strip().replace(" ", "")
                if len(text) == 2:
                    kind, value = "function", int(text, 16)
                elif len(text) == 4:
                    kind, value = "function", (int(text[0:2], 16), int(text[2:4], 16))
                else:
                    raise ValueError(f"Expected 2 or 4 hex digits, got {len(text)}.")
            else:  # Program
                if not self._program_names:
                    raise ValueError("This state has no global programs to assign.")
                name = self._program_var.get().strip()
                if not name or name not in self._program_names:
                    raise ValueError("Choose a program.")
                kind, value = "program", name
        except ValueError as e:
            logger.warning("Invalid key assignment value on %s tab: %s", which, e)
            messagebox.showerror("Invalid Value", str(e))
            return

        self._on_save(kind, value)
        self.destroy()

    def _on_delete_clicked(self):
        self._on_delete()
        self.destroy()
