"""Tests for GitHub issue #41: the Key Assignments tab's keys follow the
application font, never get narrower than seven characters of it (so a long
function name doesn't resize a column of keys), and the DM41X layout spaces
its 5-key rows more widely than its 6-key rows, as the real keyboard does,
and the ENTER key (41) is one double-size key -- double-width on the DM41X,
double-height on the DM41L -- rather than two keys.

Since phase 6 of docs/dm41x_explorer_plan.md the tab draws ONE keyboard,
the one belonging to the rendered state's profile, so `make_tab` takes the
profile to build for and every test inspects the single `tab._grid_frame`.
A test that is about one particular keyboard says which; the rest are
parametrized over both.

Builds the real KeyAssignmentsTab (Xvfb in CI/sandboxes, like test_app.py).
"""
from pathlib import Path

import pytest

pytest.importorskip("customtkinter")

import customtkinter as ctk

from gui import key_assignments_tab as kat
from gui.key_assignments_tab import (
    DM41L_LAYOUT,
    DM41X_LAYOUT,
    GRID_COLUMNS,
    KEY_MIN_CHARS,
    KEY_TEXT_PREFIXES,
    KeyAssignmentsTab,
    key_button_min_width,
    layout_placements,
)
from gui.window_geometry import BASE_FONT_SIZE
from gui_helpers import pump, show
from memory import DM41L, DM41X, Memory
from memory.functions import SINGLE_BYTE_FUNCTIONS, XROM_FUNCTIONS

# The profile each keyboard belongs to, for parametrizing over both.
LAYOUTS = {DM41L: DM41L_LAYOUT, DM41X: DM41X_LAYOUT}

DATA_DIR = Path(__file__).parent / "data"
LARGE_FONT = 26
LONGEST_NAMES = sorted(
    {n for n in (*SINGLE_BYTE_FUNCTIONS.values(), *XROM_FUNCTIONS.values())},
    key=len,
)[-3:]


def _function_bytes(name):
    """The key-assignment function byte(s) for a function name."""
    codes = {**{n: c for c, n in SINGLE_BYTE_FUNCTIONS.items()},
             **{n: c for c, n in XROM_FUNCTIONS.items()}}
    return codes[name]


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def font_size():
    """Sets the application font the way gui/app.py does at start-up (the
    theme's CTkFont size), for tabs built during the test."""
    saved = ctk.ThemeManager.theme["CTkFont"]["size"]

    def use(size):
        ctk.ThemeManager.theme["CTkFont"]["size"] = size

    yield use
    ctk.ThemeManager.theme["CTkFont"]["size"] = saved


@pytest.fixture
def make_tab(root, font_size):
    """Builds a tab at the given font size, mapped so geometry is real."""
    tabs = []

    def build(size=BASE_FONT_SIZE, profile=DM41X):
        """A tab rendered for `profile`, so tab._grid_frame holds that
        model's keyboard. Only one grid is built, which is what the app
        does -- and half the widgets the two-grid version made."""
        font_size(size)
        tab = KeyAssignmentsTab(root)
        tab.pack(fill="both", expand=True)
        assert show(root)
        tab.render(Memory(profile=profile))
        pump(root, 0.05)
        tabs.append(tab)
        return tab

    return build


def _all_buttons(tab):
    return list(tab._key_buttons.values())


def _grid_cells(frame):
    return [w for w in frame.winfo_children() if w.grid_info()]


def _single_width_cells(frame):
    """The cells that are one key position wide (all but a double-width key)."""
    return [c for c in _grid_cells(frame) if int(c.grid_info()["columnspan"]) in (5, 6)]


def _key_cell(frame, key_number):
    """The grid cell holding `key_number`'s buttons."""
    [cell] = [
        c for c in _grid_cells(frame)
        if any(isinstance(w, ctk.CTkLabel) and w.cget("text") == f"{key_number:02d}"
               for w in c.winfo_children())
    ]
    return cell


# -- Font -----------------------------------------------------------------------


