"""
Tests for importing a standalone HP-41 program (as decoded by
program_files.decode_program_raw()/decode_program_dat(), or straight from
Memory.get_program_bytes()) into a live Memory as the newest program --
Memory.import_program() (memory/memory.py), built on the byte-level chain
parsing in memory/program_chain.py (see test_program_chain.py).

This is the write-side counterpart to test_program_export.py: rather than
reading a program's bytes back out, these tests splice bytes *in* and then
re-read them via list_programs()/get_program_bytes() to confirm the result
is indistinguishable from a program a real calculator wrote there itself.
Ground truth throughout is docs/program.md sec 5's own worked examples,
the same real fixtures test_program_export.py/test_memory.py already
validate against (simple.dm41, unlabelled.dm41, twolabels.dm41,
global-key-assignments.dm41), and the tower.{raw,dat} third-party-compiled
1088-byte program.
"""

import os
from pathlib import Path

import pytest

from memory import Memory, DM41MemoryError, Program
from memory.opcode_scan import find_program_end
from memory.program_files import decode_program_raw, decode_program_dat

DATA_DIR = Path(__file__).parent / "data"


def _programs_by_name(memory):
    return {p.names_label: p for p in memory.programs.list_programs()}


# -- Importing into completely empty program memory ------------------------


def test_import_apptest_into_empty_memory_matches_simple_dm41_exactly():
    # Re-creating simple.dm41 from scratch by importing APPTEST alone
    # into empty.dm41 should reproduce that fixture's own DotEnd/chain
    # layout byte for byte (docs/program.md's own worked example).
    source = Memory.from_file(DATA_DIR / "simple.dm41")
    apptest_bytes = source.programs.get_program_bytes(source.programs.list_programs()[0])

    dest = Memory.from_file(DATA_DIR / "empty.dm41")
    imported = dest.programs.import_program(apptest_bytes)

    assert imported.names_label == "APPTEST"
    assert imported.length == 26
    assert dest.programs.get_program_bytes(imported) == apptest_bytes
    assert dest.status_registers.DotEnd() == source.status_registers.DotEnd()
    assert dest.status_registers.R00() == source.status_registers.R00()


def test_import_unlabelled_program_into_empty_memory_round_trips_exactly():
    # An unlabelled program whose own trailing END already encoded
    # bbb=distance_registers=0 ("no predecessor") in its source memory --
    # importing it as the first-ever program into an empty destination
    # should need no change to those bytes at all.
    source = Memory.from_file(DATA_DIR / "unlabelled.dm41")
    prog_bytes = source.programs.get_program_bytes(source.programs.list_programs()[0])

    dest = Memory.from_file(DATA_DIR / "empty.dm41")
    imported = dest.programs.import_program(prog_bytes)
    assert dest.programs.get_program_bytes(imported) == prog_bytes


def test_import_tower_into_empty_memory_round_trips_exactly():
    # A real, third-party-compiled 1088-byte program with no global label
    # at all -- exercises a much larger write/splice than any hand-built
    # fixture.
    instruction_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    dest = Memory.from_file(DATA_DIR / "empty.dm41")
    imported = dest.programs.import_program(instruction_bytes)
    assert imported.length == 1088
    assert dest.programs.get_program_bytes(imported) == instruction_bytes
    assert find_program_end(dest.programs.get_program_bytes(imported)) == 1088


def test_import_dat_and_raw_of_tower_produce_identical_results():
    dest_raw = Memory.from_file(DATA_DIR / "empty.dm41")
    dest_dat = Memory.from_file(DATA_DIR / "empty.dm41")
    raw_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    dat_bytes = decode_program_dat((DATA_DIR / "tower.dat").read_bytes())
    assert raw_bytes == dat_bytes

    imported_raw = dest_raw.programs.import_program(raw_bytes)
    imported_dat = dest_dat.programs.import_program(dat_bytes)
    assert dest_raw.programs.get_program_bytes(imported_raw) == dest_dat.programs.get_program_bytes(imported_dat)
    assert dest_raw.status_registers.DotEnd() == dest_dat.status_registers.DotEnd()


def test_import_ppc_of_tower_matches_dat_and_raw():
    # PPC (memory/program_files.py's module docstring) is DAT's own hex
    # text word-wrapped every 50 characters -- built here from tower.dat
    # directly (not through encode_program_ppc()) so this exercises
    # decode_program_dat() against a file it didn't produce itself, same
    # as test_decode_program_ppc_and_dat_agree_on_tower() in
    # test_program_export.py.
    dat_text = (DATA_DIR / "tower.dat").read_bytes()
    wrapped = b"\n".join(dat_text[i : i + 50] for i in range(0, len(dat_text), 50)) + b"\n"

    dest_ppc = Memory.from_file(DATA_DIR / "empty.dm41")
    dest_dat = Memory.from_file(DATA_DIR / "empty.dm41")
    ppc_bytes = decode_program_dat(wrapped)
    dat_bytes = decode_program_dat(dat_text)
    assert ppc_bytes == dat_bytes

    imported_ppc = dest_ppc.programs.import_program(ppc_bytes)
    imported_dat = dest_dat.programs.import_program(dat_bytes)
    assert dest_ppc.programs.get_program_bytes(imported_ppc) == dest_dat.programs.get_program_bytes(imported_dat)
    assert dest_ppc.status_registers.DotEnd() == dest_dat.status_registers.DotEnd()


