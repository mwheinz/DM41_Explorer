"""
Tests for exporting a named program (global label) out of main program
memory as a standalone HP-41 program file -- Memory.get_program_bytes()
(memory/memory.py), the forward opcode scanner it's built on
(memory/opcode_scan.py, find_program_end()), and the RAW/DAT file
encoders (memory/program_files.py).

Ground truth for the byte counts and encoded bytes below comes from
sources external to this project:

  1. docs/program.md's own worked "simple.dm41" example, itself checked
     against the real DM41L's CAT 1 listing: APPTEST occupies 26 bytes.
  2. ~/Work/hp41uc (Leo Duran's HP-41 User-Code File Converter) compiled
     from source (Source/Makefile) and run directly: feeding APPTEST's
     26 raw instruction bytes through `hp41uc /r=apptest.raw /d`
     reproduces the exact DAT bytes hard-coded below, byte for byte.
  3. tests/data/tower.{txt,dat,raw}: a real, much larger (1088-byte)
     program hp41uc itself compiled from tower.txt (`hp41uc /t=tower.txt
     /r` and `/d`) -- exercises find_program_end()/decode_program_raw()/
     decode_program_dat() against far more of the real HP-41 opcode space
     than APPTEST's 26 bytes ever could, and both directions
     (decode-then-verify, encode-then-match) against genuine third-party
     output rather than anything self-generated.

find_program_end()/get_program_bytes() are a *forward* opcode-stream scan
(matching hp41uc's own seek_end()), deliberately independent of
ProgramInfo.distance_bytes -- the backward global-search chain distance
list_programs() reports, which is NOT a program size (see ProgramInfo's
docstring and docs/program.md's still-open reconciliation question).
"""

import os
from pathlib import Path

import pytest

from memory import (
    Memory,
    DM41MemoryError,
    Program,
    encode_program_raw,
    encode_program_dat,
    encode_program_ppc,
    encode_program_txt,
    decode_program_raw,
    decode_program_dat,
    decode_program_txt,
)
from memory.opcode_scan import find_program_end

DATA_DIR = Path(__file__).parent / "data"

# APPTEST's own instruction bytes (simple.dm41): its label header ("APP" +
# "TEST" spanning two registers, docs/program.md sec 5.2) through its own
# packed END marker ("c40309", the same plain-END chain link
# list_programs() finds -- docs/program.md's worked example).
APPTEST_BYTES = bytes.fromhex(
    "c000f8004150505445535410021140111010468475b200c40309"
)

# hp41uc's own DAT output for exactly these bytes -- verified against a
# locally-built hp41uc binary (~/Work/hp41uc/Source, `make` then
# `./hp41uc /r=apptest.raw /d`), byte for byte.
APPTEST_DAT_REFERENCE = (
    b"001AC000F8004150505445535410021140111010468475B200C4030948"
)


def test_find_program_end_apptest():
    assert find_program_end(APPTEST_BYTES) == 26


def test_find_program_end_returns_none_when_truncated():
    # No END marker anywhere in a truncated copy of APPTEST's bytes.
    assert find_program_end(APPTEST_BYTES[:-3]) is None


def test_find_program_end_stops_at_first_plain_end_not_at_embedded_label():
    # A second global label (C0 00 F4 00 "AB", a 3-char name) embedded
    # partway through, itself followed by a plain END -- the scanner
    # should walk straight through the embedded label (real HP-41
    # programs can have more than one entry point sharing one trailing
    # END) and only stop at the real END.
    embedded_label = bytes.fromhex("c000f40041") + b"AB"
    data = embedded_label + bytes.fromhex("c00020")  # plain END, 3 bytes
    assert find_program_end(data) == len(data)


def test_get_program_bytes_apptest_matches_docs_program_md():
    # simple.dm41 has exactly ONE program (APPTEST) under the corrected,
    # END-delimited model -- see test_list_programs_simple_is_one_program_not_two()
    # in test_memory.py for why an earlier reading of this state wrongly
    # believed there was a second, "nameless" one.
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    programs = memory.programs.list_programs()
    assert len(programs) == 1
    apptest = programs[0]
    assert apptest.names_label == "APPTEST"
    assert memory.programs.get_program_bytes(apptest) == APPTEST_BYTES