@pytest.mark.parametrize("size", [BASE_FONT_SIZE, 20])
@pytest.mark.parametrize("profile", list(LAYOUTS))
def test_key_buttons_use_the_application_font(make_tab, size, profile):
    tab = make_tab(size, profile)
    buttons = _all_buttons(tab)
    keys = sum(isinstance(p.cell, int) for p in layout_placements(LAYOUTS[profile]))
    assert len(buttons) == keys * 2, "an unshifted and a shifted button per key cell"
    assert {b.cget("font").cget("size") for b in buttons} == {size}


@pytest.mark.parametrize("profile", list(LAYOUTS))
def test_key_numbers_and_fixed_key_labels_follow_the_application_font(
    make_tab, profile
):
    tab = make_tab(20, profile)
    sizes = set()
    for cell in _grid_cells(tab._grid_frame):
        for label in cell.winfo_children():
            if isinstance(label, ctk.CTkLabel):
                sizes.add(label.cget("font").cget("size"))
    assert sizes == {17}, "key numbers and labels are 3 points under the application font"


def test_a_larger_font_makes_taller_keys(make_tab):
    small = make_tab(BASE_FONT_SIZE)
    large = make_tab(LARGE_FONT)
    assert large._key_height > small._key_height


# -- Minimum width --------------------------------------------------------------


def test_the_minimum_width_holds_seven_characters_of_the_font(root, font_size):
    font_size(BASE_FONT_SIZE)
    font = ctk.CTkFont()
    assert key_button_min_width(font) >= font.measure("0" * KEY_MIN_CHARS)


def test_the_minimum_width_holds_the_longest_function_name_shifted(root, font_size):
    font_size(BASE_FONT_SIZE)
    font = ctk.CTkFont()
    widest = max(font.measure(KEY_TEXT_PREFIXES + name) for name in LONGEST_NAMES)
    assert key_button_min_width(font) >= widest + kat.BUTTON_TEXT_PADDING


@pytest.mark.parametrize("size", [BASE_FONT_SIZE, LARGE_FONT])
def test_a_button_at_the_minimum_width_does_not_grow_for_any_name(make_tab, size):
    """The padding CTkButton adds around its text is a constant in
    key_assignments_tab; if a platform's differs, the buttons would grow."""
    tab = make_tab(size)
    button = tab._key_buttons[(11, False)]
    base = button.winfo_reqwidth()
    for name in LONGEST_NAMES:
        for shifted in (False, True):
            button = tab._key_buttons[(11, shifted)]
            button.configure(text=(KEY_TEXT_PREFIXES if shifted else "") + name)
            tab.update_idletasks()
            assert button.winfo_reqwidth() <= base, (name, shifted)


def test_the_minimum_width_grows_with_the_font(root, font_size):
    """The text part of the width doubles with the font; the button's own
    padding (a constant) doesn't, so it is left out of the comparison."""
    font_size(BASE_FONT_SIZE)
    small = key_button_min_width(ctk.CTkFont()) - kat.BUTTON_TEXT_PADDING
    font_size(2 * BASE_FONT_SIZE)
    large = key_button_min_width(ctk.CTkFont()) - kat.BUTTON_TEXT_PADDING
    assert large > 1.7 * small


@pytest.mark.parametrize("profile", list(LAYOUTS))
def test_assigning_long_function_names_does_not_resize_the_grid(make_tab, profile):
    tab = make_tab(BASE_FONT_SIZE, profile)
    frame = tab._grid_frame
    before = (frame.winfo_reqwidth(), frame.winfo_reqheight())
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41", profile=profile)
    for key, name in zip((11, 12, 13), LONGEST_NAMES):
        for shifted in (False, True):
            memory.key_assignments.set_assignment(key, shifted, _function_bytes(name))
    tab.render(memory)
    tab.update_idletasks()
    assert tab._key_buttons[(11, True)].cget("text") == "⇧" + LONGEST_NAMES[0]
    assert (frame.winfo_reqwidth(), frame.winfo_reqheight()) == before


# -- DM41X layout ---------------------------------------------------------------


def test_every_row_of_both_layouts_divides_the_grid_evenly():
    for layout in (DM41L_LAYOUT, DM41X_LAYOUT):
        for row in layout:
            assert GRID_COLUMNS % len(row) == 0, row