# -- Case B: importing alongside a program that already has a real END ----


def test_import_into_simple_dm41_case_b_preserves_apptest_and_stacks_new_program():
    # simple.dm41's APPTEST already has its own real closing END --
    # importing a second program shouldn't touch APPTEST at all, and the
    # new program should land as a second, newer entry.
    source = Memory.from_file(DATA_DIR / "unlabelled.dm41")
    prog_bytes = source.programs.get_program_bytes(source.programs.list_programs()[0])  # 16 bytes, unlabelled

    dest = Memory.from_file(DATA_DIR / "simple.dm41")
    apptest_before = _programs_by_name(dest)["APPTEST"]

    imported = dest.programs.import_program(prog_bytes)

    programs = dest.programs.list_programs()
    assert len(programs) == 2
    apptest_after = _programs_by_name(dest)["APPTEST"]
    assert apptest_after.length == apptest_before.length == 26
    assert apptest_after.start_addr == apptest_before.start_addr
    assert apptest_after.start_offset == apptest_before.start_offset
    assert dest.programs.get_program_bytes(apptest_after) == dest.programs.get_program_bytes(apptest_before)

    assert imported.length == 16
    newest = programs[-1]
    assert newest.start_addr == imported.start_addr
    assert newest.start_offset == imported.start_offset
    assert newest.length == imported.length
    # Only the trailing marker's own distance field should have changed
    # (it now links back to APPTEST's END instead of "no predecessor").
    reimported_bytes = dest.programs.get_program_bytes(imported)
    assert reimported_bytes[:-3] == prog_bytes[:-3]
    assert reimported_bytes[-3:] != prog_bytes[-3:]


def test_import_stacks_multiple_programs_in_order():
    source = Memory.from_file(DATA_DIR / "unlabelled.dm41")
    progs = source.programs.list_programs()
    prog1_bytes = source.programs.get_program_bytes(progs[0])
    prog2_bytes = source.programs.get_program_bytes(progs[1])

    dest = Memory.from_file(DATA_DIR / "simple.dm41")
    dest.programs.import_program(prog1_bytes)
    dest.programs.import_program(prog2_bytes)

    programs = dest.programs.list_programs()
    assert [p.names_label for p in programs] == ["APPTEST", "(unlabelled)", "(unlabelled)"]
    assert [p.length for p in programs] == [26, 16, 20]
    for program in programs:
        instruction_bytes = dest.programs.get_program_bytes(program)
        assert len(instruction_bytes) == program.length
        assert find_program_end(instruction_bytes) == len(instruction_bytes)


# -- Case A: importing when .END. is itself the newest program's terminator -


def test_import_into_twolabels_case_a_converts_dot_end_to_real_end():
    # twolabels.dm41's FIRST/SECOND program has no explicit END of its
    # own -- .END. terminates it directly. Importing a new program must
    # convert that .END. into a real closing END in place (unchanged
    # length/labels/bytes for the original program) before linking the
    # new one to it.
    source = Memory.from_file(DATA_DIR / "unlabelled.dm41")
    prog_bytes = source.programs.get_program_bytes(source.programs.list_programs()[0])

    dest = Memory.from_file(DATA_DIR / "twolabels.dm41")
    original = dest.programs.list_programs()[0]
    assert original.terminator == ".END."
    original_bytes = dest.programs.get_program_bytes(original)

    imported = dest.programs.import_program(prog_bytes)

    programs = dest.programs.list_programs()
    assert len(programs) == 2
    converted = programs[0]
    assert converted.terminator == "END"  # no longer ".END." -- converted
    assert converted.length == original.length == 28
    assert [l.name for l in converted.labels] == ["FIRST", "SECOND"]
    # Only the converted marker's own end-type nibble should have changed
    # (0x2_ -- permanent .END. -- to 0x0_ -- a normal closing END); its
    # low nibble (packed status) and everything else about the program's
    # own bytes stays exactly as it was.
    converted_bytes = dest.programs.get_program_bytes(converted)
    assert converted_bytes[:-1] == original_bytes[:-1]
    assert converted_bytes[-1] == original_bytes[-1] & 0x0F
    assert original_bytes[-1] >> 4 == 2
    assert converted_bytes[-1] >> 4 == 0

    assert imported.length == 16
    assert programs[1].start_addr == imported.start_addr
    assert programs[1].start_offset == imported.start_offset


