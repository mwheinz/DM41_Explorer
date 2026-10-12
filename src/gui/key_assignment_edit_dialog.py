"""
Modal dialog for editing (or clearing) a single key's assignment, from
gui/key_assignments_tab.py -- see that module's docstring for the overall
tab design. This dialog itself never touches the Memory object; it hands
the resolved assignment back to the caller via `on_save`, and signals a
delete via `on_delete`, exactly like RegisterEditDialog.

Four tabs, matching the two independent storage mechanisms docs/
key_assignments.md sec 4.1 describes: "Function", "XROM" and "Raw Hex"
all write a built-in/peripheral function into the Key Assignment
Registers (sec 4.2), while "Program" assigns a user's global label
instead (sec 4.6) --
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

from gui.dialog_common import build_dialog_button_row, scaled
from gui.contrast import SECONDARY_TEXT
from gui.tab_common import MONOSPACE_FONT_FAMILY
from memory.mnemonics import (
    XROM_FIRST_BYTES,
    assignable_choices,
    display,
    is_available_on,
    is_dm41x_only,
    is_known,
    key_bytes_for,
    library_of,
    op_for_key_bytes,
    parse_xrom_name_text,
    parse_xrom_text,
    resolve,
    resolve_xrom_name,
    xrom_bytes,
    xrom_numbers,
    xrom_op,
    xrom_text,
)

logger = logging.getLogger(__name__)

PLATFORM_SYSTEM = platform.system()

# Shown under the Function box, in DM41X mode, when the chosen function
# is one the DM41X added to the HP-41CX set: the state may still be sent
# to a DM41L later, which would not have it. In DM41L mode such a
# function is not offered at all (see _unavailable_message), so this hint
# never appears there.
_DM41X_ONLY_HINT = "DM41X only: a DM41L does not have this function."

# Shown under the title when the key is assigned but its key flag is clear,
# as LKAOFF leaves the top two rows of keys. Saving here sets the flag.
_FLAG_CLEAR_NOTE = (
    "This key's flag is clear, so the calculator treats it as unassigned "
    "(as after LKAOFF). Saving an assignment sets the flag."
)


def _unavailable_message(name: str, op, profile) -> str:
    """Why a known function cannot be assigned in this mode."""
    library = library_of(op)
    if library is not None:
        return (
            f"{name} comes from {library}, a plug-in module, and a "
            f"{profile.name} cannot load modules.\n\n"
            "Switch to DM41X mode to use it, or assign it by its XROM "
            "numbers on the XROM tab."
        )
    return (
        f"{name} is a DM41X function, and this state is a "
        f"{profile.name}'s.\n\n"
        f"A {profile.name} cannot run it, so it cannot be assigned to a "
        "key here. Switch to DM41X mode to use it."
    )


# The XROM tab's module numbers. Module 00 is not a real module, so the
# tab does not offer it; Raw Hex can still write one.
_XROM_MODULES = range(1, 32)
_XROM_FUNCTIONS = range(0, 64)


def _xrom_numbers_for_assignment(assignment):
    """(module, function) strings for the XROM tab, from an existing XROM
    assignment, or ("", "") when there isn't one."""
    if (
        assignment is None
        or assignment.fn_byte2 is None
        or assignment.fn_byte1 not in XROM_FIRST_BYTES
    ):
        return "", ""
    module, function = xrom_numbers(assignment.fn_byte1, assignment.fn_byte2)
    return str(module), str(function)


def _parse_xrom_fields(module_text: str, function_text: str):
    """(byte1, byte2) from the XROM tab's two fields. Raises ValueError,
    with a message for the user, if either is missing or out of range."""
    try:
        module, function = int(module_text.strip()), int(function_text.strip())
    except ValueError:
        raise ValueError("Enter a module number and a function number.") from None
    if module not in _XROM_MODULES or function not in _XROM_FUNCTIONS:
        raise ValueError(
            f"XROM {module},{function} is out of range: the module must be "
            "1-31 and the function 0-63."
        )
    return xrom_bytes(module, function)