def test_the_dm41x_has_six_key_rows_and_five_key_rows():
    assert {len(row) for row in DM41X_LAYOUT} == {5, 6}


@pytest.mark.parametrize("profile", list(LAYOUTS))
def test_each_row_is_laid_out_across_the_whole_grid(make_tab, profile):
    tab = make_tab(BASE_FONT_SIZE, profile)
    layout = LAYOUTS[profile]
    by_row = {}
    for cell in _grid_cells(tab._grid_frame):
        info = cell.grid_info()
        for row in range(int(info["row"]), int(info["row"]) + int(info["rowspan"])):
            by_row.setdefault(row, []).append(
                (int(info["column"]), int(info["columnspan"]))
            )
    assert sorted(by_row) == list(range(len(layout)))
    for spans in by_row.values():
        spans.sort()
        position = 0
        for column, span in spans:
            assert column == position, "cells must be contiguous"
            position += span
        assert position == GRID_COLUMNS


def test_five_key_rows_have_wider_keys_than_six_key_rows(make_tab):
    tab = make_tab()
    widths = {}
    for cell in _single_width_cells(tab._grid_frame):
        info = cell.grid_info()
        count = GRID_COLUMNS // int(info["columnspan"])
        widths.setdefault(count, set()).add(cell.winfo_width())
    assert set(widths) == {5, 6}
    # Within a row length the keys are all the same width, and the 5-key
    # ones are wider by the ratio of the two column counts (6/5).
    assert all(max(w) - min(w) <= 1 for w in widths.values())
    ratio = max(widths[5]) / max(widths[6])
    assert 1.15 <= ratio <= 1.25


def test_every_dm41x_row_ends_at_the_same_edge(make_tab):
    tab = make_tab()
    ends = {}
    for cell in _grid_cells(tab._grid_frame):
        row = int(cell.grid_info()["row"])
        ends[row] = max(ends.get(row, 0), cell.winfo_x() + cell.winfo_width())
    assert len(ends) == len(DM41X_LAYOUT)
    assert max(ends.values()) - min(ends.values()) <= 2


@pytest.mark.parametrize("profile", list(LAYOUTS))
def test_the_key_number_does_not_cover_the_keys_border(make_tab, profile):
    """The key number label sits inside the cell's 1-pixel border, not on
    top of it (where it left a gap in the border's top edge)."""
    tab = make_tab(BASE_FONT_SIZE, profile)
    for cell in _grid_cells(tab._grid_frame):
        for label in cell.winfo_children():
            if isinstance(label, ctk.CTkLabel) and label.cget("text").isdigit():
                assert label.winfo_y() >= 1, label.cget("text")


# -- The double-size ENTER key (41) ------------------------------------------------


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


@pytest.mark.parametrize("profile", list(LAYOUTS))
def test_enter_is_one_key_with_one_pair_of_buttons(make_tab, profile):
    """ENTER occupies two grid positions but is ONE key:
    layout_placements() merges the repeats into a single cell, so it gets
    one unshifted and one shifted button like every other key. (While
    both keyboards were drawn at once each key had two buttons, one per
    grid; phase 6 draws one.)"""
    tab = make_tab(BASE_FONT_SIZE, profile)
    keys = {k for k, _shifted in tab._key_buttons}
    assert 41 in keys
    assert sum(1 for k, _s in tab._key_buttons if k == 41) == 2, "unshifted + shifted"
    for shifted in (False, True):
        assert isinstance(tab._key_buttons[(41, shifted)], ctk.CTkButton)


def test_enter_is_twice_as_wide_as_its_neighbours_on_the_dm41x(make_tab):
    tab = make_tab()
    enter = _key_cell(tab._grid_frame, 41)
    neighbour = _key_cell(tab._grid_frame, 42)
    # Two keys' worth, including the gap between them.
    assert abs(enter.winfo_width() - (2 * neighbour.winfo_width() + 2 * kat.CELL_PADX)) <= 2
    assert enter.winfo_height() == neighbour.winfo_height()


