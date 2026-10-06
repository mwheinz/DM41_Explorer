"""Tests for the DM41X's additions to the XROM table (phase 1 of
docs/dm41x_explorer_plan.md).

The DM41X (and DM41XN) add 18 XROM functions to the HP-41CX set: X<I>Y
(XROM 25,63), TRNG (26,36), and the 16 functions of the DM41X's own module
(26,38 - 26,53). Their names and codes below were transcribed from hp41uc's
table (hp41ucg.h, the "-DM 41X-" section at 26,37) so the checks don't depend
on that source tree being present. X<I>Y is not in hp41uc yet; its code comes
from a real DM41XN state file.

Every code is confirmed by a state file captured on a real DM41X or DM41XN:
  - tests/data/dm41xn.dm41 / dm41xn.txt: one program calling X<I>Y and TRNG.
  - tests/data/xrom.d41 / xrom.raw / xrom.txt: one program calling ED$, X<I>Y,
    TRNG and the 16 DM41X-module functions (plan, S5).
  - tests/data/dm41x_xrom_keys.d41: LKAON, LKAOFF and ED$ assigned to keys
    (plan, S6).
"""

from pathlib import Path

import pytest

from memory import (
    DM41L,
    DM41X,
    Memory,
    decode_program_raw,
    decode_program_txt,
    encode_program_txt,
)
from memory.device_profile import PROFILES
from memory.functions import CX_XROM_FUNCTIONS, DM41X_XROM_FUNCTIONS, XROM_FUNCTIONS
from memory.mnemonics import (
    OpKind,
    canonical,
    display,
    entries,
    function_op,
    is_dm41x_only,
    key_bytes_for,
    resolve,
    xrom_op,
)

DATA_DIR = Path(__file__).parent / "data"

# XROM (module, function) -> name, in hp41uc's order.
DM41X_ADDITIONS = {
    (25, 63): "X<I>Y",
    (26, 36): "TRNG",
    (26, 38): "ABSP",
    (26, 39): "AINT",
    (26, 40): "ASWAP",
    (26, 41): "CLAC",
    (26, 42): "CLEM",
    (26, 43): "FAST",
    (26, 44): "FILL",
    (26, 45): "FLCOPY",
    (26, 46): "FLHD",
    (26, 47): "FLTYPE",
    (26, 48): "LKAOFF",
    (26, 49): "LKAON",
    (26, 50): "RENMFL",
    (26, 51): "RETPFL",
    (26, 52): "SLOW",
    (26, 53): "WORKFL",
}

# The program in xrom.raw, in the order it calls them (ED$ first).
XROM_PROGRAM_NAMES = ["ED", "X<I>Y", "TRNG"] + [
    name
    for (module, _), name in DM41X_ADDITIONS.items()
    if module == 26 and name != "TRNG"
]


def _xrom_number(byte1, byte2):
    """(module, function) for an XROM's two bytes (docs/key_assignments.md)."""
    return ((byte1 & 0x07) << 2) | (byte2 >> 6), byte2 & 0x3F


def _whitespace_normalised(text):
    return " ".join(text.split())


def _program_bytes(fixture):
    """The only program in tests/data/<fixture>, as the instruction bytes
    in memory."""
    memory = Memory.from_file(DATA_DIR / fixture, profile=DM41X)
    (program,) = memory.programs.list_programs()
    return memory.programs.get_program_bytes(program)


# -- The tables -------------------------------------------------------------


def test_the_additions_are_the_confirmed_xroms_in_hp41ucs_order():
    found = {_xrom_number(*pair): name for pair, name in DM41X_XROM_FUNCTIONS.items()}
    assert found == DM41X_ADDITIONS


def test_table_sizes():
    assert len(CX_XROM_FUNCTIONS) == 95
    assert len(DM41X_XROM_FUNCTIONS) == 18
    assert len(XROM_FUNCTIONS) == 113


def test_the_two_tables_do_not_overlap_and_make_up_the_whole_table():
    assert not CX_XROM_FUNCTIONS.keys() & DM41X_XROM_FUNCTIONS.keys()
    assert XROM_FUNCTIONS == {**CX_XROM_FUNCTIONS, **DM41X_XROM_FUNCTIONS}


def test_no_addition_reuses_a_cx_code_or_name():
    cx_names = set(CX_XROM_FUNCTIONS.values())
    assert not cx_names & set(DM41X_XROM_FUNCTIONS.values())


# -- Device profiles --------------------------------------------------------


def test_dm41l_has_only_the_original_cx_xroms():
    assert DM41L.builtin_xroms == frozenset(CX_XROM_FUNCTIONS)
    assert len(DM41L.builtin_xroms) == 95


def test_dm41x_has_the_cx_xroms_and_its_additions():
    assert DM41X.builtin_xroms == frozenset(XROM_FUNCTIONS)
    assert DM41X.builtin_xroms - DM41L.builtin_xroms == frozenset(DM41X_XROM_FUNCTIONS)
    assert DM41L.builtin_xroms < DM41X.builtin_xroms


def test_registered_profiles_have_their_xroms():
    assert PROFILES["DM41L"].builtin_xroms == DM41L.builtin_xroms
    assert PROFILES["DM41X"].builtin_xroms == DM41X.builtin_xroms


# -- Names ------------------------------------------------------------------


@pytest.mark.parametrize("pair, name", sorted(DM41X_XROM_FUNCTIONS.items()))
def test_every_addition_resolves_by_canonical_lower_and_mixed_case(pair, name):
    op = xrom_op(*pair)
    assert canonical(op) == display(op) == name
    for spelling in (name, name.lower(), name.swapcase(), name.capitalize()):
        assert resolve(spelling) == op, spelling
    assert key_bytes_for(op) == pair


