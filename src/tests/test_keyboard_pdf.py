"""Tests for the PDF keyboard diagram of the key assignments (GitHub issue
#76): memory/keyboard.py's key_labels() and memory/keyboard_pdf.py.

Where things land on the page is checked by recording the builder's
draw_text() calls and comparing them with key_boxes(), not by reading the
PDF back, so no PDF parser is needed.
"""

import datetime
import re
from pathlib import Path

import pytest

from memory import DM41L, DM41X, Memory
from memory import keyboard_pdf
from memory.keyboard import (
    FUNCTION,
    PROGRAM,
    KeyLabel,
    key_labels,
    layout_for_profile,
    layout_placements,
)
from memory.keyboard_pdf import (
    MARGIN,
    PAGE_SIZES,
    SYMBOL_FONT,
    TEXT_FONT,
    UNTITLED,
    key_assignments_pdf,
    key_boxes,
    table_rows,
    text_runs,
    write_key_assignments_pdf,
)

DATA_DIR = Path(__file__).parent / "data"
DATE = datetime.date(2026, 11, 13)


def _pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type /Page\b", pdf))


def _media_box(pdf: bytes):
    match = re.search(rb"/MediaBox \[ *([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) *\]", pdf)
    return tuple(float(v) for v in match.groups())


@pytest.fixture
def drawn(monkeypatch):
    """Records every draw_text() call as (text, x, y), page by page."""
    calls = []
    real = keyboard_pdf.draw_text

    def record(pdf, text, x, y, size, bold=False):
        calls.append((text, x, y))
        real(pdf, text, x, y, size, bold)

    monkeypatch.setattr(keyboard_pdf, "draw_text", record)
    return calls


# -- key_labels() --------------------------------------------------------------


def test_key_labels_has_both_kinds():
    """manyfiles.dm41 has three global-label assignments and two Key
    Assignment Register ones (see test_key_assignments_tab.py)."""
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41", profile=DM41L)
    labels = key_labels(memory)
    assert labels[(11, False)] == KeyLabel("XMBCD", PROGRAM)
    assert labels[(12, False)] == KeyLabel("XMALPHA", PROGRAM)
    assert labels[(13, False)] == KeyLabel("PURXM", PROGRAM)
    assert labels[(14, False)] == KeyLabel("EMROOM", FUNCTION)
    assert labels[(15, False)] == KeyLabel("XTOA", FUNCTION)
    assert len(labels) == 5


def test_key_labels_marks_flag_clear_assignments():
    """lkaoff3.d41 was saved after LKAOFF: both assignments are there,
    with their key flags clear."""
    memory = Memory.from_file(DATA_DIR / "lkaoff3.d41", profile=DM41X)
    labels = key_labels(memory)
    assert len(labels) == 2
    assert all(label.flag_clear for label in labels.values())

    memory = Memory.from_file(DATA_DIR / "lkaon3.d41", profile=DM41X)
    assert not any(label.flag_clear for label in key_labels(memory).values())


def test_a_register_assignment_wins_over_a_global_label_on_the_same_key():
    """The calculator looks the Key Assignment Registers up first, so a
    state that somehow has both shows the register's function."""
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41", profile=DM41L)
    # Write a register entry for key 11, where XMBCD is assigned, past
    # set_assignment()'s mutual exclusion.
    key_byte = memory.key_assignments.key_byte_for(11, False)
    entries = memory.key_assignments.decode_entries()
    memory.key_assignments._encode_entries([(0x40, None, key_byte)] + entries)
    assert memory.programs.get_program_for_key(11, False) is not None
    assert key_labels(memory)[(11, False)] == KeyLabel("+", FUNCTION)


# -- Text --------------------------------------------------------------------


def test_text_runs_use_symbol_for_hp41_glyphs():
    assert text_runs("ΣREG") == [(SYMBOL_FONT, "Σ"), (TEXT_FONT, "REG")]
    assert text_runs("X≠Y?") == [
        (TEXT_FONT, "X"),
        (SYMBOL_FONT, "≠"),
        (TEXT_FONT, "Y?"),
    ]


def test_text_runs_fall_back_to_trigraphs():
    """⊦ (append) is in neither standard font, so it prints as its
    hp41uc trigraph rather than vanishing."""
    [(font, text)] = text_runs("A⊦B")
    assert font == TEXT_FONT
    assert text.startswith("A\\") and text.endswith("B")


def test_text_presentation_selectors_are_dropped():
    assert text_runs("↑\ufe0e") == [(SYMBOL_FONT, "↑")]


