"""
Tests for the memory region objects (memory/regions.py and the per-region
modules built on it).

The point of this module is the property the whole region refactor rests
on: a region instance is a LIVE view, so its boundaries follow edits that
move it rather than freezing at the moment it was handed out. An earlier
generation of these classes cached `address_range` at construction time
and had to be deleted for exactly that reason (GitHub issue #25); these
tests exist so it can't quietly come back.
"""

from pathlib import Path

import pytest

from memory import (
    Memory,
    Register,
    RegionSpan,
    StatusRegisters,
    KeyAssignments,
    Alarms,
    FreeSpace,
    ProgramMemory,
    DataMemory,
    ExtendedMemory,
    KEY_ASSIGNMENTS_RANGE,
    PRIMARY_DATA_END,
    STATUS_REGISTERS_RANGE,
)

DATA_DIR = Path(__file__).parent / "data"


def unpartitioned_memory() -> Memory:
    """A Memory with no meaningful R00/.END. partition at all.

    Note this is NOT what a bare `Memory()` is: the constructor seeds
    register c from a real "Memory Lost" state, which already carries a
    sane R00 (0x19c) and `.END.` (0x19b). Zeroing register c is what
    actually produces the corrupt/never-loaded state the program and data
    regions are supposed to report themselves empty for.
    """
    memory = Memory()
    memory.set_register(StatusRegisters.REG_C_ADDR, Register(size=7))
    return memory


# --- Region lookup ----------------------------------------------------


def test_memory_hands_out_a_region_for_every_key():
    memory = Memory()
    expected = {
        "status": StatusRegisters,
        "key": KeyAssignments,
        "alarms": Alarms,
        "unused": FreeSpace,
        "program": ProgramMemory,
        "data": DataMemory,
        "xm": ExtendedMemory,
    }
    for key, cls in expected.items():
        assert isinstance(memory.region(key), cls), key
        assert memory.region(key).key == key


def test_region_lookup_is_stable_across_calls():
    """The same instance comes back every time -- callers can hold on to
    one (that's the whole point of live boundaries) without every lookup
    allocating a fresh object."""
    memory = Memory()
    assert memory.region("program") is memory.programs
    assert memory.region("program") is memory.region("program")
    assert memory.region("key") is memory.key_assignments


def test_unknown_region_key_raises():
    with pytest.raises(KeyError):
        Memory().region("nope")


def test_named_properties_match_region_keys():
    memory = Memory()
    assert memory.status_registers is memory.region("status")
    assert memory.key_assignments is memory.region("key")
    assert memory.alarms is memory.region("alarms")
    assert memory.free_space is memory.region("unused")
    assert memory.programs is memory.region("program")
    assert memory.data_memory is memory.region("data")
    assert memory.extended_memory is memory.region("xm")


# --- Live boundaries --------------------------------------------------


def test_key_assignments_region_grows_and_shrinks_in_place():
    """One region instance, held across three edits -- its extent has to
    follow the edits, not the moment it was fetched."""
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    keys = memory.key_assignments

    assert keys.is_empty
    assert keys.start == KEY_ASSIGNMENTS_RANGE[0]
    assert keys.end == keys.start - 1  # empty regions end just below start
    assert keys.count == 0

    memory.key_assignments.set_assignment(11, False, 0x40)
    assert keys.count == 1  # same object, new extent
    assert keys.end == keys.start

    # Two entries pack into one register; the third needs a second one.
    memory.key_assignments.set_assignment(12, False, 0x41)
    assert keys.count == 1
    memory.key_assignments.set_assignment(13, False, 0x42)
    assert keys.count == 2

    memory.key_assignments.delete_assignment(13, False)
    assert keys.count == 1
    memory.key_assignments.delete_assignment(12, False)
    memory.key_assignments.delete_assignment(11, False)
    assert keys.is_empty


def test_alarms_region_follows_key_assignments_upward():
    """The alarms buffer always starts exactly where key assignments end,
    with no gap -- so growing key assignments moves the alarms region even
    though nothing touched it directly."""
    memory = Memory.from_file(DATA_DIR / "alarmtest.dm41")
    keys, alarms = memory.key_assignments, memory.alarms

    before_start = alarms.start
    before_count = alarms.count
    assert before_count > 0, "fixture is supposed to have alarms in it"
    assert alarms.start == keys.end_exclusive

    # Two entries fit per register, so assign until the region is
    # provably one register wider than it started.
    before_keys = keys.count
    for key_number in (11, 12, 13, 14, 15):
        memory.key_assignments.set_assignment(key_number, False, 0x40)
        if keys.count > before_keys:
            break
    assert keys.count > before_keys, "key assignments never grew"

    assert alarms.start == keys.end_exclusive
    assert alarms.start > before_start  # pushed up by the new KA register
    assert alarms.count == before_count  # same buffer, just relocated
    assert alarms.get_register(alarms.start).get_bytes()[0] == Alarms.HEADER_MARKER


