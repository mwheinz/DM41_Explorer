"""
Tests for extended memory on a model with more than two XM regions (the
DM41X's third region, 0x301-0x3EF), and for the DeviceProfile that carries
each model's region list.

tests/data/dm41x_manyfiles.dm41 is a real DM41X dump with extended memory
almost completely full: 59 files, two of which span a region boundary (one
from region 0 into 1, one from 1 into 2), leaving only 7 free registers in
region 2. The walk order, the end-of-directory marker and all three region
pointer registers were decoded from it by hand before any of this code
existed, and the expectations below are those decoded facts. If the fixture
is ever regenerated, update the FIXTURE FACTS block.
"""

from pathlib import Path

import pytest

from memory import (
    Memory,
    DM41L,
    DM41X,
    DeviceProfile,
    DM41MemoryError,
    Register,
    XM_REGIONS,
    EOM_REGISTER_HEX,
)

DATA_DIR = Path(__file__).parent / "data"
DM41X_DUMP = DATA_DIR / "dm41x_manyfiles.dm41"

# -- FIXTURE FACTS (decoded from dm41x_manyfiles.dm41) ---------------------
FILE_COUNT = 59
FIRST_FILE = "XM0.   "
LAST_FILE = "XM46.  "
EOM_ADDR = 0x309  # the FF-filled end-of-directory register, in region 2

# The three region pointer registers, exactly as the real DM41X wrote them.
R40 = "0003c03c2ef0bf"  # WW=0x3c, PP=0x3c, NNN=0x2ef, TTT=0x0bf
R201 = "000000403ef2ef"  # WW=0, back-link 0x040, NNN=0x3ef, TTT=0x2ef
R301 = "000002010003ef"  # WW=0, back-link 0x201, NNN=0 (last), TTT=0x3ef
# ---------------------------------------------------------------------------

# Region 2's pointer register sits at 0x301, so the lowest address a new
# directory terminator can occupy is 0x302. A file whose name register is at
# EOM_ADDR with N data registers puts its terminator at EOM_ADDR - N - 2, so
# the largest N that still fits is:
MAX_FREE_DATA_REGISTERS = EOM_ADDR - (0x301 + 3)


def _x_memory() -> Memory:
    return Memory.from_file(DM41X_DUMP, profile=DM41X)


def _hex(memory: Memory, addr: int) -> str:
    return memory.get_register(addr).get_hex()


def _snapshot(xm) -> list:
    """Everything about each file that must survive a rebuild."""
    return [
        (f.name_bytes, f.file_type, [r.get_hex() for r in f.data_registers()])
        for f in xm.list_files()
    ]


# -- DeviceProfile -----------------------------------------------------------


def test_profiles_describe_the_regions_of_each_model():
    assert DM41L.xm_regions == ((0x40, 0xBF), (0x201, 0x2EF))
    assert DM41X.xm_regions == ((0x40, 0xBF), (0x201, 0x2EF), (0x301, 0x3EF))
    # XM_REGIONS stays as the DM41L's list for existing callers.
    assert XM_REGIONS == list(DM41L.xm_regions)


def test_profile_derived_sizes():
    assert DM41L.xm_raw_registers == 365
    assert DM41X.xm_raw_registers == 603
    assert DM41L.display_end == 0x2EF
    assert DM41X.display_end == 0x3EF
    assert DM41X.xm_address_range == (0x40, 0x3EF)


@pytest.mark.parametrize(
    "regions",
    [
        (),
        ((0x40, 0x40),),  # no usable register
        ((0x201, 0x2EF), (0x40, 0xBF)),  # not ascending
        ((0x40, 0xBF), (0xB0, 0x2EF)),  # overlapping
    ],
)
def test_profile_rejects_bad_regions(regions):
    with pytest.raises(ValueError):
        DeviceProfile("bad", regions)


def test_memory_defaults_to_the_dm41l_profile():
    assert Memory().profile is DM41L
    assert Memory(profile=DM41X).profile is DM41X


def test_profile_is_part_of_memory_equality():
    assert Memory(profile=DM41X) != Memory(profile=DM41L)
    assert Memory(profile=DM41X) == Memory(profile=DM41X)


# -- Reading the real three-region dump --------------------------------------


def test_dm41x_dump_lists_all_files():
    files = _x_memory().extended_memory.list_files()
    assert len(files) == FILE_COUNT
    assert files[0].name == FIRST_FILE
    assert files[-1].name == LAST_FILE
    # Names are unique.
    assert len({f.name_bytes for f in files}) == FILE_COUNT


def test_dm41x_dump_files_use_all_three_regions():
    memory = _x_memory()
    regions = DM41X.xm_regions
    used = set()
    for f in memory.extended_memory.list_files():
        for start, end in f.segments:
            if end < start:
                continue
            for i, (lo, hi) in enumerate(regions):
                if lo < start <= hi:
                    assert end <= hi, f"{f.name}: segment crosses a region ceiling"
                    used.add(i)
    assert used == {0, 1, 2}