def test_import_key_assignment_case_a():
    # global-key-assignments.dm41's AAA program terminates with a real
    # END already... use twolabels.dm41 instead so this exercises Case A
    # (see test above) while also verifying the imported label's own key
    # byte gets cleared.
    source = Memory.from_file(DATA_DIR / "global-key-assignments.dm41")
    programs = source.programs.list_programs()
    aaa = [p for p in programs if p.names_label == "AAA"][0]
    aaa_bytes = source.programs.get_program_bytes(aaa)
    assert aaa.labels[0].key_assignment != 0

    dest = Memory.from_file(DATA_DIR / "twolabels.dm41")
    imported = dest.programs.import_program(aaa_bytes)
    assert imported.labels[0].key_assignment == 0
    reimported_bytes = dest.programs.get_program_bytes(imported)
    assert reimported_bytes[3] == 0x00  # the label header's own key byte


# -- Validation / error handling -------------------------------------------


def test_import_rejects_empty_bytes():
    dest = Memory.from_file(DATA_DIR / "empty.dm41")
    with pytest.raises(ValueError):
        dest.programs.import_program(b"")


def test_import_rejects_malformed_bytes():
    dest = Memory.from_file(DATA_DIR / "empty.dm41")
    with pytest.raises(ValueError):
        dest.programs.import_program(b"\x00\x01\x02\x03")


def test_import_blocks_duplicate_global_label_name():
    source = Memory.from_file(DATA_DIR / "global-key-assignments.dm41")
    programs = source.programs.list_programs()
    aaa_bytes = source.programs.get_program_bytes(
        [p for p in programs if p.names_label == "AAA"][0]
    )

    dest = Memory.from_file(DATA_DIR / "global-key-assignments.dm41")
    with pytest.raises(ValueError):
        dest.programs.import_program(aaa_bytes)  # AAA already exists in dest
    # Blocked -- the buffer must be completely unchanged.
    assert dest.programs.list_programs()[0].names_label == "AAA"
    assert len(dest.programs.list_programs()) == 2


def test_import_raises_when_program_memory_is_full_and_leaves_memory_unchanged():
    dest = Memory.from_file(DATA_DIR / "empty.dm41")
    alarms_end = dest.alarms.end_exclusive
    dest.status_registers.set_R00(alarms_end + 2)  # ~14 bytes of free program memory
    dest.status_registers.set_DotEnd(alarms_end + 1)

    before_dot_end = dest.status_registers.DotEnd()
    before_programs = dest.programs.list_programs()

    tower_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    with pytest.raises(DM41MemoryError):
        dest.programs.import_program(tower_bytes)

    assert dest.status_registers.DotEnd() == before_dot_end
    assert dest.programs.list_programs() == before_programs


def test_import_rejects_when_no_valid_partition_is_loaded():
    # A freshly-constructed Memory() actually ships with sane built-in
    # R00/.END. defaults (matching empty.dm41) -- corrupt R00 directly to
    # simulate a state with no real partition at all.
    dest = Memory()
    dest.status_registers.set_R00(0)
    with pytest.raises(DM41MemoryError):
        dest.programs.import_program(bytes.fromhex("c00009"))


def test_import_rejects_stale_program_reference_style_bogus_bytes():
    # Not a program-splicing concern per se, but a nearby sharp edge:
    # get_program_bytes() itself still raises ValueError for a Program
    # that doesn't match the current chain -- confirm import_program()
    # doesn't change that existing contract.
    dest = Memory.from_file(DATA_DIR / "simple.dm41")
    bogus = Program(start_addr=0x000, start_offset=0, length=0, labels=[], terminator="END")
    with pytest.raises(ValueError):
        dest.programs.get_program_bytes(bogus)


# -- Regression sweep --------------------------------------------------------


def test_import_every_program_in_every_sample_state_into_a_fresh_empty_memory():
    """For every real program in every sample state, exporting it and then
    importing it into a fresh empty buffer should never raise, and the
    result should read back the same length and remain one well-formed
    program (find_program_end() agrees) -- the fields that legitimately
    change on import (the outermost marker's own distance, and any
    label's key-assignment byte) are covered precisely by the more
    targeted tests above; this sweep is the same defensive/regression
    coverage as get_program_bytes()'s own
    test_get_program_bytes_terminates_on_every_sample_state(), just for
    the write side."""
    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(".dm41"):
            continue
        source = Memory.from_file(DATA_DIR / filename)
        for program in source.programs.list_programs():
            instruction_bytes = source.programs.get_program_bytes(program)
            dest = Memory.from_file(DATA_DIR / "empty.dm41")
            imported = dest.programs.import_program(instruction_bytes)
            assert imported.length == program.length, filename
            assert imported.names_label == program.names_label, filename
            reimported = dest.programs.get_program_bytes(imported)
            assert len(reimported) == len(instruction_bytes), filename
            assert find_program_end(reimported) == len(reimported), filename