def test_program_and_data_regions_follow_r00():
    """Moving the R00 partition marker resizes both neighbours at once,
    with no rescan or invalidation step in between."""
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    programs, data = memory.programs, memory.data_memory

    r00 = memory.status_registers.R00()
    assert programs.end == r00 - 1
    assert data.start == r00
    assert data.end == PRIMARY_DATA_END
    assert data.count == PRIMARY_DATA_END - r00 + 1

    # Moved down far enough to matter, but still above `.END.` -- below
    # that the partition stops being a partition at all (see
    # test_program_and_data_regions_are_empty_without_a_partition).
    moved = r00 - 4
    memory.status_registers.set_R00(moved)

    assert programs.end == moved - 1
    assert data.start == moved
    assert data.count == PRIMARY_DATA_END - moved + 1


def test_program_region_follows_dot_end():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    programs = memory.programs
    assert programs.start == memory.status_registers.DotEnd()

    memory.status_registers.set_DotEnd(programs.start - 4)
    assert programs.start == memory.status_registers.DotEnd()
    assert programs.count > 0


def test_free_space_shrinks_as_programs_grow():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    free = memory.free_space
    before = free.count

    memory.status_registers.set_DotEnd(memory.status_registers.DotEnd() - 5)

    assert free.count == before - 5
    assert free.end == memory.programs.start - 1


# --- Empty / no-partition behavior ------------------------------------


def test_program_and_data_regions_are_empty_without_a_partition():
    """A state whose register c decodes to nonsense has no partition;
    rather than inventing a span from it, both regions report themselves
    empty and no address matches them."""
    memory = unpartitioned_memory()
    assert not memory.has_program_partition()
    assert memory.programs.is_empty
    assert memory.data_memory.is_empty
    assert memory.programs.count == 0  # never negative
    assert memory.data_memory.count == 0
    for addr in (0x00, 0xC0, 0x150, PRIMARY_DATA_END):
        assert addr not in memory.programs
        assert addr not in memory.data_memory
    assert list(memory.programs) == []
    # Free space then covers everything above the alarms buffer.
    assert memory.free_space.end == PRIMARY_DATA_END


def test_empty_region_iteration_and_len():
    memory = Memory()
    keys = memory.key_assignments
    assert keys.is_empty
    assert len(keys) == 0
    assert list(keys) == []
    assert keys.registers() == {}
    assert KEY_ASSIGNMENTS_RANGE[0] not in keys


# --- Register access --------------------------------------------------


def test_region_register_access_is_bounded():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    status = memory.status_registers

    assert status.address_range == STATUS_REGISTERS_RANGE
    assert status.get_register(0x0D) == memory.get_register(0x0D)

    with pytest.raises(ValueError):
        status.get_register(0x10)
    with pytest.raises(ValueError):
        status.set_register(0x10, Register(size=7))


def test_region_write_is_visible_through_memory():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    data = memory.data_memory
    addr = data.start + 3

    data.set_register(addr, Register.from_hex("00000000000042"))
    assert memory.get_register(addr).get_hex() == "00000000000042"
    assert data.get(3).get_hex() == "00000000000042"
    assert data.number_for(addr) == 3
    assert data.address_for(3) == addr


def test_data_memory_rejects_out_of_partition_register_numbers():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    data = memory.data_memory
    with pytest.raises(ValueError):
        data.address_for(data.count)
    with pytest.raises(ValueError):
        data.address_for(-1)
    with pytest.raises(ValueError):
        unpartitioned_memory().data_memory.address_for(0)


def test_region_clear_zeroes_only_its_own_registers():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    data = memory.data_memory
    below = data.start - 1
    memory.set_register(below, Register.from_hex("11111111111111"))
    memory.set_register(data.start, Register.from_hex("22222222222222"))

    data.clear()

    assert memory.get_register(data.start).get_hex() == "00" * 7
    assert memory.get_register(below).get_hex() == "11111111111111"


# --- Spans (immutable snapshots) --------------------------------------


def test_span_is_a_frozen_snapshot_while_the_region_moves_on():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    keys = memory.key_assignments
    span = keys.span()

    assert isinstance(span, RegionSpan)
    assert (span.key, span.start, span.end) == (keys.key, keys.start, keys.end)

    memory.key_assignments.set_assignment(11, False, 0x40)

    assert keys.count == 1, "the region followed the edit"
    assert span.count == 0, "the snapshot did not"


def test_regions_list_covers_the_whole_display_range_without_gaps():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    spans = memory.regions()
    for addr in range(0x000, 0x2F0):
        matches = [s for s in spans if addr in s]
        assert len(matches) == 1, f"0x{addr:03x} matched {matches}"
        assert memory.region_for(addr).key == matches[0].key


def test_regions_omits_program_and_data_without_a_partition():
    keys = {s.key for s in unpartitioned_memory().regions()}
    assert "program" not in keys
    assert "data" not in keys
    assert "unused" in keys
