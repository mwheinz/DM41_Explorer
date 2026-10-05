"""
Tests that Memory.pack() produces what a REAL PACK on real hardware
produces, using before/after dump pairs captured from a real DM41L/DM41X
(tests/data):

    manyfiles.dm41              -> manyfiles-packed.dm41
    dm41x_pack_dotend.dm41      -> dm41x_pack_dotend-packed.dm41
    lander.dm41                 -> lander-packed.dm41
    targ.dm41                   -> targ-packed.dm41
    twolabels.dm41              -> twolabels-packed.dm41
    manyfiles-packed.dm41       -> manyfiles-repacked.dm41   (PACK of a packed file)

What the real PACK was observed to do (see also docs/program.md):

1. Deletes every standalone NULL (0x00) instruction from program memory,
   EXCEPT a NULL sitting between two number-entry instructions, which is
   what keeps "12345 <NULL> 67890" from becoming one number.
2. Rewrites each GTO/XEQ's cached jump-distance bytes. This project clears
   them all to 0x00 ("not resolved yet"; the calculator resolves them again
   when the program runs). A real PACK leaves a jump alone when nothing
   moved, so the comparisons below treat the jump-cache bytes as
   don't-cares (`clear_jump_caches()` on the expected side).
3. Marks every END "packed" (low nibble 9) and re-links the chain.
4. Always closes the newest program with a real END placed immediately
   after its last instruction, followed by the permanent `.END.` as a
   separate, empty marker on the next register boundary -- even when the
   source had the program closed by `.END.` directly.
"""

from pathlib import Path

import pytest

from memory.opcode_scan import (
    clear_jump_caches,
    iter_instructions,
    scan_global_markers_forward,
)
from fixture_loading import load_fixture

DATA_DIR = Path(__file__).parent / "data"
ALL_FIXTURES = sorted(DATA_DIR.glob("*.dm41"))

# (as captured, after a real PACK)
HARDWARE_PAIRS = [
    ("manyfiles.dm41", "manyfiles-packed.dm41"),
    ("dm41x_pack_dotend.dm41", "dm41x_pack_dotend-packed.dm41"),
    ("lander.dm41", "lander-packed.dm41"),
    ("targ.dm41", "targ-packed.dm41"),
    ("twolabels.dm41", "twolabels-packed.dm41"),
]
HARDWARE_PACKED = [packed for _, packed in HARDWARE_PAIRS] + ["manyfiles-repacked.dm41"]

NUMBER_ENTRY_BYTES = range(0x10, 0x1D)


def program_area(memory) -> bytes:
    """Every byte from the oldest program's first byte down to the floor of
    the `.END.` register, in reading order."""
    status, programs = memory.status_registers, memory.programs
    top_addr = programs.addr_for(status.R00() - 1, 0)
    floor_addr = programs.addr_for(status.DotEnd(), 6)
    reg, offset = programs.pos_for(top_addr)
    return programs.read_bytes_forward(reg, offset, top_addr - floor_addr + 1)


def free_registers(memory) -> list:
    """Registers between the Alarms buffer and `.END.` -- program space
    that must read as untouched zeros."""
    return [
        memory.get_register(r).get_hex()
        for r in range(memory.alarms.end_exclusive, memory.status_registers.DotEnd())
    ]


def path(name):
    return DATA_DIR / name


# -- Real-hardware before/after pairs -----------------------------------------


@pytest.mark.parametrize("before_name,after_name", HARDWARE_PAIRS)
def test_pack_reproduces_a_real_hardware_pack(before_name, after_name):
    memory = load_fixture(path(before_name))
    hardware = load_fixture(path(after_name))
    registers_before = memory.status_registers.DotEnd()

    freed = memory.pack()

    assert memory.status_registers.DotEnd() == hardware.status_registers.DotEnd()
    assert freed == hardware.status_registers.DotEnd() - registers_before
    assert program_area(memory) == clear_jump_caches(program_area(hardware))


@pytest.mark.parametrize("before_name,after_name", HARDWARE_PAIRS)
def test_pack_leaves_no_stale_bytes_in_the_registers_it_frees(before_name, after_name):
    # A previous version left a stray END marker (c0 01 20) behind in the
    # space it handed back.
    memory = load_fixture(path(before_name))
    memory.pack()
    assert all(set(hex_) <= {"0"} for hex_ in free_registers(memory))


@pytest.mark.parametrize("packed_name", HARDWARE_PACKED)
def test_pack_is_a_no_op_on_a_file_a_real_pack_already_packed(packed_name):
    memory = load_fixture(path(packed_name))
    before_area = program_area(memory)
    before_dot_end = memory.status_registers.DotEnd()

    assert memory.pack() == 0

    assert memory.status_registers.DotEnd() == before_dot_end
    assert program_area(memory) == clear_jump_caches(before_area)