def test_get_program_bytes_rejects_stale_entry():
    # get_program_bytes() rejects an entry that doesn't match anything in
    # the *current* program list at all, e.g. a hand-built Program
    # pointing nowhere real.
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    bogus = Program(
        start_addr=0x000,
        start_offset=0,
        length=0,
        labels=[],
        terminator="END",
    )
    with pytest.raises(ValueError):
        memory.programs.get_program_bytes(bogus)


def test_get_program_bytes_unlabelled_matches_real_cat_1_byte_counts():
    # tests/data/unlabelled.dm41: two programs, neither named -- the user
    # confirmed 16 and 20 bytes against a real DM41L's CAT 1 listing. This
    # is the fixture that caught the bug where an earlier version of the
    # program grouping mistook the zero-padding bytes in front of the
    # permanent .END. marker for a small, nonexistent third program.
    memory = Memory.from_file(DATA_DIR / "unlabelled.dm41")
    programs = memory.programs.list_programs()
    assert len(programs) == 2
    assert [p.length for p in programs] == [16, 20]
    for program in programs:
        instruction_bytes = memory.programs.get_program_bytes(program)
        assert len(instruction_bytes) == program.length
        assert find_program_end(instruction_bytes) == len(instruction_bytes)


def test_get_program_bytes_twolabels_exports_the_whole_shared_program():
    # tests/data/twolabels.dm41: one program, two global labels ("FIRST",
    # "SECOND"), no explicit END -- terminated only by the permanent
    # .END. sentinel (legal for the single newest program in memory).
    # Exporting it returns the full 28-byte block starting at FIRST's own
    # header (the oldest/topmost label), not a per-label slice.
    memory = Memory.from_file(DATA_DIR / "twolabels.dm41")
    programs = memory.programs.list_programs()
    assert len(programs) == 1
    program = programs[0]
    instruction_bytes = memory.programs.get_program_bytes(program)
    assert len(instruction_bytes) == 28
    assert find_program_end(instruction_bytes) == len(instruction_bytes)


def test_get_program_bytes_finds_a_real_unnamed_program_mid_chain():
    # 3x-xm.dm41 has a real unnamed program sandwiched between two named
    # ones (not just trailing at the newest end of the chain like
    # simple.dm41's case) -- a stronger check that list_programs()'s
    # grouping generalizes past the single-program case.
    memory = Memory.from_file(DATA_DIR / "3x-xm.dm41")
    programs = memory.programs.list_programs()
    unnamed = [p for p in programs if not p.is_named]
    assert len(unnamed) == 1
    data = memory.programs.get_program_bytes(unnamed[0])
    assert len(data) == unnamed[0].length > 0
    assert find_program_end(data) == len(data)


def test_get_program_bytes_terminates_on_every_sample_state():
    """Defensive/regression coverage, matching
    test_list_programs_terminates_on_every_sample_state(): exporting every
    program in every sample state should never raise DM41MemoryError (a
    corrupt-data signal) or hang, and every program's own bytes must form
    exactly one well-formed program (find_program_end() agrees on where
    it ends)."""
    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(".dm41"):
            continue
        memory = Memory.from_file(DATA_DIR / filename)
        for program in memory.programs.list_programs():
            instruction_bytes = memory.programs.get_program_bytes(program)
            assert len(instruction_bytes) == program.length > 0, filename
            assert find_program_end(instruction_bytes) == len(
                instruction_bytes
            ), filename


def test_encode_program_raw_apptest():
    encoded = encode_program_raw(APPTEST_BYTES)
    # [compiled code] + [1-byte checksum] + [zero trailer], padded to a
    # multiple of 256 bytes (hp41uc's write_raw_checksum(), bufsize=256).
    assert len(encoded) == 256
    assert encoded[: len(APPTEST_BYTES)] == APPTEST_BYTES
    assert encoded[len(APPTEST_BYTES)] == sum(APPTEST_BYTES) % 256
    assert all(b == 0 for b in encoded[len(APPTEST_BYTES) + 1 :])


