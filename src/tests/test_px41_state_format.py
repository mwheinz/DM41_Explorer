"""Reading and writing PX-41CX dumps (`.px41`, GitHub issue #82): the state
text format with a `PX41` header, single spaces between registers, a
trailing space on each register row and a closing `EOF` line.

The PX-41CX has the DM41X's memory map, so these load with the DM41X
profile until it has a profile of its own.
"""

from pathlib import Path

import pytest

from memory import DM41L, DM41X, Memory

DATA_DIR = Path(__file__).parent / "data"
PX41_FIXTURES = sorted(DATA_DIR.glob("px41cx_*.px41"))

# The dumps that are not in the layout a PX-41CX writes (tests/data/README.md):
# GRAVITY is a DM41 state with a PX41 header, NQUEENS was written by
# hp41cx_tools' encoder, and SETCLOCK-agarza has no trailing spaces.
NOT_DEVICE_LAYOUT = {
    "px41cx_GRAVITY.px41",
    "px41cx_NQUEENS.px41",
    "px41cx_SETCLOCK-agarza.px41",
}
DEVICE_LAYOUT = [p for p in PX41_FIXTURES if p.name not in NOT_DEVICE_LAYOUT]


def _ids(paths):
    return [p.name for p in paths]


def _as_dm41(text):
    """The same state with a DM41 header and no EOF line."""
    lines = text.splitlines()
    assert lines[0] == "PX41"
    return "\n".join(["DM41"] + [line for line in lines[1:] if line != "EOF"])


def test_there_are_fixtures():
    assert len(PX41_FIXTURES) == 15
    assert len(DEVICE_LAYOUT) == 12


@pytest.mark.parametrize("path", PX41_FIXTURES, ids=_ids(PX41_FIXTURES))
def test_every_dump_loads_and_keeps_its_header(path):
    memory = Memory.from_file(path, profile=DM41X)
    assert memory._header == "PX41"
    assert memory.programs.list_programs()


@pytest.mark.parametrize("path", PX41_FIXTURES, ids=_ids(PX41_FIXTURES))
def test_a_dump_holds_the_same_registers_as_its_dm41_form(path):
    """Only the layout differs: the registers read are the same."""
    text = path.read_text()
    px41 = Memory.from_string(text, profile=DM41X)
    dm41 = Memory.from_string(_as_dm41(text), profile=DM41X)
    addresses = set(px41._core_memory) | set(dm41._core_memory)
    assert addresses
    for addr in addresses:
        assert px41.get_register(addr) == dm41.get_register(addr), hex(addr)
    assert px41._special_registers == dm41._special_registers


@pytest.mark.parametrize("path", DEVICE_LAYOUT, ids=_ids(DEVICE_LAYOUT))
def test_a_dump_in_the_device_layout_is_written_back_byte_for_byte(path):
    text = path.read_text()
    assert Memory.from_string(text, profile=DM41X).to_string() == text


@pytest.mark.parametrize(
    "name", sorted(NOT_DEVICE_LAYOUT), ids=sorted(NOT_DEVICE_LAYOUT)
)
def test_any_other_px41_layout_is_written_in_the_device_layout(name):
    memory = Memory.from_file(DATA_DIR / name, profile=DM41X)
    written = memory.to_string()
    lines = written.splitlines()
    assert lines[0] == "PX41"
    assert lines[-1] == "EOF"
    rows = [line for line in lines[1:-1] if ":" not in line]
    assert rows and all(row.endswith(" ") and "  " not in row for row in rows)
    assert Memory.from_string(written, profile=DM41X) == memory


def test_a_dm41_state_is_still_written_in_the_dm41_layout():
    memory = Memory.from_file(DATA_DIR / "simple.dm41", profile=DM41L)
    written = memory.to_string()
    assert written.startswith("DM41\n")
    assert "EOF" not in written
    assert Memory.from_string(written, profile=DM41L) == memory


def test_a_px41_and_a_dm41_state_with_the_same_registers_differ():
    """The header is part of the state: it says how it is written."""
    text = (DATA_DIR / "px41cx_GTO.px41").read_text()
    px41 = Memory.from_string(text, profile=DM41X)
    dm41 = Memory.from_string(_as_dm41(text), profile=DM41X)
    assert px41 != dm41


def test_eof_ends_the_state_and_what_follows_is_ignored():
    """Anything after EOF -- noise from the serial transfer, say -- is not
    part of the state, in either layout."""
    text = (DATA_DIR / "px41cx_GTO.px41").read_text()
    clean = Memory.from_string(text, profile=DM41X)
    noisy = Memory.from_string(text + "garbage\n\x00\x00 not a row\n", profile=DM41X)
    assert noisy == clean

    dm41 = _as_dm41(text) + "\nEOF\nleftovers\n"
    assert Memory.from_string(dm41, profile=DM41X) == Memory.from_string(
        _as_dm41(text), profile=DM41X
    )


def test_eof_is_optional():
    text = (DATA_DIR / "px41cx_GTO.px41").read_text()
    without = text.replace("EOF\n", "")
    assert Memory.from_string(without, profile=DM41X) == Memory.from_string(
        text, profile=DM41X
    )


def test_a_header_with_surrounding_spaces_is_accepted():
    text = (DATA_DIR / "px41cx_GTO.px41").read_text()
    padded = text.replace("PX41\n", "PX41  \n", 1)
    assert Memory.from_string(padded, profile=DM41X)._header == "PX41"


def test_an_unknown_header_is_still_refused():
    with pytest.raises(ValueError, match="Invalid header: PX42"):
        Memory.from_string("PX42\n00 01020304050607", profile=DM41X)


def test_a_new_memory_is_a_dm41_state_unless_told_otherwise():
    assert Memory()._header == "DM41"
    new = Memory(header="PX41", profile=DM41X)
    written = new.to_string()
    assert written.startswith("PX41\n") and written.endswith("\nEOF\n")
    with pytest.raises(ValueError, match="Unknown state header"):
        Memory(header="HP41")