# -- The individual rules, across every fixture -------------------------------


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda p: p.name)
def test_pack_clears_every_jump_cache(fixture):
    memory = load_fixture(fixture)
    memory.pack()
    area = program_area(memory)
    assert clear_jump_caches(area) == area


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda p: p.name)
def test_pack_only_leaves_a_null_between_two_numbers(fixture):
    memory = load_fixture(fixture)
    memory.pack()
    area = program_area(memory)
    instructions = list(iter_instructions(area))
    # The zero bytes between the last END and the permanent .END. are
    # register-alignment padding, not NULL instructions.
    markers = scan_global_markers_forward(area)[:-1]
    last_marker_end = markers[-1]["index"] + 3 if markers else 0
    for position, (start, length) in enumerate(instructions):
        if area[start] != 0x00 or start >= last_marker_end:
            continue
        before = instructions[position - 1][0] if position else None
        after = instructions[position + 1][0] if position + 1 < len(instructions) else None
        assert before is not None and area[before] in NUMBER_ENTRY_BYTES, (
            f"{fixture.name}: NULL at byte {start} doesn't follow a number"
        )
        assert after is not None and area[after] in NUMBER_ENTRY_BYTES, (
            f"{fixture.name}: NULL at byte {start} isn't followed by a number"
        )


def test_pack_keeps_the_null_that_separates_two_numbers():
    # numtest.dm41 is real hardware's capture of "12345 <NULL> 67890".
    memory = load_fixture(path("numtest.dm41"))
    area_before = program_area(memory)
    memory.pack()
    area = program_area(memory)
    assert bytes([0x15, 0x00, 0x16]) in area
    assert bytes([0x15, 0x00, 0x16]) in area_before


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda p: p.name)
def test_pack_marks_every_end_packed_and_closes_with_an_empty_dot_end(fixture):
    memory = load_fixture(fixture)
    if not memory.programs.list_programs():
        pytest.skip("no programs")
    memory.pack()
    markers = [
        m for m in scan_global_markers_forward(program_area(memory)) if not m["is_label"]
    ]
    assert [m["third_byte"] for m in markers[:-1]] == [0x09] * (len(markers) - 1)
    assert markers[-1]["third_byte"] == 0x20  # the permanent .END.
    programs = memory.programs.list_programs()
    assert all(p.terminator == "END" for p in programs)


def test_pack_turns_a_dot_end_closed_program_into_end_plus_empty_dot_end():
    # twolabels.dm41's one program is closed by the permanent .END.
    # directly, flagged "needs packing" (2d) -- the same state as
    # dm41x_pack_dotend.dm41, which a real PACK turned into END + .END.
    memory = load_fixture(path("twolabels.dm41"))
    assert memory.programs.list_programs()[0].terminator == ".END."
    memory.pack()
    programs = memory.programs.list_programs()
    assert len(programs) == 1
    assert programs[0].terminator == "END"


# -- compact_program_stream(): the byte-level rules, in isolation -------------

from memory.opcode_scan import compact_program_stream  # noqa: E402


def test_compact_keeps_one_null_between_two_numbers_and_drops_the_rest():
    assert compact_program_stream(bytes([0x15, 0x00, 0x16])) == bytes([0x15, 0x00, 0x16])
    assert compact_program_stream(bytes([0x15, 0x00, 0x00, 0x00, 0x16])) == bytes(
        [0x15, 0x00, 0x16]
    )
    # Not between two numbers: gone.
    assert compact_program_stream(bytes([0x83, 0x00, 0x16])) == bytes([0x83, 0x16])
    assert compact_program_stream(bytes([0x15, 0x00, 0x83])) == bytes([0x15, 0x83])
    assert compact_program_stream(bytes([0x00, 0x00, 0x83, 0x00])) == bytes([0x83])


def test_compact_never_touches_a_zero_that_is_part_of_an_instruction():
    fix_0 = bytes([0x9C, 0x00])  # FIX 0
    isg_00 = bytes([0x96, 0x00])  # ISG 00
    text = bytes([0xF3, 0x41, 0x00, 0x42])  # a 3-character ALPHA string containing a 00 byte
    assert compact_program_stream(fix_0 + isg_00 + text) == fix_0 + isg_00 + text