def test_encode_program_dat_apptest_matches_hp41uc():
    assert encode_program_dat(APPTEST_BYTES) == APPTEST_DAT_REFERENCE


def test_encode_program_dat_rejects_oversized_program():
    with pytest.raises(ValueError):
        encode_program_dat(b"\x00" * 0x10000)


# -- tower.{txt,dat,raw}: a real, third-party-generated 1088-byte program --
#
# The user compiled tests/data/tower.txt with hp41uc directly (`/t=tower.txt
# /r=tower.raw` and `/d=tower.dat`) and added the results as test fixtures.
# tower.txt's first real line is a LOCAL numbered label ("LBL 21"), not a
# global alpha label -- exactly the real-world case that showed
# Memory.get_program_bytes() originally (and wrongly) required a named
# global label to export a program at all; see
# test_get_program_bytes_recovers_a_program_with_no_label_at_all() and
# test_get_program_bytes_finds_a_real_unnamed_program_mid_chain() above,
# which exercise that fix directly against real .dm41 states.
#
# tower.{raw,dat} aren't themselves loaded into any .dm41 state fixture,
# so they can't drive Memory.get_program_bytes() end to end -- but they're
# an excellent real-world check on the lower-level pieces that fix relies
# on: decoding both files must recover identical instruction bytes,
# find_program_end() must confirm they form exactly one well-formed
# program, and re-encoding must reproduce both files byte for byte.


def test_decode_program_raw_and_dat_agree_on_tower():
    raw_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    dat_bytes = decode_program_dat((DATA_DIR / "tower.dat").read_bytes())
    assert raw_bytes == dat_bytes
    assert len(raw_bytes) == 1088


def test_find_program_end_matches_towers_declared_length():
    dat_bytes = decode_program_dat((DATA_DIR / "tower.dat").read_bytes())
    assert find_program_end(dat_bytes) == len(dat_bytes)


def test_decode_program_dat_reads_towers_declared_length_header():
    raw_dat_file = (DATA_DIR / "tower.dat").read_bytes()
    assert raw_dat_file[:4] == b"0440"  # 0x0440 == 1088, big-endian ASCII hex
    assert int(raw_dat_file[:4], 16) == 1088


def test_encode_program_raw_round_trips_tower():
    instruction_bytes = decode_program_dat((DATA_DIR / "tower.dat").read_bytes())
    assert encode_program_raw(instruction_bytes) == (DATA_DIR / "tower.raw").read_bytes()


def test_encode_program_dat_round_trips_tower():
    instruction_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    assert encode_program_dat(instruction_bytes) == (DATA_DIR / "tower.dat").read_bytes()


def test_decode_program_raw_rejects_corrupt_checksum():
    data = bytearray(encode_program_raw(APPTEST_BYTES))
    data[len(APPTEST_BYTES)] ^= 0xFF  # flip the checksum byte
    with pytest.raises(DM41MemoryError):
        decode_program_raw(bytes(data))


def test_decode_program_dat_rejects_corrupt_checksum():
    data = bytearray(encode_program_dat(APPTEST_BYTES))
    data[-1] ^= 0x0F  # perturb one checksum hex digit
    with pytest.raises(DM41MemoryError):
        decode_program_dat(bytes(data))


def test_decode_program_dat_rejects_truncated_file():
    with pytest.raises(DM41MemoryError):
        decode_program_dat(b"001A")  # header only, no body/checksum