def test_dm41x_dump_has_exactly_two_spanning_files():
    files = _x_memory().extended_memory.list_files()
    spanning = {f.name.strip(): f for f in files if f.spans_regions}
    assert set(spanning) == {"XMA2.", "XMA16."}
    assert spanning["XMA2."].segments == [[0x41, 0x45], [0x2ED, 0x2EF]]
    assert spanning["XMA16."].segments == [[0x202, 0x204], [0x3EB, 0x3EF]]
    # A spanning file still has exactly its declared number of registers.
    for f in spanning.values():
        assert f.num_registers == f.declared_length == 8


def test_dm41x_dump_directory_ends_at_the_eom_marker():
    memory = _x_memory()
    files = memory.extended_memory.list_files()
    last = files[-1]
    assert _hex(memory, last.data_start - 1) == EOM_REGISTER_HEX
    assert last.data_start - 1 == EOM_ADDR


def test_dm41x_dump_files_do_not_overlap():
    files = _x_memory().extended_memory.list_files()
    claimed = {}
    for f in files:
        addrs = [f.header_addr, f.header_addr + 1]  # header + name registers
        for start, end in f.segments:
            addrs.extend(range(start, end + 1))
        for a in addrs:
            assert a not in claimed, f"0x{a:x}: {f.name} overlaps {claimed[a]}"
            claimed[a] = f.name


def test_dm41x_dump_file_contents_decode():
    files = _x_memory().extended_memory.list_files()
    by_name = {f.name.strip(): f for f in files}
    # The file that crosses from region 1 into region 2 reads as one
    # continuous record stream.
    assert by_name["XMA16."].get_records() is not None
    assert len(by_name["XMA16."].data_registers()) == 8
    assert len(by_name["XMA2."].data_registers()) == 8


def test_dm41x_dump_loaded_as_a_dm41l_is_a_clean_error_not_an_indexerror():
    memory = Memory.from_file(DM41X_DUMP)  # default DM41L profile
    with pytest.raises(DM41MemoryError, match="past the last XM region"):
        memory.extended_memory.list_files()


def test_dm41x_dump_round_trips_through_text():
    memory = _x_memory()
    again = Memory.from_string(memory.to_string(), profile=DM41X)
    assert again == memory
    assert _snapshot(again.extended_memory) == _snapshot(memory.extended_memory)


# -- Region pointer registers ------------------------------------------------


def test_dm41x_dump_pointer_registers_are_as_decoded():
    memory = _x_memory()
    assert _hex(memory, 0x40) == R40
    assert _hex(memory, 0x201) == R201
    assert _hex(memory, 0x301) == R301


def test_build_region_pointer_reproduces_the_real_registers():
    xm = _x_memory().extended_memory
    build = xm._build_region_pointer  # pylint: disable=protected-access
    assert build(0, next_region_active=True, ww=0x3C, pp=0x3C).get_hex() == R40
    assert build(1, next_region_active=True, ww=0, pp=0x40).get_hex() == R201
    assert build(2, next_region_active=False, ww=0, pp=0x201).get_hex() == R301


def test_build_region_pointer_keeps_both_ww_digits():
    # tests/data/manyfiles.dm41 (a DM41L dump) has WW=0x1f in region 0.
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    build = memory.extended_memory._build_region_pointer  # pylint: disable=protected-access
    assert (
        build(0, next_region_active=True, ww=0x1F, pp=0x1F).get_hex()
        == _hex(memory, 0x40)
    )


# -- Memory.regions() --------------------------------------------------------


def test_regions_lists_every_xm_span_for_the_dm41x():
    spans = _x_memory().regions()
    xm = [(s.start, s.end) for s in spans if s.key == "xm"]
    assert xm == [(0x40, 0xBF), (0x200, 0x2EF), (0x300, 0x3EF)]


def test_regions_for_the_dm41l_is_unchanged():
    spans = Memory().regions()
    xm = [(s.start, s.end) for s in spans if s.key == "xm"]
    assert xm == [(0x40, 0xBF), (0x200, 0x2EF)]


def test_region_for_finds_the_third_region_and_the_gap():
    memory = _x_memory()
    assert memory.region_for(0x3A0).key == "xm"
    assert memory.region_for(0x301).key == "xm"
    # 0x2F0-0x2FF belongs to no region on a DM41X.
    assert memory.region_for(0x2F8) is None


# -- Writing ------------------------------------------------------------------


def test_add_file_uses_the_free_space_at_the_bottom_of_region_two():
    memory = _x_memory()
    xm = memory.extended_memory
    before = xm.list_files()

    added = xm.add_file("NEWFILE", xm.TYPE_DATA, numbers=[2.5] * 3)

    files = xm.list_files()
    assert len(files) == len(before) + 1
    assert files[-1].name_bytes == added.name_bytes
    assert files[-1].get_numbers() == [2.5] * 3
    assert not files[-1].spans_regions
    # Existing files and the pointer registers are untouched.
    assert _snapshot(xm)[:-1] == [
        (f.name_bytes, f.file_type, [r.get_hex() for r in f.data_registers()])
        for f in before
    ]
    assert (_hex(memory, 0x40), _hex(memory, 0x201), _hex(memory, 0x301)) == (
        R40, R201, R301,
    )
    # And the new directory still ends in an EOM marker.
    assert _hex(memory, files[-1].data_start - 1) == EOM_REGISTER_HEX


