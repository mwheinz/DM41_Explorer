"""Tests for gui/key_assignment_edit_dialog.py's KeyAssignmentEditDialog.

Two areas: the pre-existing Function tab typed-input normalization
(GitHub issue #17) -- typing a lowercase or ASCII-approximated function
name ("cos", "x^2", "sigma+", "x<=y?") must resolve to the real
assignment, the same way picking it from the dropdown would -- and the
newer Program tab (global-label assignments, docs/key_assignments.md sec
4.6), including the on_save(kind, value) contract both tabs now share.

Same pattern as test_register_range_dialog.py: construct the real dialog
against a live (withdrawn) Tk root, drive its StringVar/button handler
directly rather than simulating keystrokes.

Requires a real Tk display (Xvfb in CI/sandboxes) -- same requirement as
test_app.py.
"""

from unittest import mock

import pytest

pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")

from tkinter import messagebox
import customtkinter as ctk

from gui.key_assignment_edit_dialog import KeyAssignmentEditDialog
from memory import DM41L, DM41X
from memory.functions import XROM_FUNCTIONS
from memory.mnemonics import key_bytes_for, resolve


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


def _make_dialog(
    root,
    on_save=None,
    on_delete=None,
    assignment=None,
    program_assignment=None,
    program_names=(),
    flag_clear=False,
    profile=None,
):
    return KeyAssignmentEditDialog(
        root,
        key_number=1,
        shifted=False,
        assignment=assignment,
        program_assignment=program_assignment,
        program_names=list(program_names),
        on_save=on_save or mock.Mock(),
        on_delete=on_delete or mock.Mock(),
        flag_clear=flag_clear,
        profile=profile,
    )


@pytest.mark.parametrize(
    "typed,expected_bytes",
    [
        ("cos", 0x5A),
        ("x^2", 0x51),
        ("p->r", 0x4E),
        ("sigma+", 0x47),
        ("x<=y?", 0x46),
    ],
)
def test_typed_lowercase_or_ascii_name_resolves_on_save(root, typed, expected_bytes):
    on_save = mock.Mock()
    dlg = _make_dialog(root, on_save=on_save)
    dlg._function_var.set(typed)

    dlg._on_save_clicked()

    on_save.assert_called_once_with("function", expected_bytes)


def test_typed_ascii_native_xrom_name_is_not_mangled(root):
    """'X<=NN?' is spelled with literal ASCII in the real table -- typing
    it (in any case) must resolve to that XROM, not to the unrelated
    single-byte X<=Y? or X<=0?."""
    on_save = mock.Mock()
    dlg = _make_dialog(root, on_save=on_save)
    dlg._function_var.set("x<=nn?")

    dlg._on_save_clicked()

    on_save.assert_called_once_with("function", (0xA6, 0x7C))


def test_dropdown_uses_hp41_display_names(root):
    dlg = _make_dialog(
        root,
        assignment={
            "key_number": 1, "shifted": False,
            "fn_byte1": 0x4E, "fn_byte2": None, "name": "P-R",
        },
    )
    # The current assignment's display name is a real dropdown entry, so
    # the Function tab (not Raw Hex) opens with it selected.
    assert dlg._function_var.get() == "P-R"
    assert dlg._tabs.get() == "Function"


def test_dm41x_only_hint_follows_the_function_in_the_box(root):
    dlg = _make_dialog(root)

    def hint():
        return dlg._dm41x_hint.cget("text")

    dlg._function_var.set("LKAOFF")
    assert "DM41X only" in hint()
    dlg._function_var.set("cos")
    assert hint() == ""
    # Any spelling Save accepts counts, and so does a function whose
    # name is only another spelling of an original one.
    dlg._function_var.set("trng")
    assert "DM41X only" in hint()
    dlg._function_var.set("ED$")
    assert hint() == ""
    # Half-typed or unknown: no hint (Save reports the problem).
    dlg._function_var.set("lka")
    assert hint() == ""
    dlg._function_var.set("")
    assert hint() == ""


def test_dm41x_only_hint_is_shown_for_the_current_assignment(root):
    dlg = _make_dialog(
        root,
        assignment={
            "key_number": 1, "shifted": False,
            "fn_byte1": 0xA6, "fn_byte2": 0xB1, "name": "LKAON",
        },
    )
    assert dlg._function_var.get() == "LKAON"
    assert "DM41X only" in dlg._dm41x_hint.cget("text")


def test_dm41x_only_function_can_be_assigned(root):
    on_save = mock.Mock()
    dlg = _make_dialog(root, on_save=on_save)
    dlg._function_var.set("LKAOFF")

    dlg._on_save_clicked()

    on_save.assert_called_once_with("function", (0xA6, 0xB0))