# -- PPC: a third, non-hp41uc format found alongside real RAW/DAT/TXT
# files for the same programs (~/Work/DM41/TowerOfSkelos, given the
# ".ppc" suffix by whoever saved them) --------------------------------
#
# Reverse-engineered by comparing two real *.ppc files against their own
# *.dat siblings directly: byte-for-byte identical except for a newline
# (0x0A) inserted every 50 characters, plus one trailing newline -- i.e.
# PPC is just DAT's own hex text, word-wrapped for display/printing, not
# a distinct binary layout. See program_files.py's module docstring for
# the full story (including why DM41_Explorer's own DAT import was
# choking on these files when tried under a renamed .dat/.raw
# extension: decode_program_dat()'s fixed-byte-offset slicing and
# decode_program_raw()'s opcode scan both assume no embedded whitespace).
#
# These tests build synthetic PPC input by wrapping a known-good DAT
# file's own hex text (_wrap_like_ppc(), independent of
# encode_program_ppc()) rather than adding new binary fixtures -- the
# format is fully defined in terms of DAT, and this also keeps
# decode_program_dat() from only ever being checked against its own
# encoder's exact inverse.


def _wrap_like_ppc(dat_text: bytes, width: int = 50) -> bytes:
    """Word-wraps DAT hex text the same way a real PPC file does, without
    going through encode_program_ppc() itself."""
    lines = [dat_text[i : i + width] for i in range(0, len(dat_text), width)]
    return b"\n".join(lines) + b"\n"


def test_decode_program_ppc_round_trips_apptest():
    ppc_bytes = encode_program_ppc(APPTEST_BYTES)
    assert b"\n" in ppc_bytes  # APPTEST's 58-char DAT text wraps at least once
    assert decode_program_dat(ppc_bytes) == APPTEST_BYTES


def test_encode_program_ppc_wraps_dat_text_at_50_chars():
    dat_text = encode_program_dat(APPTEST_BYTES)
    ppc_bytes = encode_program_ppc(APPTEST_BYTES)
    assert ppc_bytes.replace(b"\n", b"") == dat_text
    lines = ppc_bytes.split(b"\n")
    assert lines[-1] == b""  # trailing newline leaves an empty final split
    assert all(len(line) == 50 for line in lines[:-2])
    assert 0 < len(lines[-2]) <= 50


def test_decode_program_ppc_and_dat_agree_on_tower():
    dat_bytes = decode_program_dat((DATA_DIR / "tower.dat").read_bytes())
    wrapped = _wrap_like_ppc((DATA_DIR / "tower.dat").read_bytes())
    assert decode_program_dat(wrapped) == dat_bytes


def test_encode_program_ppc_round_trips_tower():
    instruction_bytes = decode_program_dat((DATA_DIR / "tower.dat").read_bytes())
    ppc_bytes = encode_program_ppc(instruction_bytes)
    assert decode_program_dat(ppc_bytes) == instruction_bytes
    assert ppc_bytes.replace(b"\n", b"") == (DATA_DIR / "tower.dat").read_bytes()


def test_decode_program_ppc_tolerates_crlf_line_endings():
    # Only the two real sample files (LF only) were available to reverse-
    # engineer this format from, so this guards the whitespace-stripping
    # being deliberately broader than just "\n" -- in case a PPC file
    # picks up CRLF line endings somewhere along the way (e.g. opened and
    # resaved on Windows).
    ppc_bytes = encode_program_ppc(APPTEST_BYTES).replace(b"\n", b"\r\n")
    assert decode_program_dat(ppc_bytes) == APPTEST_BYTES


def test_decode_program_ppc_rejects_corrupt_checksum():
    data = bytearray(encode_program_ppc(APPTEST_BYTES))
    data[-2] ^= 0x0F  # perturb the checksum's last hex digit (data[-1] is "\n")
    with pytest.raises(DM41MemoryError):
        decode_program_dat(bytes(data))


# -- TXT: the plain-text keystroke-listing format (memory/program_text.py,
# docs/program_text_io_plan.md). encode_program_txt()/decode_program_txt()
# here are thin UTF-8-bytes wrappers around program_text.py's own str-based
# converters (see program_files.py's own docstring) -- the actual opcode
# table and tokenizer are already exhaustively tested directly against
# tower.txt/tower.raw in test_program_text.py; these tests only check the
# file-format wrapping (bytes in, bytes out, DM41MemoryError instead of a
# bare ValueError) that gui/program_tab.py's Export/Import dispatch relies
# on, matching RAW/DAT/PPC's own coverage above.