def test_compact_clears_jump_caches_but_keeps_the_label_being_jumped_to():
    # Compact GTO 01 / GTO 00: second byte is the cached distance.
    assert compact_program_stream(bytes([0xB2, 0xB1, 0xB1, 0x88])) == bytes(
        [0xB2, 0x00, 0xB1, 0x00]
    )
    # General 3-byte GTO/XEQ: the first byte's low nibble, all of the
    # second byte, and the direction bit of the third are the cache; the
    # third byte's low 7 bits are the label.
    assert compact_program_stream(bytes([0xE4, 0x22, 0x04])) == bytes([0xE0, 0x00, 0x04])
    assert compact_program_stream(bytes([0xE0, 0x00, 0x9E])) == bytes([0xE0, 0x00, 0x1E])
    assert compact_program_stream(bytes([0xD3, 0x45, 0x89])) == bytes([0xD0, 0x00, 0x09])


def test_compact_copies_markers_verbatim():
    label = bytes([0xC0, 0x00, 0xF6, 0x01, 0x58, 0x4D, 0x42, 0x43, 0x44])  # LBL "XMBCD"
    end = bytes([0xC4, 0x0E, 0x09])
    assert compact_program_stream(label + bytes([0x00, 0x83]) + end) == label + bytes([0x83]) + end


# -- More real-hardware evidence -----------------------------------------------


def test_a_real_pack_of_an_already_packed_file_changes_nothing_in_program_memory():
    # manyfiles-packed.dm41 was loaded on a DM41L and PACKed again
    # (manyfiles-repacked.dm41). Every program byte came back identical --
    # including PURXM's cached jump (b1 91), which pack() clears. So a real
    # PACK only rewrites a jump cache when something moved.
    packed = load_fixture(path("manyfiles-packed.dm41"))
    repacked = load_fixture(path("manyfiles-repacked.dm41"))
    assert program_area(repacked) == program_area(packed)
    assert repacked.status_registers.DotEnd() == packed.status_registers.DotEnd()


def test_twolabels_costs_one_register_exactly_like_the_real_pack_did():
    # twolabels.dm41 (one program, closed by .END. directly, flagged "needs
    # packing") -> a real PACK on a DM41L: END + empty .END., .END. one
    # register lower. Byte-for-byte what pack() produces, jump caches
    # included (this program has none).
    memory = load_fixture(path("twolabels.dm41"))
    hardware = load_fixture(path("twolabels-packed.dm41"))
    assert memory.pack() == -1
    assert program_area(memory) == program_area(hardware)
    assert memory.status_registers.DotEnd() == hardware.status_registers.DotEnd()


# Explorer-produced output (Tools > Pack Memory) that was then loaded on a
# real DM41L and checked: the programs still run. The "before" dumps are
# calculator captures (packed-test.dm41 is the dump of a calculator right
# after it ran the program, so its GTO/XEQ jump caches are real).
EXPLORER_PACKED_AND_VERIFIED = [
    ("packed-test.dm41", "repacked-test.dm41"),
    ("nulltest.dm41", "nulltest-2.dm41"),
]


@pytest.mark.parametrize("before_name,after_name", EXPLORER_PACKED_AND_VERIFIED)
def test_pack_output_that_was_verified_to_run_on_a_calculator(before_name, after_name):
    memory = load_fixture(path(before_name))
    expected = load_fixture(path(after_name))
    assert memory.pack() == 0
    assert program_area(memory) == program_area(expected)


def test_pack_clears_a_real_forward_xeq_and_backward_gto_cache():
    # packed-test.dm41's program: "XEQ 03" (forward, 3-byte form e2 02 03)
    # and "GTO 00" (backward, compact form b1 e0), both resolved by the
    # calculator while it ran the program.
    before = program_area(load_fixture(path("packed-test.dm41")))
    after = program_area(load_fixture(path("repacked-test.dm41")))
    assert bytes([0xE2, 0x02, 0x03]) in before and bytes([0xE0, 0x00, 0x03]) in after
    assert bytes([0xB1, 0xE0]) in before and bytes([0xB1, 0x00]) in after


def test_pack_keeps_every_null_that_separates_two_numbers():
    # nulltest.dm41's program BBB is the numbers 5, 7, 123, 456 and 789,
    # each pair separated by a NULL: 15 00 17 00 11 12 13 00 14 15 16 00 17 18 19.
    memory = load_fixture(path("nulltest.dm41"))
    separated = bytes(
        [0x15, 0x00, 0x17, 0x00, 0x11, 0x12, 0x13, 0x00, 0x14, 0x15, 0x16, 0x00, 0x17, 0x18, 0x19]
    )
    assert separated in program_area(memory)
    memory.pack()
    assert separated in program_area(memory)
