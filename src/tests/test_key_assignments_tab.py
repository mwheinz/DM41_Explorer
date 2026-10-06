"""Tests for gui/key_assignments_tab.py's KeyAssignmentsTab, focused on
the newer Program-assignment support (docs/key_assignments.md sec 4.6)
added alongside the "Program" tab in gui/key_assignment_edit_dialog.py:
grid display of a global-label assignment (marked with the "▸" prefix,
previously shown as unassigned), the end-to-end assign/reassign/delete
flow through _edit_key() for both assignment kinds, and the mutual-
exclusion behavior between the two storage mechanisms on the same key.

manyfiles.dm41 is used here specifically because it has both kinds of
assignment already present in one real state (three global-label
assignments -- XMBCD/XMALPHA/PURXM on keys 11/12/13 unshifted -- plus two
built-in/peripheral ones -- EMROOM/XTOA on keys 14/15 unshifted).

Same pattern as test_key_assignment_edit_dialog.py: construct the real
widget against a live (withdrawn) Tk root and drive its methods directly.
Requires a real Tk display (Xvfb in CI/sandboxes).
"""

from pathlib import Path

import pytest

pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")

import customtkinter as ctk

from memory import DM41X, Memory
from memory.mnemonics import key_bytes_for, resolve
from gui.key_assignments_tab import FLAG_CLEAR_TEXT, KeyAssignmentsTab

DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def tab(root):
    t = KeyAssignmentsTab(root)
    t.pack()
    return t


def _button_text(tab, key_number, shifted):
    # Both grids' buttons for a key stay in sync -- checking the first is
    # enough (test_key_assignment_storage_research's bug writeup is the
    # reason this isn't assumed without at least one assertion per key).
    return tab._key_buttons[(key_number, shifted)][0].cget("text")


def test_render_shows_program_assignment_with_marker(tab):
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    tab.render(memory)

    assert _button_text(tab, 11, False) == "▸XMBCD"
    assert _button_text(tab, 12, False) == "▸XMALPHA"
    assert _button_text(tab, 13, False) == "▸PURXM"


def test_render_shows_function_assignment_without_marker(tab):
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    tab.render(memory)

    assert _button_text(tab, 14, False) == "EMROOM"
    assert _button_text(tab, 15, False) == "XTOA"


def test_render_header_counts_both_kinds(tab):
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    tab.render(memory)

    # 3 global-label assignments + 2 built-in/peripheral ones.
    assert tab._header_label.cget("text") == "Key assignments: 5"


def test_both_grids_stay_in_sync_for_a_program_assignment(tab):
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    tab.render(memory)

    dm41l_btn, hp41_btn = tab._key_buttons[(11, False)]
    assert dm41l_btn.cget("text") == hp41_btn.cget("text") == "▸XMBCD"


def test_layouts_are_separate_subtabs_dm41l_first(tab):
    """Issue #39: each keyboard layout lives on its own sub-tab, DM41L
    first and HP41 second, and DM41L is the one shown by default."""
    from gui.key_assignments_tab import DM41L_TAB, HP41_TAB

    assert tab._layout_tabs._name_list == [DM41L_TAB, HP41_TAB]
    assert tab._layout_tabs.get() == DM41L_TAB


def test_each_grid_is_built_on_its_own_subtab(tab):
    """Each key's two buttons (one per layout) live under different
    sub-tabs -- the DM41L button under the DM41L tab's frame, the HP41
    button under the HP41 tab's frame."""
    from gui.key_assignments_tab import DM41L_TAB, HP41_TAB

    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    tab.render(memory)

    dm41l_btn, hp41_btn = tab._key_buttons[(11, False)]
    dm41l_tab = tab._layout_tabs.tab(DM41L_TAB)
    hp41_tab = tab._layout_tabs.tab(HP41_TAB)
    assert str(dm41l_btn).startswith(str(dm41l_tab) + ".")
    assert str(hp41_btn).startswith(str(hp41_tab) + ".")


def test_edit_key_assign_program_to_unassigned_key(tab):
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    tab.render(memory)

    assignment, program = tab._resolve_key(21, False)
    assert assignment is None and program is None

    tab._edit_key(21, False)
    # _edit_key() opens a real modal dialog; grab its save callback via
    # the dialog instance it just created (still open, not destroyed,
    # since nothing clicked Save yet) rather than simulating widget
    # interaction, matching test_key_assignment_edit_dialog.py's approach
    # of driving the handler directly.
    dlg = root_children_dialog(tab)
    dlg._tabs.set("Program")
    dlg._program_var.set("XMALPHA")
    dlg._on_save_clicked()

    assert memory.programs.get_program_for_key(21, False).name == "XMALPHA"
    assert _button_text(tab, 21, False) == "▸XMALPHA"
    # XMALPHA moved off key 12 -- its old slot is unassigned now.
    assert memory.programs.get_program_for_key(12, False) is None
    assert _button_text(tab, 12, False) == "--"