# -- The keyboard ------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture, profile",
    [("keyassigns.dm41", DM41L), ("keyassigns.dm41", DM41X)],
)
def test_each_assignment_is_drawn_on_its_own_key(drawn, fixture, profile):
    """Every assignment's name is drawn inside its key: the shifted one
    in the strip across the top, the unshifted one below it."""
    memory = Memory.from_file(DATA_DIR / fixture, profile=profile)
    labels = key_labels(memory)
    assert len(labels) == 22
    pdf = key_assignments_pdf(memory, date=DATE)

    layout = layout_for_profile(profile)
    width, height = _media_box(pdf)[2:]
    # key_boxes() with the page's left margin and width places the keys
    # as drawn; the keyboard's top is below the heading, found from where
    # key 11's number was drawn.
    boxes = {
        box.cell: box
        for box in key_boxes(layout, MARGIN, 0, width - 2 * MARGIN)
        if isinstance(box.cell, int)
    }
    numbers = {}
    for text, x, y in drawn:  # the first "11" is the key; later ones the list
        if re.fullmatch(r"\d\d", text):
            numbers.setdefault(text, (x, y))
    first = boxes[11]
    offset = numbers["11"][1] - (first.y_top - first.height + 3)

    for (key_number, shifted), label in labels.items():
        box = boxes[key_number]
        top = box.y_top + offset
        strip_bottom = top - box.strip
        # The half of the key the name belongs in. (keyassigns.dm41 has the
        # same function on both halves of some keys, so the name is looked
        # for in the right half rather than anywhere on the key.)
        if shifted:
            low, high = strip_bottom, top
        else:
            low, high = top - box.height, strip_bottom
        hits = [
            (x, y)
            for text, x, y in drawn
            if text == label.name
            and box.x <= x <= box.x + box.width
            and low <= y <= high
        ]
        assert hits, (key_number, shifted, label.name)


def test_the_dm41l_is_drawn_landscape_and_the_dm41x_portrait():
    memory = Memory(profile=DM41L)
    llx, lly, urx, ury = _media_box(key_assignments_pdf(memory, date=DATE))
    assert urx > ury
    memory = Memory(profile=DM41X)
    llx, lly, urx, ury = _media_box(key_assignments_pdf(memory, date=DATE))
    assert urx < ury


def test_page_size_a4():
    memory = Memory(profile=DM41X)
    box = _media_box(key_assignments_pdf(memory, date=DATE, page_size="a4"))
    assert box[2:] == pytest.approx(PAGE_SIZES["a4"], abs=0.01)


def test_unknown_page_size_is_refused():
    with pytest.raises(ValueError):
        key_assignments_pdf(Memory(profile=DM41X), page_size="tabloid")


def test_the_output_is_repeatable():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41", profile=DM41L)
    first = key_assignments_pdf(memory, name="manyfiles", date=DATE)
    assert first.startswith(b"%PDF-")
    assert first == key_assignments_pdf(memory, name="manyfiles", date=DATE)


def test_the_title_is_the_state_name_with_model_date_and_count_below(drawn):
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41", profile=DM41L)
    key_assignments_pdf(memory, name="manyfiles", date=DATE)
    assert drawn[0][0] == "manyfiles"
    assert drawn[1][0] == "DM41L  ·  2026-11-13  ·  5 assignments"


def test_a_state_without_a_name_gets_a_generic_title(drawn):
    key_assignments_pdf(Memory(profile=DM41X), date=DATE)
    assert drawn[0][0] == UNTITLED
    assert drawn[1][0] == "DM41X  ·  2026-11-13  ·  0 assignments"


def test_the_list_is_on_a_second_page(drawn):
    """The keyboard has the first page to itself; the list starts the
    second, under the same title and details."""
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41", profile=DM41L)
    pdf = key_assignments_pdf(memory, name="manyfiles", date=DATE)
    assert _pages(pdf) == 2
    texts = [text for text, _, _ in drawn]
    second = texts.index("All assignments")
    assert texts[second - 2 : second] == [
        "manyfiles",
        "DM41L  ·  2026-11-13  ·  5 assignments",
    ]


def test_an_empty_keyboard_still_has_its_list_page(drawn):
    pdf = key_assignments_pdf(Memory(profile=DM41L), date=DATE)
    assert _pages(pdf) == 2
    assert "No keys are assigned." in [text for text, _, _ in drawn]


# -- The list ------------------------------------------------------------------


def test_table_rows_are_in_key_order_with_shifted_keys_negative():
    labels = {
        (21, True): KeyLabel("B", FUNCTION),
        (11, False): KeyLabel("A", PROGRAM),
        (11, True): KeyLabel("C", FUNCTION, flag_clear=True),
    }
    assert [(key, label.name) for key, label in table_rows(labels)] == [
        ("11", "A"),
        ("-11", "C"),
        ("-21", "B"),
    ]


def test_every_key_assigned_lists_all_68_across_pages(drawn):
    """A full DM41X keyboard's list fits on the one page after the
    keyboard's, with nothing lost or listed twice."""
    memory = Memory(profile=DM41X)
    for key in layout_placements(layout_for_profile(DM41X)):
        if isinstance(key.cell, int):
            memory.key_assignments.set_assignment(key.cell, False, 0x40)  # +
            memory.key_assignments.set_assignment(key.cell, True, 0x41)  # -
    labels = key_labels(memory)
    assert len(labels) == 68
    pdf = key_assignments_pdf(memory, date=DATE)
    assert _pages(pdf) == 2
    keys = [text for text, _, _ in drawn if re.fullmatch(r"-?\d\d", text)]
    # Each key number appears once on its key, and each "NN"/"-NN" once in
    # the list.
    listed = keys[34:]
    assert sorted(listed) == sorted(key for key, _ in table_rows(labels))


def test_write_key_assignments_pdf(tmp_path):
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41", profile=DM41L)
    path = tmp_path / "keys.pdf"
    write_key_assignments_pdf(memory, path, date=DATE)
    assert path.read_bytes() == key_assignments_pdf(memory, date=DATE)
