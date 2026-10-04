"""
Tests for Memory.remove_program() -- GitHub issue #6 ("add the ability to
remove programs"; Export/Import already covered "add"/"edit", see
test_program_export.py/test_program_import.py, this is the last of the
three). Also exercises the shared _rebuild_program_memory() helper this
leans on (see test_pack.py for its other caller, Memory.pack()).

Ground truth throughout is the same real fixtures the rest of the program-
memory test suite already validates against: 6x-xm.dm41 (three real
programs -- XMBCD/XMALPHA/PURXM, oldest to newest), simple.dm41 (one
program, APPTEST), and global-key-assignments.dm41 (two single-label
programs, AAA and BBB, each with its own global-label key assignment --
sec 4.6).
"""

from pathlib import Path

import pytest

from memory import Memory, DM41MemoryError, Program

DATA_DIR = Path(__file__).parent / "data"


def _names_lengths(programs):
    return [(p.names_label, p.length) for p in programs]


# -- Removing from each position in a multi-program chain -------------------


def test_remove_oldest_program_keeps_the_rest_intact():
    memory = Memory.from_file(DATA_DIR / "6x-xm.dm41")
    programs = memory.programs.list_programs()
    assert _names_lengths(programs) == [
        ("XMBCD", 61), ("XMALPHA", 51), ("PURXM", 20),
    ]

    memory.programs.remove_program(programs[0])

    after = memory.programs.list_programs()
    assert _names_lengths(after) == [("XMALPHA", 51), ("PURXM", 20)]
    # R00 (the data-register boundary) is never touched by a program
    # removal -- only the program/free-memory split below it moves.
    assert memory.status_registers.R00() == 0x19C


def test_remove_newest_program_leaves_the_new_newest_dot_end_terminated():
    memory = Memory.from_file(DATA_DIR / "6x-xm.dm41")
    programs = memory.programs.list_programs()

    memory.programs.remove_program(programs[-1])

    after = memory.programs.list_programs()
    assert _names_lengths(after) == [("XMBCD", 61), ("XMALPHA", 51)]
    # The new newest program is register-aligned in this fixture, so it
    # collapses back to being .END.-terminated directly (no wasted
    # register) -- see _collapse_trailing_end_into_dot_end()'s docstring
    # for when this optimization can and can't apply.
    assert after[-1].terminator == ".END."


def test_remove_middle_program_keeps_the_others_at_their_own_lengths():
    memory = Memory.from_file(DATA_DIR / "6x-xm.dm41")
    programs = memory.programs.list_programs()

    memory.programs.remove_program(programs[1])  # XMALPHA

    after = memory.programs.list_programs()
    assert _names_lengths(after) == [("XMBCD", 61), ("PURXM", 20)]


def test_remove_the_only_program_leaves_program_memory_genuinely_empty():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    programs = memory.programs.list_programs()
    assert len(programs) == 1

    memory.programs.remove_program(programs[0])

    assert memory.programs.list_programs() == []
    assert memory.programs.list_global_chain() == []
    # .END. collapses all the way back to R00 -- list_global_chain()'s
    # own definition of "no programs at all yet".
    assert memory.status_registers.DotEnd() == memory.status_registers.R00()


# -- Content integrity of what's left behind ---------------------------------


def test_remove_does_not_alter_the_bytes_of_programs_further_from_the_gap():
    # Removing the oldest program only has to relink whatever was
    # immediately newer than it (its own predecessor link); anything
    # further away in the chain should come out byte-for-byte identical.
    memory = Memory.from_file(DATA_DIR / "6x-xm.dm41")
    programs = memory.programs.list_programs()
    purxm_before = memory.programs.get_program_bytes(programs[2])

    memory.programs.remove_program(programs[0])

    after = memory.programs.list_programs()
    purxm_after = memory.programs.get_program_bytes(after[-1])
    assert after[-1].names_label == "PURXM"
    assert purxm_after == purxm_before


def test_remove_round_trips_through_to_string_and_from_string():
    memory = Memory.from_file(DATA_DIR / "6x-xm.dm41")
    programs = memory.programs.list_programs()
    memory.programs.remove_program(programs[1])
    expected = _names_lengths(memory.programs.list_programs())

    reloaded = Memory.from_string(memory.to_string())
    assert _names_lengths(reloaded.programs.list_programs()) == expected