def test_edit_key_assign_function_clears_conflicting_program(tab):
    """Per the mutual-exclusion rule (memory.py's set_key_assignment()),
    assigning a built-in function to a key that currently holds a global
    program silently clears that program's assignment."""
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    tab.render(memory)

    tab._edit_key(11, False)  # currently XMBCD
    dlg = root_children_dialog(tab)
    dlg._tabs.set("Raw Hex")
    dlg._hex_var.set("40")  # '+'
    dlg._on_save_clicked()

    assert memory.key_assignments.get_assignment(11, False)["name"] == "+"
    assert memory.programs.get_program_for_key(11, False) is None
    assert _button_text(tab, 11, False) == "+"


def test_edit_key_delete_clears_program_assignment(tab):
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    tab.render(memory)

    tab._edit_key(12, False)  # currently XMALPHA
    dlg = root_children_dialog(tab)
    dlg._on_delete_clicked()

    assert memory.programs.get_program_for_key(12, False) is None
    assert memory.key_assignments.get_key_flag(12, False) is False
    assert _button_text(tab, 12, False) == "--"


def test_notify_change_called_on_program_assignment(root):
    calls = []
    tab = KeyAssignmentsTab(root, on_change=lambda: calls.append(1))
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    tab.render(memory)

    tab._edit_key(21, False)
    dlg = root_children_dialog(tab)
    dlg._tabs.set("Program")
    dlg._program_var.set("XMALPHA")
    dlg._on_save_clicked()

    assert calls, "on_change should fire for a program-assignment save"


def root_children_dialog(tab):
    """Finds the (single) open KeyAssignmentEditDialog among the tab's
    own children -- KeyAssignmentEditDialog(self, ...) in _edit_key()
    passes the tab itself as `master`, so the still-open dialog (nothing's
    clicked Save/Cancel yet) shows up in the tab's own winfo_children(),
    same as any other Tk Toplevel does relative to its master."""
    from gui.key_assignment_edit_dialog import KeyAssignmentEditDialog

    dialogs = [
        w for w in tab.winfo_children() if isinstance(w, KeyAssignmentEditDialog)
    ]
    assert len(dialogs) == 1, "expected exactly one open KeyAssignmentEditDialog"
    return dialogs[0]


# -- LKAOFF: an assignment whose key flag is clear (plan, phase 3 step 8) --


def test_flag_clear_assignment_is_shown_with_a_warning_mark(tab):
    tab.render(Memory.from_file(DATA_DIR / "lkaoff3.d41", profile=DM41X))

    assert _button_text(tab, 11, False) == "⚠▸LKATST"
    assert _button_text(tab, 12, True) == "⚠⇧▸BBB"
    button = tab._key_buttons[(11, False)][0]
    assert button.cget("text_color") == FLAG_CLEAR_TEXT


def test_lkaon_state_shows_the_same_assignments_unmarked(tab):
    tab.render(Memory.from_file(DATA_DIR / "lkaon3.d41", profile=DM41X))

    assert _button_text(tab, 11, False) == "▸LKATST"
    assert _button_text(tab, 12, True) == "⇧▸BBB"
    assert tab._key_buttons[(11, False)][0].cget("text_color") != FLAG_CLEAR_TEXT


def test_header_counts_the_flag_clear_keys(tab):
    tab.render(Memory.from_file(DATA_DIR / "lkaoff3.d41", profile=DM41X))
    assert tab._header_label.cget("text") == (
        "Key assignments: 2 -- 2 with the key flag clear (LKAOFF)"
    )

    tab.render(Memory.from_file(DATA_DIR / "lkaon3.d41", profile=DM41X))
    assert tab._header_label.cget("text") == "Key assignments: 2"


def test_assigning_a_marked_key_clears_the_mark(tab):
    memory = Memory.from_file(DATA_DIR / "lkaoff3.d41", profile=DM41X)
    tab.render(memory)

    memory.key_assignments.set_assignment(11, False, key_bytes_for(resolve("COS")))
    tab._refresh_buttons()

    assert _button_text(tab, 11, False) == "COS"
    assert _button_text(tab, 12, True) == "⚠⇧▸BBB"