def test_add_file_exactly_fills_then_overflows_the_last_region():
    # The dump has only a handful of free registers left in region 2 (see
    # MAX_FREE_DATA_REGISTERS). The largest file that still leaves a usable
    # address, above the region's pointer register, for the directory
    # terminator must fit...
    n = MAX_FREE_DATA_REGISTERS
    assert n > 0
    xm = _x_memory().extended_memory
    xm.add_file("FITS", xm.TYPE_DATA, numbers=[1.0] * n)
    files = xm.list_files()
    assert len(files) == FILE_COUNT + 1
    assert files[-1].get_numbers() == [1.0] * n

    # ...and one register more must be rejected before any write.
    memory = _x_memory()
    xm = memory.extended_memory
    text_before = memory.to_string()
    with pytest.raises(DM41MemoryError, match="Not enough free space"):
        xm.add_file("TOOBIG", xm.TYPE_DATA, numbers=[1.0] * (n + 1))
    assert memory.to_string() == text_before  # rejected before any write


def test_one_file_can_span_all_three_regions():
    memory = Memory(profile=DM41X)
    xm = memory.extended_memory
    numbers = [float(i) for i in range(370)]

    added = xm.add_file("HUGE", xm.TYPE_DATA, numbers=numbers)

    assert [len(added.segments)] == [3]
    files = xm.list_files()
    assert len(files) == 1
    assert files[0].segments == added.segments
    assert files[0].get_numbers() == numbers
    # Every region is initialised, chained forward by NNN, and back-linked.
    assert _hex(memory, 0x40) == "000010002ef0bf"  # ww=1, pp=0 (bootstrap)
    assert _hex(memory, 0x201) == R201
    assert _hex(memory, 0x301) == R301


def test_a_dm41l_rejects_the_same_file():
    xm = Memory().extended_memory  # DM41L: 362 registers available
    with pytest.raises(DM41MemoryError, match="Not enough free space"):
        xm.add_file("HUGE", xm.TYPE_DATA, numbers=[1.0] * 370)


def test_first_file_into_each_new_region_chains_the_pointers_later():
    memory = Memory(profile=DM41X)
    xm = memory.extended_memory

    xm.add_file("A", xm.TYPE_DATA, numbers=[1.0] * 150)  # spans 0 -> 1
    assert _hex(memory, 0x40)[8:11] == "2ef"  # NNN: region 1 in use
    assert _hex(memory, 0x201) != "00" * 7
    assert _hex(memory, 0x201)[8:11] == "000"  # region 2 not in use yet
    assert _hex(memory, 0x301) == "00" * 7

    xm.add_file("B", xm.TYPE_DATA, numbers=[2.0] * 100)  # stays in region 1
    assert _hex(memory, 0x301) == "00" * 7

    xm.add_file("C", xm.TYPE_DATA, numbers=[3.0] * 150)  # spills into region 2
    assert _hex(memory, 0x201)[8:11] == "3ef"  # NNN patched in
    assert _hex(memory, 0x301) == R301
    assert [f.name.strip() for f in xm.list_files()] == ["A", "B", "C"]


def test_patching_nnn_leaves_ww_and_pp_alone():
    # A pointer whose WW needs both digits (as in manyfiles.dm41): the older
    # code rebuilt the register from a one-digit WW when it patched NNN, and
    # silently turned 0x1f into 0x0f.
    memory = Memory()
    xm = memory.extended_memory
    xm.add_file("A", xm.TYPE_DATA, numbers=[1.0] * 3)  # region 0 only
    raw = bytearray(memory.get_register(0x40).get_bytes())
    raw[1], raw[2], raw[3] = 0x01, 0xF0, 0x1F  # WW=0x1f, PP=0x1f
    memory.set_register(0x40, Register(data=bytes(raw)))

    xm.add_file("B", xm.TYPE_DATA, numbers=[2.0] * 200)  # first use of region 1

    r40 = _hex(memory, 0x40)
    assert r40[3:5] == "1f" and r40[6:8] == "1f"
    assert r40[8:11] == "2ef"


def test_remove_file_rebuilds_a_three_region_directory():
    memory = _x_memory()
    xm = memory.extended_memory
    before = _snapshot(xm)
    victim = next(f for f in xm.list_files() if f.name.strip() == "XMA2.")

    xm.remove_file(victim.header_addr)

    after = _snapshot(xm)
    assert after == [entry for entry in before if entry[0].decode().strip() != "XMA2."]
    assert len(after) == len(before) - 1
    # Survivors were re-packed; the spanning file XMA16 is still intact and
    # the directory still ends in an EOM marker.
    files = xm.list_files()
    assert _hex(memory, files[-1].data_start - 1) == EOM_REGISTER_HEX
    again = Memory.from_string(memory.to_string(), profile=DM41X)
    assert _snapshot(again.extended_memory) == after