# -- Key assignments (sec 4.6) -----------------------------------------------


def test_remove_clears_the_removed_programs_own_key_assignment_flag():
    memory = Memory.from_file(DATA_DIR / "global-key-assignments.dm41")
    programs = memory.programs.list_programs()
    aaa = next(p for p in programs if p.names_label == "AAA")
    bbb = next(p for p in programs if p.names_label == "BBB")
    assert aaa.labels[0].key_assignment != 0
    assert bbb.labels[0].key_assignment != 0

    aaa_key_number, aaa_shifted = memory.key_assignments.key_number_for_byte(
        aaa.labels[0].key_assignment
    )
    bbb_key_byte = bbb.labels[0].key_assignment
    bbb_key_number, bbb_shifted = memory.key_assignments.key_number_for_byte(bbb_key_byte)
    assert memory.key_assignments.get_key_flag(aaa_key_number, aaa_shifted) is True

    memory.programs.remove_program(aaa)

    # AAA's own key is now genuinely unassigned -- flag cleared, and
    # nothing decodes to it anymore.
    assert memory.key_assignments.get_key_flag(aaa_key_number, aaa_shifted) is False
    assert memory.programs.get_program_for_key(aaa_key_number, aaa_shifted) is None

    # BBB, the surviving program, keeps its own key assignment exactly.
    bbb_after = memory.programs.list_programs()[0]
    assert bbb_after.names_label == "BBB"
    assert bbb_after.labels[0].key_assignment == bbb_key_byte
    assert memory.key_assignments.get_key_flag(bbb_key_number, bbb_shifted) is True
    looked_up = memory.programs.get_program_for_key(bbb_key_number, bbb_shifted)
    assert looked_up is not None and looked_up.name == "BBB"


def test_remove_leaves_key_assignment_registers_alone():
    # The *other* key-assignment storage mechanism (sec 4.1/4.2, ASN to a
    # built-in function rather than a program) has nothing to do with
    # program memory at all -- removing a program shouldn't touch it.
    memory = Memory.from_file(DATA_DIR / "keyassigns.dm41")
    before = memory.key_assignments.decode_entries()
    programs = memory.programs.list_programs()
    if not programs:
        pytest.skip("keyassigns.dm41 has no programs to remove")
    memory.programs.remove_program(programs[0])
    assert memory.key_assignments.decode_entries() == before


# -- Error handling -----------------------------------------------------------


def test_remove_raises_for_a_program_not_in_the_current_list():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    bogus = Program(
        start_addr=0x000, start_offset=0, length=0, labels=[], terminator="END"
    )
    with pytest.raises(ValueError):
        memory.programs.remove_program(bogus)


def test_remove_raises_when_program_memory_is_already_empty():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    only = memory.programs.list_programs()[0]
    memory.programs.remove_program(only)
    with pytest.raises(ValueError):
        memory.programs.remove_program(only)


def test_remove_rejects_on_a_buffer_with_no_valid_partition():
    memory = Memory()
    memory.status_registers.set_R00(0)
    bogus = Program(
        start_addr=0x000, start_offset=0, length=0, labels=[], terminator="END"
    )
    with pytest.raises(ValueError):
        memory.programs.remove_program(bogus)


# -- Regression sweep ---------------------------------------------------------


def test_remove_every_program_in_every_sample_dump_one_at_a_time():
    """For every real program in every sample dump, removing it should
    never raise, should reduce the program count by exactly one, and
    should leave every other program's own name/length exactly as it
    was."""
    for path in sorted(DATA_DIR.glob("*.dm41")):
        memory = Memory.from_file(path)
        programs = memory.programs.list_programs()
        for target in programs:
            fresh = Memory.from_file(path)
            fresh_programs = fresh.programs.list_programs()
            # Match target by position, same convention get_program_bytes()
            # itself uses.
            match = next(
                p for p in fresh_programs
                if p.start_addr == target.start_addr
                and p.start_offset == target.start_offset
            )
            expected_remaining = [
                (p.names_label, p.length) for p in fresh_programs if p is not match
            ]
            fresh.programs.remove_program(match)
            after = fresh.programs.list_programs()
            assert len(after) == len(fresh_programs) - 1, path.name
            assert _names_lengths(after) == expected_remaining, path.name