def test_enter_is_twice_as_tall_as_its_neighbours_on_the_dm41l(make_tab):
    """Its cell runs from the top of row 3 to the bottom of row 4."""
    tab = make_tab(BASE_FONT_SIZE, DM41L)
    frame = tab._grid_frame
    enter = _key_cell(frame, 41)
    third = [c for c in _grid_cells(frame) if int(c.grid_info()["row"]) == 2 and c is not enter]
    fourth = [c for c in _grid_cells(frame) if int(c.grid_info()["row"]) == 3]
    assert enter.winfo_y() == min(c.winfo_y() for c in third)
    assert enter.winfo_y() + enter.winfo_height() == max(c.winfo_y() + c.winfo_height() for c in fourth)
    assert enter.winfo_height() > 1.8 * _key_cell(frame, 44).winfo_height()
    # ...and its two buttons share the height rather than leaving a gap.
    assert (
        tab._key_buttons[(41, False)].winfo_height()
        > tab._key_buttons[(44, False)].winfo_height()
    )


# What the application window spends on its own padding and tab bar before
# the Key Assignments tab: a 1080-wide window gives the tab 1052 (measured).
APP_CHROME = 28


def test_the_dm41l_grid_fits_a_default_size_window_at_the_default_font(root, make_tab):
    """The grid used to be narrow enough for the default window; wider keys
    must not push its 10 columns off the right edge.  Measured where the
    window system's own fonts and scroll bar are, rather than assumed."""
    from gui.window_geometry import BASE_DEFAULT_SIZE

    root.geometry(f"{BASE_DEFAULT_SIZE[0] - APP_CHROME}x{BASE_DEFAULT_SIZE[1]}")
    tab = make_tab(BASE_FONT_SIZE, DM41L)
    pump(root, 0.3)
    scroller = tab._grid_frame.master  # the CTkScrollableFrame holding the grid
    assert scroller.winfo_reqwidth() <= scroller.winfo_width(), (
        scroller.winfo_reqwidth(),
        scroller.winfo_width(),
    )


def test_the_buttons_fill_their_keys(make_tab):
    """A key in a 5-key row is wider than one in a 6-key row, and its
    buttons stretch with it instead of sitting centered at the minimum."""
    tab = make_tab()
    five = tab._key_buttons[(51, False)].winfo_width()
    six = tab._key_buttons[(11, False)].winfo_width()
    assert five > six * 1.1


def test_one_unusually_long_assignment_widens_all_columns_equally(make_tab):
    """Text past the minimum (a program name with its markers, say) can
    still widen a key -- every column then grows together, so the grid
    stays aligned instead of one column jumping."""
    tab = make_tab()
    tab._key_buttons[(12, True)].configure(text="⚠⇧▸ABCDEFGHIJK")
    tab._equalize_columns(tab._grid_frame)
    tab.update()
    widths = {}
    ends = {}
    for cell in _grid_cells(tab._grid_frame):
        info = cell.grid_info()
        if int(info["columnspan"]) in (5, 6):
            count = GRID_COLUMNS // int(info["columnspan"])
            widths.setdefault(count, set()).add(cell.winfo_width())
        row = int(info["row"])
        ends[row] = max(ends.get(row, 0), cell.winfo_x() + cell.winfo_width())
    assert all(max(w) - min(w) <= 1 for w in widths.values()), widths
    assert max(ends.values()) - min(ends.values()) <= 2


# -- Column widths ----------------------------------------------------------------


@pytest.mark.parametrize("size", [BASE_FONT_SIZE, 20])
@pytest.mark.parametrize("profile", list(LAYOUTS))
def test_the_column_widths_match_what_the_widgets_really_ask_for(
    make_tab, size, profile
):
    """_equalize_columns() works the widths out from the fonts and padding;
    this checks that against the widths Tk reports, on whatever platform
    the tests run (button padding differs between them)."""
    tab = make_tab(size, profile)
    tab._key_buttons[(12, True)].configure(text="⚠⇧▸ABCDEFGHIJK")
    frame = tab._grid_frame
    tab._equalize_columns(frame)
    frame.update_idletasks()
    measured = 0
    for cell in _grid_cells(frame):
        needed = cell.winfo_reqwidth() + 2 * kat.CELL_PADX
        measured = max(measured, -(-needed // int(cell.grid_info()["columnspan"])))
    assert frame.grid_columnconfigure(0)["minsize"] == measured


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
