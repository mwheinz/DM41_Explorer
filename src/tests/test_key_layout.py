"""Tests for the Key Assignments tab's keyboard layouts (GitHub issue #41):
where each key sits on the DM41L's and the DM41X's keyboard, and that the
ENTER key (41) is one double-size key -- double-width on the DM41X,
double-height on the DM41L -- rather than two keys.

The layout tables are tested directly. The two widget tests build a real
KeyAssignmentsTab but only inspect its structure, never its on-screen
geometry, so they don't depend on the platform's fonts or window system.
"""
import pytest

pytest.importorskip("customtkinter")

import customtkinter as ctk

from gui.key_assignments_tab import (
    DM41L_LAYOUT,
    DM41X_LAYOUT,
    GRID_COLUMNS,
    KeyAssignmentsTab,
    layout_placements,
)
from memory import DM41L, DM41X, Memory


# -- The layout tables ------------------------------------------------------


def test_every_row_of_both_layouts_divides_the_grid_evenly():
    for layout in (DM41L_LAYOUT, DM41X_LAYOUT):
        for row in layout:
            assert GRID_COLUMNS % len(row) == 0, row


def test_the_dm41x_has_six_key_rows_and_five_key_rows():
    assert {len(row) for row in DM41X_LAYOUT} == {5, 6}


def _keys(layout):
    return [p.cell for p in layout_placements(layout) if isinstance(p.cell, int)]


@pytest.mark.parametrize("layout", [DM41L_LAYOUT, DM41X_LAYOUT])
def test_each_assignable_key_is_placed_exactly_once(layout):
    keys = _keys(layout)
    assert len(keys) == len(set(keys)) == 34


def test_placements_cover_every_position_of_a_layout_exactly_once():
    for layout in (DM41L_LAYOUT, DM41X_LAYOUT):
        covered = {}
        for p in layout_placements(layout):
            for row in range(p.row, p.row + p.rowspan):
                for column in range(p.column, p.column + p.columnspan):
                    assert (row, column) not in covered, "cells overlap"
                    covered[(row, column)] = p.cell
        assert len(covered) == len(layout) * GRID_COLUMNS


def test_enter_is_double_width_on_the_dm41x():
    [enter] = [p for p in layout_placements(DM41X_LAYOUT) if p.cell == 41]
    [other] = [p for p in layout_placements(DM41X_LAYOUT) if p.cell == 42]
    assert enter.columnspan == 2 * other.columnspan
    assert enter.rowspan == 1


def test_enter_is_double_height_on_the_dm41l():
    [enter] = [p for p in layout_placements(DM41L_LAYOUT) if p.cell == 41]
    [other] = [p for p in layout_placements(DM41L_LAYOUT) if p.cell == 44]
    assert enter.rowspan == 2
    assert enter.columnspan == other.columnspan


def test_only_adjacent_repeats_merge():
    """The same number in two places that don't touch stays two cells, and
    labels (including blanks) are never merged."""
    layout = [[11, 12, 11], ["", "", 21], [11, 12, 21]]
    cells = [(p.cell, p.row, p.column, p.columnspan, p.rowspan) for p in layout_placements(layout)]
    assert cells == [
        (11, 0, 0, 10, 1), (12, 0, 10, 10, 1), (11, 0, 20, 10, 1),
        ("", 1, 0, 10, 1), ("", 1, 10, 10, 1), (21, 1, 20, 10, 2),
        (11, 2, 0, 10, 1), (12, 2, 10, 10, 1),
    ]


# -- The tab ----------------------------------------------------------------


@pytest.fixture
def make_tab():
    """Builds a tab rendered for a profile, in a withdrawn root window."""
    root = ctk.CTk()
    root.withdraw()

    def build(profile=DM41X):
        tab = KeyAssignmentsTab(root)
        tab.pack(fill="both", expand=True)
        tab.render(Memory(profile=profile))
        return tab

    yield build
    root.destroy()


@pytest.mark.parametrize("profile", [DM41L, DM41X])
def test_enter_is_one_key_with_one_pair_of_buttons(make_tab, profile):
    """ENTER occupies two grid positions but is ONE key, with one
    unshifted and one shifted button like every other key."""
    tab = make_tab(profile)
    assert sum(1 for key, _shifted in tab._key_buttons if key == 41) == 2
    for shifted in (False, True):
        assert isinstance(tab._key_buttons[(41, shifted)], ctk.CTkButton)


def test_equalizing_the_columns_does_not_flush_pending_drawing(make_tab, monkeypatch):
    """update_idletasks() runs every widget's pending redraw, and
    customtkinter's scroll bars then redraw from inside it, nesting without
    end (minutes per render on macOS).  The widths must not need it."""
    import tkinter

    tab = make_tab()
    flushes = []
    monkeypatch.setattr(tkinter.Misc, "update_idletasks", lambda self: flushes.append(self))
    tab._equalize_columns(tab._grid_frame)
    assert not flushes
