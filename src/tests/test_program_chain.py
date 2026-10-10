"""
Tests for memory/program_chain.py -- byte-level global-chain marker
parsing/encoding, operating on a plain bytes buffer rather than register-
addressed Memory state. This is the piece Memory.import_program() (see
test_program_import.py) uses to inspect and patch a standalone program's
bytes before splicing them into program memory.
"""

from pathlib import Path

import pytest

from memory import Memory, decode_chain_marker, decode_label_name, encode_chain_marker, walk_chain
from memory.program_files import decode_program_dat

DATA_DIR = Path(__file__).parent / "data"

# APPTEST's own instruction bytes (simple.dm41, see test_program_export.py):
# a global label header ("APPTEST") through its own packed END marker.
APPTEST_BYTES = bytes.fromhex(
    "c000f8004150505445535410021140111010468475b200c40309"
)

# unlabelled.dm41's own first program (see test_program_export.py): starts
# with an ALPHA-string opcode ("f8..."), no global label at all, ends in a
# plain (unpacked) END whose own distance was 0/0 in its source memory
# (it was the first program there too).
UNLABELLED_BYTES = bytes.fromhex("f84e4f204c4142454c72715142c00009")


def test_decode_chain_marker_apptest_header():
    marker = decode_chain_marker(APPTEST_BYTES, 0)
    assert marker.is_label
    assert marker.bbb == 0
    assert marker.distance_registers == 0
    assert marker.label_length == 7  # 0xf8 & 0xf = 8, minus 1 = 7 chars ("APPTEST")


def test_decode_label_name_apptest():
    marker = decode_chain_marker(APPTEST_BYTES, 0)
    name, key = decode_label_name(APPTEST_BYTES, 0, marker.label_length)
    assert name == "APPTEST"
    assert key == 0x00


def test_decode_chain_marker_apptest_trailing_end():
    # Last 3 bytes: "c40309" -- a packed plain END, distance 3*7+2=23 back
    # to APPTEST's own header (docs/program.md's worked example).
    marker = decode_chain_marker(APPTEST_BYTES, len(APPTEST_BYTES) - 3)
    assert not marker.is_label
    assert marker.end_type == 0
    assert marker.distance_registers * 7 + marker.bbb == 23


def test_decode_chain_marker_out_of_bounds_returns_none():
    assert decode_chain_marker(APPTEST_BYTES, len(APPTEST_BYTES) - 2) is None
    assert decode_chain_marker(APPTEST_BYTES, -1) is None


def test_decode_chain_marker_rejects_non_marker_byte():
    assert decode_chain_marker(b"\x01\x02\x03", 0) is None


def test_encode_chain_marker_round_trips_apptest_trailing_end():
    original = APPTEST_BYTES[-3:]
    marker = decode_chain_marker(APPTEST_BYTES, len(APPTEST_BYTES) - 3)
    encoded = encode_chain_marker(
        marker.bbb, marker.distance_registers, marker.third_byte
    )
    assert encoded == original


def test_encode_chain_marker_matches_dotend_worked_example():
    # docs/program.md's worked simple.dm41 example: register 197 decodes
    # to bbb=2 (010), distance_registers=1, third byte 0x20 (.END.) -> the
    # raw bytes "c40120".
    assert encode_chain_marker(2, 1, 0x20) == bytes.fromhex("c40120")


def test_encode_chain_marker_rejects_out_of_range_fields():
    with pytest.raises(ValueError):
        encode_chain_marker(8, 0, 0x20)  # bbb doesn't fit 3 bits
    with pytest.raises(ValueError):
        encode_chain_marker(0, 0x200, 0x20)  # distance_registers doesn't fit 9 bits


def test_walk_chain_apptest_finds_trailing_then_outermost_label():
    entries = walk_chain(APPTEST_BYTES)
    # Trailing END first (newest), then APPTEST's own header (outermost,
    # bbb=distance_registers=0 -- "no predecessor").
    assert len(entries) == 2
    assert entries[0].index == len(APPTEST_BYTES) - 3
    assert not entries[0].is_label
    assert entries[-1].index == 0
    assert entries[-1].is_label
    assert entries[-1].name == "APPTEST"
    assert entries[-1].bbb == 0
    assert entries[-1].distance_registers == 0


def test_walk_chain_unlabelled_program_has_only_the_trailing_marker():
    # No global label at all -- the trailing marker is both the newest
    # AND the outermost (only) entry.
    entries = walk_chain(UNLABELLED_BYTES)
    assert len(entries) == 1
    assert entries[0].index == len(UNLABELLED_BYTES) - 3
    assert not entries[0].is_label
    assert entries[0].bbb == 0
    assert entries[0].distance_registers == 0


def test_walk_chain_twolabels_finds_both_internal_labels():
    # tests/data/twolabels.dm41's program: FIRST -> SECOND -> .END., no
    # plain END at all. Both labels should be found, newest (SECOND, the
    # one closer to the trailing marker) first, oldest (FIRST, the
    # outermost one) last.
    memory = Memory.from_file(DATA_DIR / "twolabels.dm41")
    program = memory.programs.list_programs()[0]
    data = memory.programs.get_program_bytes(program)
    entries = walk_chain(data)
    names = [e.name for e in entries if e.is_label]
    assert names == ["SECOND", "FIRST"]
    assert entries[-1].name == "FIRST"


def test_walk_chain_tower_has_only_its_own_trailing_marker():
    # tower.dat: a real, third-party-compiled 1088-byte program with no
    # global label at all -- same shape as the small unlabelled case
    # above, just much larger.
    data = decode_program_dat((DATA_DIR / "tower.dat").read_bytes())
    entries = walk_chain(data)
    assert len(entries) == 1
    assert entries[0].index == len(data) - 3