def test_ed_dollar_is_the_dm41x_name_for_ed():
    ed = xrom_op(0xA6, 0x73)
    assert resolve("ED") == ed
    for spelling in ("ED$", "ed$", "Ed$"):
        assert resolve(spelling) == ed, spelling
    # One code, one canonical name: it still decompiles as ED everywhere.
    assert canonical(ed) == "ED"
    assert not is_dm41x_only(ed)


@pytest.mark.parametrize("pair", sorted(DM41X_XROM_FUNCTIONS))
def test_additions_are_flagged_dm41x_only(pair):
    assert is_dm41x_only(xrom_op(*pair))


@pytest.mark.parametrize(
    "op",
    [
        xrom_op(0xA6, 0x41),  # ALENG, Extended Functions
        xrom_op(0xA6, 0x73),  # ED
        xrom_op(0xA6, 0x9C),  # TIME
        xrom_op(0xA6, 0xA3),  # SWPT, the last CX Time function
        function_op(0x40),  # +
        function_op(0x00),  # CAT, keyboard only
    ],
)
def test_original_functions_are_not_flagged(op):
    assert not is_dm41x_only(op)


def test_the_flag_is_set_for_exactly_the_additions():
    flagged = {e.op.code for e in entries() if e.dm41x_only}
    assert flagged == set(DM41X_XROM_FUNCTIONS)
    assert all(e.op.kind is OpKind.XROM for e in entries() if e.dm41x_only)


# -- Program text -----------------------------------------------------------


def test_names_compile_with_extra_whitespace_and_any_case():
    text = b'LBL "T"\n  lkaoff\n\tX<i>y  \nTrNg\nED$\nEND\n'
    want = [
        b"\xc0\x00\xf2\x00T",
        b"\xa6\xb0",
        b"\xa6\x7f",
        b"\xa6\xa4",
        b"\xa6\x73",
    ]
    assert decode_program_txt(text).startswith(b"".join(want))


def test_dm41xn_text_compiles_to_the_bytes_in_the_state_file():
    compiled = decode_program_txt((DATA_DIR / "dm41xn.txt").read_bytes())
    in_memory = _program_bytes("dm41xn.dm41")
    # Only the END differs: the compiler writes a placeholder END (C0 00 0D),
    # the calculator's carries the link to the previous program.
    assert compiled[:-3] == in_memory[:-3]
    assert compiled[-3:] == b"\xc0\x00\x0d"


def test_dm41xn_program_decompiles_to_xrom_numbers_and_names():
    lines = encode_program_txt(_program_bytes("dm41xn.dm41")).decode().splitlines()
    assert lines[1:3] == ["XROM 25,63 ;X<I>Y", "XROM 26,36 ;TRNG"]


def test_xrom_text_compiles_to_the_bytes_in_xrom_raw():
    compiled = decode_program_txt((DATA_DIR / "xrom.txt").read_bytes())
    program = decode_program_raw((DATA_DIR / "xrom.raw").read_bytes())
    assert compiled[:-3] == program[:-3]
    assert compiled[-3:] == b"\xc0\x00\x0d"


def test_xrom_state_file_holds_the_same_program_as_xrom_raw():
    assert _program_bytes("xrom.d41") == decode_program_raw(
        (DATA_DIR / "xrom.raw").read_bytes()
    )


def test_xrom_program_decompiles_to_hp41ucs_canonical_names():
    program = decode_program_raw((DATA_DIR / "xrom.raw").read_bytes())
    lines = encode_program_txt(program).decode().splitlines()
    assert lines[0] == 'LBL "DM41X"'
    assert lines[-1].startswith("END")
    names = [line.split(";")[1] for line in lines[1:-1]]
    assert names == XROM_PROGRAM_NAMES
    assert lines[1:-1][0] == "XROM 25,51 ;ED"


# -- Key assignments (plan, S6) -----------------------------------------------


def _assignments(memory):
    return [
        (a["key_number"], a["shifted"], a["name"])
        for a in memory.key_assignments.list_assignments()
    ]


def test_s6_state_file_shows_the_new_xroms_by_name():
    memory = Memory.from_file(DATA_DIR / "dm41x_xrom_keys.d41", profile=DM41X)
    # Newest first. The buffer's first slot is an empty leftover.
    assert _assignments(memory) == [
        (13, False, "ED"),
        (12, False, "LKAOFF"),
        (11, False, "LKAON"),
    ]


def test_s6_state_file_round_trips_through_load_and_save():
    text = (DATA_DIR / "dm41x_xrom_keys.d41").read_text()
    memory = Memory.from_string(text, profile=DM41X)
    assert _whitespace_normalised(memory.to_string()) == _whitespace_normalised(text)


def test_assigning_new_xroms_to_keys_round_trips():
    memory = Memory.from_file(DATA_DIR / "dm41x_base.d41", profile=DM41X)
    keys = memory.key_assignments
    for key_number, name in ((11, "LKAON"), (12, "LKAOFF"), (13, "TRNG")):
        keys.set_assignment(key_number, False, key_bytes_for(resolve(name)))

    # The calculator's own bytes for them, from the S6 sample.
    assert keys.get_assignment(12, False)["fn_byte1"] == 0xA6
    assert keys.get_assignment(12, False)["fn_byte2"] == 0xB0
    assert keys.get_assignment(11, False)["fn_byte2"] == 0xB1
    assert keys.get_assignment(13, False)["fn_byte2"] == 0xA4

    reloaded = Memory.from_string(memory.to_string(), profile=DM41X)
    assert _assignments(reloaded) == [
        (13, False, "TRNG"),
        (12, False, "LKAOFF"),
        (11, False, "LKAON"),
    ]
    assert all(reloaded.key_assignments.get_key_flag(k, False) for k in (11, 12, 13))