def test_unknown_function_error_suggests_close_match(root, monkeypatch):
    errors = []
    monkeypatch.setattr(
        messagebox, "showerror", lambda title, msg: errors.append((title, msg))
    )
    dlg = _make_dialog(root)
    dlg._function_var.set("sigmaregg")

    dlg._on_save_clicked()

    assert errors and "did you mean" in errors[0][1]


def test_still_rejects_genuinely_unknown_function(root, monkeypatch):
    on_save = mock.Mock()
    errors = []
    monkeypatch.setattr(
        messagebox, "showerror", lambda title, msg: errors.append((title, msg))
    )
    dlg = _make_dialog(root, on_save=on_save)
    dlg._function_var.set("not a real function")

    dlg._on_save_clicked()

    on_save.assert_not_called()
    assert errors and errors[0][0] == "Invalid Value"


def test_raw_hex_two_digits_saves_as_function(root):
    on_save = mock.Mock()
    dlg = _make_dialog(root, on_save=on_save)
    dlg._tabs.set("Raw Hex")
    dlg._hex_var.set("40")

    dlg._on_save_clicked()

    on_save.assert_called_once_with("function", 0x40)


def test_raw_hex_four_digits_saves_as_xrom_function(root):
    on_save = mock.Mock()
    dlg = _make_dialog(root, on_save=on_save)
    dlg._tabs.set("Raw Hex")
    dlg._hex_var.set("A681")

    dlg._on_save_clicked()

    on_save.assert_called_once_with("function", (0xA6, 0x81))


# ---- Program tab (docs/key_assignments.md sec 4.6) ----


def test_program_tab_saves_chosen_name(root):
    on_save = mock.Mock()
    dlg = _make_dialog(root, on_save=on_save, program_names=["AAA", "BBB"])
    dlg._tabs.set("Program")
    dlg._program_var.set("BBB")

    dlg._on_save_clicked()

    on_save.assert_called_once_with("program", "BBB")


def test_program_tab_defaults_to_current_program_assignment(root):
    """If the key's current assignment is a global label, the Program tab
    opens pre-selected to it and is the tab that opens by default."""
    program = mock.Mock(name="AAA")
    program.name = "AAA"
    dlg = _make_dialog(
        root, program_assignment=program, program_names=["AAA", "BBB"]
    )

    assert dlg._tabs.get() == "Program"
    assert dlg._program_var.get() == "AAA"


def test_program_tab_with_no_programs_shows_message_and_rejects_save(root):
    on_save = mock.Mock()
    errors = []
    with mock.patch.object(
        messagebox, "showerror", lambda title, msg: errors.append((title, msg))
    ):
        dlg = _make_dialog(root, on_save=on_save, program_names=[])
        dlg._tabs.set("Program")

        dlg._on_save_clicked()

    assert dlg._program_var is None
    on_save.assert_not_called()
    assert errors and errors[0][0] == "Invalid Value"


def test_function_assignment_takes_display_priority_over_program(root):
    """Per docs sec 4.7's real lookup order, a Key Assignment Register
    entry shadows a global-label one on the same key -- callers should
    only ever pass one of `assignment`/`program_assignment`, but the
    dialog's default-tab logic should still prefer `assignment` if both
    were somehow passed."""
    program = mock.Mock(name="AAA")
    program.name = "AAA"
    dlg = _make_dialog(
        root,
        assignment={
            "key_number": 1, "shifted": False,
            "fn_byte1": 0x40, "fn_byte2": None, "name": "+",
            "raw_key_byte": 0x01,
        },
        program_assignment=program,
        program_names=["AAA"],
    )

    assert dlg._tabs.get() == "Function"
    assert "Currently assigned: +" in dlg.winfo_children()[0].cget("text")


def test_flag_clear_note_is_shown_only_when_the_flag_is_clear(root):
    def note_shown(dlg):
        return any(
            "flag is clear" in str(w.cget("text"))
            for w in dlg.winfo_children()
            if isinstance(w, ctk.CTkLabel)
        )

    assert note_shown(_make_dialog(root, flag_clear=True))
    assert not note_shown(_make_dialog(root))


# -- The function list follows the mode (plan, phase 6) ----------------------
#
# In DM41L mode the picker offers only the functions a DM41L has: a DM41L
# cannot run a DM41X function, so offering it would be a trap (Mike,
# 2026-10-09). The 18 DM41X additions are X<I>Y, TRNG and the DM41X
# module's 16.