def _hex_for_assignment(assignment) -> str:
    """Best-effort raw-hex default for the Hex tab: the entry's function
    byte(s) as plain hex, no separators, or "" if there's no assignment to
    default from."""
    if assignment is None:
        return ""
    if assignment.fn_byte2 is None:
        return f"{assignment.fn_byte1:02X}"
    return f"{assignment.fn_byte1:02X}{assignment.fn_byte2:02X}"


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
        profile=None,
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
        # The model this state belongs to, which decides which functions
        # can be assigned at all. None means "do not filter", for a
        # caller that has no profile to give.
        self._profile = profile
        # In DM41L mode the picker offers the HP-41CX set alone: a DM41L
        # cannot run a DM41X function, so offering it would be a trap.
        # A name two functions share -- two pacs' SOLVE, or a pac's LOG and
        # the mainframe's -- is listed once for each, labelled with where
        # it comes from, and picking one assigns exactly that function
        # (GitHub issue #52).
        self._choices = dict(assignable_choices(profile))
        self._function_names = sorted(self._choices)
        self._label_for_op = {op: label for label, op in self._choices.items()}
        current_op = (
            op_for_key_bytes(assignment.fn_byte1, assignment.fn_byte2)
            if assignment is not None
            else None
        )

        if assignment is not None:
            current_text = f"Currently assigned: {assignment.name}"
        elif program_assignment is not None:
            current_text = f'Currently assigned: program "{program_assignment.name}"'
        else:
            current_text = "Currently unassigned."
        ctk.CTkLabel(
            self,
            text=f"Key {key_number:02d} ({shift_label}) -- {current_text}",
            font=ctk.CTkFont(weight="bold"),
            wraplength=scaled(340),
            justify="left",
        ).pack(padx=16, pady=(16, 4), anchor="w")
        if flag_clear:
            ctk.CTkLabel(
                self,
                text=_FLAG_CLEAR_NOTE,
                text_color=SECONDARY_TEXT,
                wraplength=scaled(340),
                justify="left",
            ).pack(padx=16, pady=(0, 4), anchor="w")

        tabs = ctk.CTkTabview(self, width=scaled(360))
        tabs.pack(padx=16, pady=8, fill="both", expand=True)
        tabs.add("Function")
        tabs.add("XROM")
        tabs.add("Program")
        tabs.add("Raw Hex")

        # -- Function tab: pick a named built-in/peripheral function -----
        default_name = self._label_for_op.get(
            current_op, self._function_names[0] if self._function_names else ""
        )
        self._function_var = ctk.StringVar(value=default_name)
        ctk.CTkLabel(tabs.tab("Function"), text="Function:").pack(
            anchor="w", padx=8, pady=(12, 4)
        )
        ctk.CTkComboBox(
            tabs.tab("Function"),
            values=self._function_names,
            variable=self._function_var,
            width=scaled(300),
        ).pack(anchor="w", padx=8, fill="x")
        # Typing and picking both change the variable, so one trace covers
        # both.
        self._dm41x_hint = ctk.CTkLabel(
            tabs.tab("Function"),
            text="",
            text_color=SECONDARY_TEXT,
            wraplength=scaled(320),
            justify="left",
        )
        self._dm41x_hint.pack(anchor="w", padx=8, pady=(8, 0))
        self._function_var.trace_add("write", self._update_dm41x_hint)
        self._update_dm41x_hint()

        # -- XROM tab: any module (1-31) and function (0-63) -------------
        # For a module function the Function tab doesn't list, without
        # working out its bytes by hand. A pair no table names can still
        # be assigned: the module may simply not be known here.
        module_text, function_text = _xrom_numbers_for_assignment(assignment)
        self._xrom_module_var = ctk.StringVar(value=module_text)
        self._xrom_function_var = ctk.StringVar(value=function_text)
        xrom_tab = tabs.tab("XROM")
        ctk.CTkLabel(
            xrom_tab,
            text="Module (1-31) and function (0-63), as in XROM mm,ff:",
            wraplength=scaled(320),
            justify="left",
        ).pack(anchor="w", padx=8, pady=(12, 4))
        fields = ctk.CTkFrame(xrom_tab, fg_color="transparent")
        fields.pack(anchor="w", padx=8)
        ctk.CTkLabel(fields, text="XROM").pack(side="left")
        for var, after in (
            (self._xrom_module_var, ","),
            (self._xrom_function_var, None),
        ):
            ctk.CTkEntry(
                fields,
                textvariable=var,
                width=scaled(48),
                font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
            ).pack(side="left", padx=(6, 0))
            if after:
                ctk.CTkLabel(fields, text=after).pack(side="left", padx=(2, 0))
        self._xrom_info = ctk.CTkLabel(
            xrom_tab,
            text="",
            text_color=SECONDARY_TEXT,
            wraplength=scaled(320),
            justify="left",
        )
        self._xrom_info.pack(anchor="w", padx=8, pady=(8, 0))
        self._xrom_module_var.trace_add("write", self._update_xrom_info)
        self._xrom_function_var.trace_add("write", self._update_xrom_info)
        self._update_xrom_info()

        # -- Raw Hex tab: 2 hex digits (single-byte) or 4 (XROM 2-byte) --
        self._hex_var = ctk.StringVar(value=_hex_for_assignment(assignment))
        ctk.CTkLabel(
            tabs.tab("Raw Hex"),
            text="Raw function byte(s): 2 hex digits (e.g. 40) for a "
            "built-in function, or 4 (e.g. A681) for an XROM/"
            "peripheral function.",
            wraplength=scaled(320),
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
                wraplength=scaled(320),
                justify="left",
            ).pack(anchor="w", padx=8, pady=(12, 4))
            ctk.CTkComboBox(
                tabs.tab("Program"),
                values=self._program_names,
                variable=self._program_var,
                width=scaled(300),
            ).pack(anchor="w", padx=8, fill="x")
        else:
            self._program_var = None
            ctk.CTkLabel(
                tabs.tab("Program"),
                text="This state has no global programs to assign.",
                text_color=SECONDARY_TEXT,
                wraplength=scaled(320),
                justify="left",
            ).pack(anchor="w", padx=8, pady=(12, 4))

        # Whichever tab already matches the current assignment (if any)
        # opens first. One the Function tab can't show -- an unknown
        # function, or a name outside the list -- opens on XROM when it is
        # an XROM, and on Raw Hex otherwise, rather than silently
        # defaulting to some unrelated function.
        # A name this model does not have counts as unrepresentable here
        # too, not just an unknown one: in DM41L mode a state that already
        # has LKAOFF on a key opens on the XROM tab, which is honest -- the
        # Function tab cannot offer that name.
        if assignment is not None and current_op not in self._label_for_op:
            tabs.set("XROM" if module_text else "Raw Hex")
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
            op = self._op_for_function_text(self._function_var.get().strip())
        except ValueError:
            op = None  # empty, half-typed or unknown: Save reports it
        # Only worth saying in a mode that actually has the function: in
        # DM41L mode it is refused on Save instead, with a reason.
        dm41x_only = (
            op is not None and is_dm41x_only(op) and is_available_on(op, self._profile)
        )
        self._dm41x_hint.configure(text=_DM41X_ONLY_HINT if dm41x_only else "")

    def _update_xrom_info(self, *_trace_args):
        """Under the XROM tab's fields: the bytes, and the function's name
        when it is a known one -- or why the numbers can't be saved."""
        try:
            byte1, byte2 = _parse_xrom_fields(
                self._xrom_module_var.get(), self._xrom_function_var.get()
            )
        except ValueError as e:
            text = "" if not self._xrom_module_var.get().strip() else str(e)
            self._xrom_info.configure(text=text)
            return
        op = xrom_op(byte1, byte2)
        name = display(op) if is_known(op) else "Not a known function"
        lines = [f"Bytes {byte1:02X} {byte2:02X}: {name}"]
        if self._lacks_xrom(op):
            lines.append(f"A {self._profile.name} does not have this function.")
        elif not is_known(op):
            lines.append("It can still be assigned.")
        self._xrom_info.configure(text="\n".join(lines))

    def _lacks_xrom(self, op) -> bool:
        """True if this model certainly cannot run the XROM `op`: it isn't
        built in, and the model can't plug in a module that might have
        it. On a model that loads modules, any XROM may be there."""
        return not is_available_on(op, self._profile)

    def _op_for_function_text(self, text: str):
        """The function the Function box's text means: a label picked from
        the list means exactly that function; 'XROM "NAME"' an XROM
        function by name; anything else is a typed name, which follows
        the calculator's own search order, as ASN does -- with the
        Standard Applications Pac active, "LOG" is the pac's (issue #52).
        Raises ValueError for text that names nothing."""
        if text in self._choices:
            return self._choices[text]
        name = parse_xrom_name_text(text)
        if name is not None:
            return resolve_xrom_name(name)
        # Typed input doesn't have to match the display name's exact
        # spelling or case: any spelling program-text import accepts works
        # here too ("sigma+", "x^2", "p->r", "\\EREG";
        # docs/mnemonic_dialects_plan.md). Keyboard-only functions (CAT,
        # SST, ...) are assignable, so programmable_only is off. An unknown
        # name raises with "did you mean" hints.
        return resolve(text, programmable_only=False, prefer_modules=True)

    def _confirm_xrom(self, byte1: int, byte2: int) -> bool:
        """Whether to go ahead with an XROM given by its numbers. One this
        model can't run is allowed, after asking -- the state may be
        meant for another calculator -- unlike a function picked by name,
        which is refused (see _unavailable_message)."""
        op = xrom_op(byte1, byte2)
        if not self._lacks_xrom(op):
            return True
        what = xrom_text(byte1, byte2)
        if is_known(op):
            what += f" ({display(op)})"
        return messagebox.askyesno(
            "Not on This Calculator",
            f"{what} is not built into a {self._profile.name}, so pressing "
            "the key there will not run it.\n\nAssign it anyway?",
            parent=self,
        )

    def _on_save_clicked(self):
        which = self._tabs.get()
        try:
            if which == "Function":
                name = self._function_var.get().strip()
                if not name:
                    raise ValueError("Choose a function.")
                numbers = parse_xrom_text(name)
                if numbers is not None:
                    value = _parse_xrom_fields(*map(str, numbers))
                    if not self._confirm_xrom(*value):
                        return
                    self._on_save("function", value)
                    self.destroy()
                    return
                op = self._op_for_function_text(name)
                # A typed name bypasses the filtered dropdown, so the
                # availability check has to be here as well, or the filter
                # is only cosmetic. The key always stores the function's
                # bytes, whichever way it was named.
                if not is_available_on(op, self._profile):
                    raise ValueError(_unavailable_message(name, op, self._profile))
                kind, value = "function", key_bytes_for(op)
            elif which == "XROM":
                value = _parse_xrom_fields(
                    self._xrom_module_var.get(), self._xrom_function_var.get()
                )
                if not self._confirm_xrom(*value):
                    return
                kind = "function"
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
