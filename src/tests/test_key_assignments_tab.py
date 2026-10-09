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
from gui.key_assignments_tab import FLAG_CLEAR_FG, FLAG_CLEAR_TEXT, KeyAssignmentsTab

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
    return tab._key_buttons[(key_number, shifted)].cget("text")


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


def test_a_program_assignment_shows_on_its_key(tab):
    """A global-label assignment shows with the "▸" marker rather than as
    unassigned."""
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    tab.render(memory)

    assert tab._key_buttons[(11, False)].cget("text") == "▸XMBCD"


# -- One keyboard at a time (phase 6, superseding issue #39) -----------------


def test_only_the_rendered_profiles_keyboard_is_drawn(tab):
    """Issue #39 put each layout on its own sub-tab; phase 6 draws just
    one, chosen by the state's profile (Mike, 2026-10-09)."""
    from gui.key_assignments_tab import DM41L_LAYOUT, DM41X_LAYOUT, layout_placements
    from memory import DM41L

    def key_cells(layout):
        return sum(isinstance(p.cell, int) for p in layout_placements(layout))

    assert not hasattr(tab, "_layout_tabs"), "the sub-tab view is gone"

    tab.render(Memory(profile=DM41L))
    assert tab._built_for == "DM41L"
    assert len(tab._key_buttons) == key_cells(DM41L_LAYOUT) * 2

    tab.render(Memory(profile=DM41X))
    assert tab._built_for == "DM41X"
    assert len(tab._key_buttons) == key_cells(DM41X_LAYOUT) * 2


def test_a_profile_change_rebuilds_the_grid(tab):
    """A mode change hands the tab a Memory with a different profile. The
    grid must be rebuilt, not relabelled -- the keys are in different
    places."""
    from memory import DM41L

    tab.render(Memory(profile=DM41X))
    first = tab._key_buttons[(11, False)]

    tab.render(Memory(profile=DM41L))
    assert tab._built_for == "DM41L"
    assert tab._key_buttons[(11, False)] is not first, "rebuilt, not reused"
    assert not first.winfo_exists(), "the old grid was destroyed"


def test_rendering_the_same_profile_again_reuses_the_grid(tab):
    """The expensive rebuild only happens on a real change: this tab
    deliberately configures existing buttons in place (see its module
    docstring) rather than recreating ~270 widgets per edit."""
    tab.render(Memory(profile=DM41X))
    before = tab._key_buttons[(11, False)]

    tab.render(Memory(profile=DM41X))
    assert tab._key_buttons[(11, False)] is before


def test_the_dm41l_keyboard_is_drawn_for_a_dm41l_state(tab):
    """A spot-check on the layout itself: key 42 sits in the first row of
    the DM41L's keyboard and the fourth row of the DM41X's."""
    from memory import DM41L

    def row_of(key_number):
        for cell in tab._grid_frame.winfo_children():
            if not cell.grid_info():
                continue
            for label in cell.winfo_children():
                if isinstance(label, ctk.CTkLabel) and label.cget(
                    "text"
                ) == f"{key_number:02d}":
                    return int(cell.grid_info()["row"])
        return None

    tab.render(Memory(profile=DM41L))
    assert row_of(42) == 0

    tab.render(Memory(profile=DM41X))
    assert row_of(42) == 3


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
    button = tab._key_buttons[(11, False)]
    assert button.cget("text_color") == FLAG_CLEAR_TEXT
    # Issue #42: the mark is a fill, not amber text on the blue key button.
    assert tuple(button.cget("fg_color")) == FLAG_CLEAR_FG


def test_lkaon_state_shows_the_same_assignments_unmarked(tab):
    tab.render(Memory.from_file(DATA_DIR / "lkaon3.d41", profile=DM41X))

    assert _button_text(tab, 11, False) == "▸LKATST"
    assert _button_text(tab, 12, True) == "⇧▸BBB"
    assert tuple(tab._key_buttons[(11, False)].cget("fg_color")) != FLAG_CLEAR_FG


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