def test_the_picker_offers_every_function_in_dm41x_mode(root):
    dialog = _make_dialog(root, profile=DM41X)
    assert "LKAOFF" in dialog._function_names
    assert "X<I>Y" in dialog._function_names
    assert "COS" in dialog._function_names


def test_the_picker_hides_dm41x_functions_in_dm41l_mode(root):
    dialog = _make_dialog(root, profile=DM41L)
    assert "LKAOFF" not in dialog._function_names
    assert "X<I>Y" not in dialog._function_names
    assert "COS" in dialog._function_names, "the CX set is still offered"


def test_the_two_lists_differ_by_exactly_the_dm41x_additions(root):
    """Pinned against the profiles themselves, so a function added to
    either model's built-in set cannot silently change the picker."""
    wide = _make_dialog(root, profile=DM41X)._function_names
    narrow = _make_dialog(root, profile=DM41L)._function_names
    added = {XROM_FUNCTIONS[code] for code in DM41X.builtin_xroms - DM41L.builtin_xroms}
    assert set(wide) - set(narrow) == added
    assert len(added) == 18


def test_no_profile_offers_everything(root):
    """A caller with no profile to give gets the unfiltered list, which is
    what the import path and the mnemonic reference want."""
    dialog = _make_dialog(root, profile=None)
    assert "LKAOFF" in dialog._function_names


def test_a_typed_dm41x_function_is_refused_in_dm41l_mode(root):
    """Typing bypasses the dropdown, so the filter has to be enforced on
    Save too, or it is only cosmetic."""
    on_save = mock.Mock()
    dialog = _make_dialog(root, on_save=on_save, profile=DM41L)
    dialog._tabs.set("Function")
    dialog._function_var.set("LKAOFF")

    with mock.patch.object(messagebox, "showerror") as error:
        dialog._on_save_clicked()

    on_save.assert_not_called()
    assert error.called
    message = error.call_args.args[1]
    assert "DM41X function" in message
    assert "DM41L" in message


def test_a_typed_dm41x_function_is_accepted_in_dm41x_mode(root):
    on_save = mock.Mock()
    dialog = _make_dialog(root, on_save=on_save, profile=DM41X)
    dialog._tabs.set("Function")
    dialog._function_var.set("LKAOFF")

    with mock.patch.object(messagebox, "showerror") as error:
        dialog._on_save_clicked()

    error.assert_not_called()
    kind, value = on_save.call_args.args
    assert kind == "function"
    assert value == key_bytes_for(resolve("LKAOFF", programmable_only=False))


def test_an_unknown_name_still_reports_itself_as_unknown(root):
    """The availability check must not swallow the "did you mean" help for
    a name that is not a function at all."""
    dialog = _make_dialog(root, profile=DM41L)
    dialog._tabs.set("Function")
    dialog._function_var.set("NOTAFUNCTION")

    with mock.patch.object(messagebox, "showerror") as error:
        dialog._on_save_clicked()

    assert "DM41X function" not in error.call_args.args[1]


def test_the_dm41x_hint_only_appears_in_a_mode_that_has_the_function(root):
    """In DM41X mode the hint is worth saying: the state may be sent to a
    DM41L later. In DM41L mode the function cannot be assigned at all, so
    the hint would be the wrong message."""
    wide = _make_dialog(root, profile=DM41X)
    wide._function_var.set("LKAOFF")
    assert "DM41X only" in wide._dm41x_hint.cget("text")

    narrow = _make_dialog(root, profile=DM41L)
    narrow._function_var.set("LKAOFF")
    assert narrow._dm41x_hint.cget("text") == ""


def test_the_hint_stays_empty_for_a_function_both_models_have(root):
    dialog = _make_dialog(root, profile=DM41X)
    dialog._function_var.set("COS")
    assert dialog._dm41x_hint.cget("text") == ""


def test_an_existing_dm41x_assignment_opens_on_raw_hex_in_dm41l_mode(root):
    """A state can already have LKAOFF on a key (saved in DM41X mode, or
    made on the calculator). The Function tab cannot offer that name in
    DM41L mode, so the dialog opens on Raw Hex rather than silently
    showing some unrelated function."""
    byte1, byte2 = key_bytes_for(resolve("LKAOFF", programmable_only=False))
    assignment = {
        "name": "LKAOFF",
        "fn_byte1": byte1,
        "fn_byte2": byte2,
        "key_number": 1,
        "shifted": False,
        "raw_key_byte": 0x01,
    }
    dialog = _make_dialog(root, assignment=assignment, profile=DM41L)
    assert dialog._tabs.get() == "Raw Hex"