def test_encode_program_txt_apptest_round_trips():
    # APPTEST_BYTES is a real-hardware capture, not hp41uc-compiled text
    # -- its own END genuinely links back to its own label header (bbb=2/
    # distance=3, see memory/program_text.py's decode_program_txt()
    # docstring), which a compiler working from text alone can never
    # reconstruct (nor should it try to -- see that same docstring). So
    # this checks a *stable* round trip (recompiling reproduces the same
    # text, and the same instruction content/length), not byte-identical
    # equality against APPTEST_BYTES itself -- test_decode_program_txt_
    # round_trips_tower_raw() below is the byte-identical check, against
    # a fixture that's actually hp41uc's own compiled output.
    file_bytes = encode_program_txt(APPTEST_BYTES)
    assert isinstance(file_bytes, bytes)
    text = file_bytes.decode("utf-8")
    assert text.splitlines()[0] == 'LBL "APPTEST"'
    recompiled = decode_program_txt(file_bytes)
    assert len(recompiled) == len(APPTEST_BYTES)
    assert encode_program_txt(recompiled) == file_bytes


def test_decode_program_txt_round_trips_tower_raw():
    instruction_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    file_bytes = encode_program_txt(instruction_bytes)
    assert decode_program_txt(file_bytes) == instruction_bytes


def test_decode_program_txt_reads_towers_own_txt_file():
    # tower.txt itself, read as a file (not run through this project's
    # own encoder first) -- the same real, third-party-generated fixture
    # test_program_text.py's test_decode_program_txt_matches_tower_raw_bytes()
    # already checks at the program_text.py level; this confirms the
    # program_files.py wrapper reproduces it too, reading actual on-disk
    # bytes exactly as gui/program_tab.py's Import handler would.
    file_bytes = (DATA_DIR / "tower.txt").read_bytes()
    raw_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    assert decode_program_txt(file_bytes) == raw_bytes


def test_decode_program_txt_ignores_utf8_bom():
    # Some Windows editors save UTF-8 with a leading byte-order mark;
    # left in, it would glue itself onto the first token (LBL).
    assert decode_program_txt(b'\xef\xbb\xbfLBL "T1"\nSIN\nEND\n') == (
        decode_program_txt(b'LBL "T1"\nSIN\nEND\n')
    )


def test_decode_program_txt_falls_back_to_cp437():
    # hp41uc-era DOS listings: CP437 byte 0xE4 is Sigma. b"\xe4+" isn't
    # valid UTF-8, so this only compiles via the CP437 fallback.
    compiled = decode_program_txt(b'LBL "T1"\n\xe4+\nCL\xe4\nEND\n')
    assert compiled[6:8] == bytes([0x47, 0x70])  # Σ+, CLΣ


def test_decode_program_txt_non_utf8_garbage_is_a_compile_error():
    # CP437 decodes any byte, so non-UTF-8 junk is no longer rejected at
    # the decoding step -- it must still fail as an unrecognized
    # instruction, and still as DM41MemoryError.
    with pytest.raises(DM41MemoryError, match="unrecognized instruction"):
        decode_program_txt(b'LBL "T1"\n\xff\xfe\nEND\n')


def test_decode_program_txt_rejects_uncompilable_text():
    # program_text.decode_program_txt() itself raises a bare ValueError
    # for this (see test_program_text.py); the program_files.py wrapper
    # must translate that into DM41MemoryError, matching
    # decode_program_raw()/decode_program_dat()'s own error type, so
    # gui/program_tab.py's shared `except (OSError, DM41MemoryError)`
    # handler actually catches it.
    with pytest.raises(DM41MemoryError):
        decode_program_txt(b'LBL "T1"\nNOTAREALINSTRUCTION\nEND\n')
