'''
Tests for decompiling HP-41 program instruction bytes into plain text
(memory/program_text.py's encode_program_txt()) -- phase 1 ("Opcode
table + decompile only") of docs/program_text_io_plan.md's suggested
phasing.

The main test here (test_encode_program_txt_matches_tower_reference)
round-trips the same real, third-party-generated 1088-byte program used
by test_program_export.py (tests/data/tower.raw, hp41uc's own compiled
output of tests/data/tower.txt) back through this module's decompiler and
compares the result against tower.txt line by line -- the strongest
available check, since tower.txt wasn't produced by any code in this
project. Every one of tower.txt's 527 real instruction lines is expected
to match exactly, since decompiling can't invent branch-offset math or
guess ambiguous encodings (docs/program_text_io_plan.md sec 2.2) -- the
opcode table is either right or it visibly isn't.

Two categories of expected, harmless divergence are accounted for rather
than ignored outright:

  1. User-written prose comments (";Tower of Skelos Game Program",
     ";Append colon", etc.) can never be recovered from compiled bytes --
     comments aren't part of the instruction stream at all -- so this
     project's own decompile decision (docs/program_text_io_plan.md sec
     5) is to reproduce hp41uc's own *mechanical* annotations (the XROM
     name comment, the END trailer) but not arbitrary human prose. The
     comparison below strips any trailing `;...` comment from tower.txt's
     side before comparing, but NOT from this module's own output --
     hp41uc-style mechanical comments (XROM's function name, matched
     exactly) are expected to come through unchanged.
  2. tower.txt's own END line says ";1084 BYTES", but decode_program_raw()
     independently confirms (and test_program_export.py already asserts)
     that tower.raw's real instruction-byte length is 1088, not 1084 --
     a pre-existing inconsistency in the fixture itself, not something
     this project's own code can or should paper over by hard-coding
     "1084". This is checked separately: the END line's own byte count
     is asserted against the objectively-correct 1088, independent of
     whatever tower.txt's own text says.
'''

import os
from pathlib import Path

import pytest

from memory.program_files import decode_program_raw, decode_program_dat
from memory.opcode_scan import find_program_end, scan_global_markers_forward
from memory.program_text import encode_program_txt, decode_program_txt

DATA_DIR = Path(__file__).parent / "data"

# APPTEST's own instruction bytes -- see test_program_export.py's own
# copy of this constant for its provenance (docs/program.md's
# "simple.dm41" worked example, cross-checked against a real hp41uc
# build). A second, independent (and much smaller) real-world case.
APPTEST_BYTES = bytes.fromhex(
    "c000f8004150505445535410021140111010468475b200c40309"
)


def _strip_trailing_comment(line: str) -> str:
    '''Quote-aware removal of a trailing `;` comment (tower.txt only ever
    uses `;`, never `#`, for a real comment -- and a couple of its
    mnemonics, like "X#Y?", contain a literal '#' that must NOT be
    mistaken for one).'''
    in_quotes = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ";" and not in_quotes:
            return line[:i].rstrip()
    return line.rstrip()


def test_encode_program_txt_apptest_basic_shape():
    text = encode_program_txt(APPTEST_BYTES)
    lines = text.splitlines()
    assert lines[0] == 'LBL "APPTEST"'
    assert lines[-1] == "END ;26 BYTES"
    # Every real HP-41 instruction in between decoded to *something*
    # recognizable -- never this module's own "unrecognized" fallback --
    # for a fixture with no synthetic/undocumented bytes in it at all.
    assert not any(line.startswith("; UNKNOWN OPCODE") for line in lines)


def test_encode_program_txt_stops_at_end_ignoring_trailing_padding():
    # Regression test: a caller that passes a RAW *file*'s bytes
    # directly -- forgetting to strip its own checksum-and-zero-padding
    # trailer with decode_program_raw() first (encode_program_raw()
    # always pads the file out to a multiple of 256 bytes) -- must still
    # get back exactly the real program, correctly terminated, not the
    # padding misdecoded as more instructions tacked on after it.
    apptest_padded = APPTEST_BYTES + bytes([sum(APPTEST_BYTES) % 256]) + bytes(229)
    assert len(apptest_padded) == 256  # a real RAW file's own padded size
    text = encode_program_txt(apptest_padded)
    lines = text.splitlines()
    assert lines[-1] == "END ;26 BYTES"
    assert not any("UNKNOWN OPCODE" in line for line in lines)

    # The same thing, but against the real tower.raw *file* bytes (1280
    # bytes on disk) rather than its 1088 decoded instruction bytes --
    # the exact scenario that surfaced this bug.
    raw_file_bytes = (DATA_DIR / "tower.raw").read_bytes()
    assert len(raw_file_bytes) == 1280
    lines = encode_program_txt(raw_file_bytes).splitlines()
    assert lines[-1] == "END ;1088 BYTES"
    assert len(lines) == 527
    assert not any("UNKNOWN OPCODE" in line for line in lines)


def test_encode_program_txt_matches_tower_reference():
    instruction_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    assert len(instruction_bytes) == 1088
    assert find_program_end(instruction_bytes) == len(instruction_bytes)

    decoded_lines = encode_program_txt(instruction_bytes).splitlines()

    reference_lines = (DATA_DIR / "tower.txt").read_text(encoding="utf-8").splitlines()
    reference_instruction_lines = [
        line
        for line in reference_lines
        if line.strip() and not line.strip().startswith((";", "#"))
    ]

    assert len(decoded_lines) == len(reference_instruction_lines) == 527

    mismatches = []
    pairs = zip(decoded_lines, reference_instruction_lines)
    for i, (got, want_raw) in enumerate(pairs, start=1):
        got = got.strip()
        want_full = want_raw.strip()
        want_code_only = _strip_trailing_comment(want_raw).strip()
        # A mechanical comment hp41uc's own decompiler generates (XROM's
        # function-name annotation) is reproduced exactly, so `got`
        # should match tower.txt's *full* line; a hand-typed prose
        # comment isn't reproduced at all, so `got` should match only
        # tower.txt's code portion with the comment stripped. Either is
        # an expected match.
        if got == want_full or got == want_code_only:
            continue
        # The only other expected divergence: the END line's own byte
        # count differs from tower.txt's stale "1084" -- see this
        # module's own docstring, point 2. Everything else about the
        # line must still match.
        if (
            want_code_only == "END"
            and got.startswith("END ;")
            and got.endswith(" BYTES")
        ):
            continue
        mismatches.append((i, got, want_raw))

    assert not mismatches, "\n".join(
        f"line {i}: got {got!r}, want (code only) {want!r}"
        for i, got, want in mismatches
    )


def test_encode_program_txt_tower_end_reports_real_byte_count():
    # Independent of tower.txt's own (stale) comment text -- see this
    # module's docstring, point 2.
    instruction_bytes = decode_program_dat((DATA_DIR / "tower.dat").read_bytes())
    decoded_lines = encode_program_txt(instruction_bytes).splitlines()
    assert decoded_lines[-1] == f"END ;{len(instruction_bytes)} BYTES"
    assert len(instruction_bytes) == 1088


def test_encode_program_txt_decodes_xrom_with_module_comment():
    # "XROM 25,46 ;X<>F" appears three times in tower.txt -- confirms the
    # mm/ff-recovery formula against a real, third-party-compiled
    # example, not just this module's own encoder.
    instruction_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    text = encode_program_txt(instruction_bytes)
    assert "XROM 25,46 ;X<>F" in text
    assert "XROM 25,42 ;SEEKPT" in text
    assert "XROM 25,05 ;ARCLREC" in text
    assert "XROM 25,17 ;GETKEY" in text


def test_encode_program_txt_decodes_number_literals_and_negatives():
    instruction_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    lines = encode_program_txt(instruction_bytes).splitlines()
    assert "3 E3" in lines  # scientific notation: mantissa, space, E-exponent
    assert "E2" in lines  # bare exponent, no mantissa -- no leading space
    assert "-1" in lines  # CHS rendered as a leading '-', no space
    assert "1.006" in lines  # decimal point mid-run
    assert ".5" in lines  # decimal point as the very first character


def test_encode_program_txt_decodes_indirect_and_stack_register_operands():
    instruction_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    lines = encode_program_txt(instruction_bytes).splitlines()
    assert "STO IND 16" in lines
    assert "RCL IND 22" in lines
    assert "STO IND Y" in lines
    assert "GTO IND X" in lines
    assert "ARCL X" in lines


def test_encode_program_txt_decodes_compact_local_forms():
    instruction_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    lines = encode_program_txt(instruction_bytes).splitlines()
    # Compact single-byte forms (registers/labels 00-15/00-14) alongside
    # the general 2-/3-byte forms for the same mnemonics at higher values
    # -- both must decode correctly from the *same* real program.
    assert "RCL 07" in lines
    assert "RCL 16" in lines
    assert "STO 09" in lines
    assert "STO 19" in lines
    assert "LBL 00" in lines
    assert "LBL 21" in lines
    assert "GTO 00" in lines
    assert "GTO 20" in lines
    assert "XEQ 07" in lines  # XEQ never gets the compact form, unlike GTO


def test_encode_program_txt_decodes_append_alpha_strings():
    instruction_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    lines = encode_program_txt(instruction_bytes).splitlines()
    assert '>":"' in lines
    assert '>" "' in lines
    assert '"SCORE: "' in lines


def test_encode_program_txt_decodes_global_name_references():
    instruction_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    lines = encode_program_txt(instruction_bytes).splitlines()
    assert 'XEQ "PK-N"' in lines
    assert 'XEQ "UP-N"' in lines
    assert 'LBL "TWR"' in lines


def test_encode_program_txt_never_raises_on_every_sample_program():
    '''Defensive/regression coverage, matching the style of
    test_get_program_bytes_terminates_on_every_sample_state() in
    test_program_export.py: decompiling every real program in every
    sample state should never raise, and should always end in a line
    starting with "END ;" (whether the underlying marker is a plain END
    or the permanent .END. sentinel -- both are rendered the same way,
    see this module's own encode_program_txt() docstring).'''
    import os

    from memory import Memory

    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(".dm41"):
            continue
        memory = Memory.from_file(DATA_DIR / filename)
        for program in memory.programs.list_programs():
            instruction_bytes = memory.programs.get_program_bytes(program)
            text = encode_program_txt(instruction_bytes)
            assert text.splitlines()[-1].startswith("END ;"), filename
            # hp41uc text is 7-bit ASCII (docs/mnemonic_dialects_plan.md R2).
            assert text.isascii(), filename


# ===========================================================================
# Phase 2 -- decode_program_txt(): compiling text back into instruction
# bytes. docs/program_text_io_plan.md's own phase-2 description (sec 7):
# "round-trip-test against the same tower.txt/tower.raw pair, and against
# a few hand-authored short test programs covering each instruction
# category (single-byte, postfix, global label, ALPHA text with escapes,
# XROM, END)."
# ===========================================================================


def test_decode_program_txt_matches_tower_raw_bytes():
    '''The strongest available check (plan doc sec 4.4): tower.raw is
    *hp41uc's own compiled output* from tower.txt, so a byte-identical
    match here confirms this module's compiler against a real,
    third-party-generated program, not just against its own decompiler.
    tower.txt's own END comment now correctly says ";1088 BYTES" (the
    user corrected the fixture's stale "1084" after independently
    confirming 1088 on real DM41 hardware -- see this module's own
    docstring's "point 2", now historical) -- decode_program_txt() never
    looks at that comment text either way, only at the actual END
    keyword.'''
    raw_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    assert len(raw_bytes) == 1088

    tower_text = (DATA_DIR / "tower.txt").read_text(encoding="utf-8")
    compiled = decode_program_txt(tower_text)

    assert compiled == raw_bytes


def test_decode_program_txt_round_trips_encode_program_txt_on_tower():
    '''A second, independent round trip in the other direction: feed
    tower.raw's own bytes through this module's own decompiler, then
    straight back through the compiler, and expect the exact same bytes
    back out -- decode(encode(x)) == x, not just decode(hp41uc's text) ==
    hp41uc's bytes.'''
    raw_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    assert decode_program_txt(encode_program_txt(raw_bytes)) == raw_bytes


def _normalize_memory_state_fields(data: bytes) -> bytes:
    '''Zeroes out exactly the fields decode_program_txt() can never
    recover from text alone, because encode_program_txt() never renders
    them into text in the first place -- every one of these is
    live-memory state, not program *content*:

      - Every global chain marker's own bbb/distance_registers link
        fields. Confirmed (this module's own decode_program_txt()
        docstring) that hp41uc's own compiler always emits these as
        zero/unlinked, even mid-buffer -- but a real hardware capture
        (or any state copied out of live memory, e.g. via
        ProgramMemory.get_program_bytes()) carries whatever *real*
        links happened to exist in that memory layout, which
        decode_program_txt() has no way to reconstruct (nor should it
        try to -- linking a state's own chain to memory it's about to be
        spliced into is exactly what Memory.import_program()'s own
        pack()/_forward_scan_programs() repair mechanism already
        handles, per docs/program.md sec 5.4, and belongs there, not in
        a text compiler).
      - A global label header's key-assignment byte -- there's no
        key-assignment syntax anywhere in the program-text format to
        begin with.
      - An END's packed-status nibble -- packing is a separate,
        already-implemented, user-invoked operation (plan doc sec 5),
        not something a freshly compiled program has an opinion about.
      - A GTO/XEQ's cached jump-distance bytes (compact B1-BF's second
        byte; the long D0-EF form's low nibble, second byte, and
        direction bit) -- a real calculator resolves them, hp41uc never
        does, and there's nowhere in the text for them to live. See
        program_text.py's _GTO_COMPACT_BASE comment.

    Does *not* normalize a plain END vs. the permanent ".END." sentinel
    (the top nibble of an END's own third byte) -- both already decode
    to identical text ("END ;N BYTES", see encode_program_txt()'s own
    docstring), so a program whose *original* terminator was ".END."
    can never round-trip back to ".END." through text at all. That's a
    known, inherent limitation of the text format itself (there's
    nothing in hp41uc-style source to say ".END." vs "END" with), not
    something a normalizing helper should paper over -- tests that care
    about it (see test_decode_program_txt_cannot_recover_permanent_end_marker
    below) check it directly instead.'''
    out = bytearray(data)
    for marker in scan_global_markers_forward(data):
        idx = marker["index"]
        out[idx] = 0xC0
        out[idx + 1] = 0x00
        if marker["is_label"]:
            out[idx + 3] = 0x00  # key assignment byte
        else:
            end_type_nibble = marker["third_byte"] & 0xF0
            out[idx + 2] = end_type_nibble  # packed-status nibble -> 0
    _zero_cached_jump_bytes(data, out)
    return bytes(out)


def _zero_cached_jump_bytes(data: bytes, out: bytearray) -> None:
    '''Walks `data` instruction by instruction (the same length classes
    opcode_scan.find_program_end() uses) and clears every GTO/XEQ's cached
    jump distance in `out` -- see _normalize_memory_state_fields().'''
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if 0x1D <= c <= 0x1F:  # GTO"/XEQ" name reference
            has_name = i + 1 < n and data[i + 1] > 0xF0
            i += 2 + (data[i + 1] & 0x0F if has_name else 0)
        elif 0xB1 <= c <= 0xBF and i + 1 < n:  # compact GTO 00-14
            out[i + 1] = 0x00
            i += 2
        elif 0x90 <= c <= 0xBF or c in (0xCE, 0xCF):
            i += 2
        elif 0xC0 <= c <= 0xCD:  # END / global LBL
            has_name = i + 2 < n and data[i + 2] >= 0xF0
            i += 3 + (data[i + 2] & 0x0F if has_name else 0)
        elif 0xD0 <= c <= 0xEF and i + 2 < n:  # long GTO/XEQ
            out[i] = c & 0xF0
            out[i + 1] = 0x00
            out[i + 2] = data[i + 2] & 0x7F
            i += 3
        elif c >= 0xF0:  # ALPHA text
            i += 1 + (c & 0x0F)
        else:
            i += 1


def _dm41_sample_programs():
    '''Yields (filename, program, instruction_bytes) for every real
    program in every .dm41 sample state -- the same fixture set
    test_encode_program_txt_never_raises_on_every_sample_program()
    already exercises for decompile-only coverage.'''
    from memory import Memory

    for filename in sorted(os.listdir(DATA_DIR)):
        if not filename.endswith(".dm41"):
            continue
        memory = Memory.from_file(DATA_DIR / filename)
        for program in memory.programs.list_programs():
            yield filename, program, memory.programs.get_program_bytes(program)


def test_decode_program_txt_round_trips_every_sample_program_modulo_memory_state():
    '''Broader coverage than the tower fixture alone: every real program
    in every .dm41 sample state, decompiled and recompiled, should
    reproduce the original bytes exactly once the memory-state-only
    fields _normalize_memory_state_fields() describes are normalized
    away on both sides. The one further exception, "twolabels.dm41"'s
    second program (captured with a permanent ".END." terminator, which
    -- see _normalize_memory_state_fields()'s own docstring -- can never
    survive a text round trip since it decodes identically to a plain
    END), is carved out explicitly rather than silently ignored.

    Unneeded NULLs (an unpacked program's) are dropped on decompile, as
    hp41uc does, so a program containing them recompiles shorter. For
    those, the check is that the recompiled program decompiles to the
    same listing (apart from the END trailer's byte count).'''
    mismatches = []
    nulls_dropped = 0
    for filename, program, instruction_bytes in _dm41_sample_programs():
        if filename == "twolabels.dm41" and program.terminator == ".END.":
            continue
        text = encode_program_txt(instruction_bytes)
        recompiled = decode_program_txt(text)
        if _normalize_memory_state_fields(recompiled) == _normalize_memory_state_fields(
            instruction_bytes
        ):
            continue
        if (
            len(recompiled) < len(instruction_bytes)
            and encode_program_txt(recompiled).splitlines()[:-1]
            == text.splitlines()[:-1]
        ):
            nulls_dropped += 1
            continue
        mismatches.append(f"{filename} {program.labels}: content mismatch")
    assert not mismatches, "\n".join(mismatches)
    # 3x-xm.dm41/manyfiles.dm41's XMBCD and XMALPHA are unpacked.
    assert nulls_dropped > 0


def test_decode_program_txt_cannot_recover_permanent_end_marker():
    '''Documents the one known, inherent round-trip gap: a program whose
    real terminator is the permanent ".END." sentinel (end_type 2, not a
    plain END's end_type 0) decompiles to text indistinguishable from a
    plain END (encode_program_txt()'s own docstring says as much), so
    recompiling that text always produces a plain END back -- not a bug,
    a property of the text format itself having no distinct spelling for
    ".END.".'''
    from memory import Memory
    from memory.program_chain import decode_chain_marker

    memory = Memory.from_file(DATA_DIR / "twolabels.dm41")
    program = next(
        p for p in memory.programs.list_programs() if p.terminator == ".END."
    )
    instruction_bytes = memory.programs.get_program_bytes(program)
    original_end = decode_chain_marker(instruction_bytes, len(instruction_bytes) - 3)
    assert original_end["end_type"] == 2  # confirms this fixture exercises .END.

    recompiled = decode_program_txt(encode_program_txt(instruction_bytes))
    recompiled_end = decode_chain_marker(recompiled, len(recompiled) - 3)
    assert recompiled_end["end_type"] == 0  # always a plain END, never .END.


def test_decode_program_txt_local_letter_labels_round_trip():
    '''src/tests/data/samplelabels.dm41 -- a real DM41 capture built
    specifically to pin down local letter labels A-J's compact 2-byte
    form (see this module's own top-of-file docstring) -- round-trips
    through text modulo only the memory-state fields
    _normalize_memory_state_fields() describes.'''
    from memory import Memory

    memory = Memory.from_file(DATA_DIR / "samplelabels.dm41")
    program = memory.programs.list_programs()[0]
    instruction_bytes = memory.programs.get_program_bytes(program)
    recompiled = decode_program_txt(encode_program_txt(instruction_bytes))
    assert _normalize_memory_state_fields(recompiled) == _normalize_memory_state_fields(
        instruction_bytes
    )


def test_decode_program_txt_numeric_literal_separator_round_trips():
    '''src/tests/data/numtest.dm41 -- a real DM41 capture built
    specifically to test the "two adjacent numeric literals need an
    inserted 0x00 separator byte" quirk (plan doc sec 2.3). This is also
    the fixture that caught a real bug during this module's own
    development: encode_program_txt() was originally *merging* the two
    separator-divided digit runs into one over-long displayed number
    ("1234567890") instead of two separate lines ("1234567890" split as
    "12345"/"67890") -- fixed to match the plan's own description of
    hp41uc's decompiler ("a Python decompiler emits two plain number
    lines back to back"), confirmed directly against this fixture.'''
    from memory import Memory

    memory = Memory.from_file(DATA_DIR / "numtest.dm41")
    program = memory.programs.list_programs()[0]
    instruction_bytes = memory.programs.get_program_bytes(program)

    text = encode_program_txt(instruction_bytes)
    lines = text.splitlines()
    assert "12345" in lines
    assert "67890" in lines
    assert "1234567890" not in lines  # the two runs must not be merged

    recompiled = decode_program_txt(text)
    assert _normalize_memory_state_fields(recompiled) == _normalize_memory_state_fields(
        instruction_bytes
    )


# -- Hand-authored short test programs, one per instruction category
# (docs/program_text_io_plan.md sec 7's own phase-2 description) --------


def test_decode_program_txt_single_byte_category():
    text = 'LBL "T1"\nSIN\nCOS\n+\nEND\n'
    compiled = decode_program_txt(text)
    # LBL "T1" header (6 bytes: c0 00 f3 <key> 'T' '1') + three
    # single-byte instructions + the 3-byte END.
    assert len(compiled) == 6 + 3 + 3
    assert compiled[6:9] == bytes([0x59, 0x5A, 0x40])  # SIN, COS, +
    assert compiled[-3:].hex() == "c0000d"  # the terminating END
    assert encode_program_txt(compiled).splitlines()[1:4] == ["SIN", "COS", "+"]


def test_ln1_plus_x_name():
    # 0x65 is LN1+X on the HP-41 (and in hp41uc); functions.py used to
    # spell it LNX+1, which hp41uc rejects.
    compiled = decode_program_txt('LBL "T1"\nLN1+X\nEND\n')
    assert compiled[6] == 0x65
    assert encode_program_txt(compiled).splitlines()[1] == "LN1+X"


def test_decode_program_txt_postfix_register_category():
    text = (
        'LBL "T2"\n'
        "RCL 07\n"  # compact single-byte form (register <= 15)
        "RCL 16\n"  # general 2-byte form (register > 15)
        "STO IND Y\n"  # general form, indirect + stack register
        "FIX 3\n"  # small-digit-operand form
        "END\n"
    )
    compiled = decode_program_txt(text)
    decompiled = encode_program_txt(compiled).splitlines()
    assert decompiled == [
        'LBL "T2"',
        "RCL 07",
        "RCL 16",
        "STO IND Y",
        "FIX 3",
        "END ;16 BYTES",
    ]


def test_decode_program_txt_global_label_category():
    text = 'LBL "MAIN"\nXEQ "SUB1"\nGTO "MAIN"\nEND\n'
    compiled = decode_program_txt(text)
    decompiled = encode_program_txt(compiled).splitlines()
    assert decompiled[0] == 'LBL "MAIN"'
    assert decompiled[1] == 'XEQ "SUB1"'
    assert decompiled[2] == 'GTO "MAIN"'
    assert decompiled[3].startswith("END ;")


def test_decode_program_txt_alpha_text_with_escapes_category():
    # \X escape (capital, per plan sec 3.1), \nnn decimal escape, a
    # C-style single-letter escape (\n, accepted per plan sec 5's
    # decision but never emitted by the encoder), and a literal
    # printable character that FOCAL reassigned (^, 0x5E -- see this
    # module's own _encode_alpha_content() docstring) that needs no
    # escape at all.
    text = 'LBL "T4"\n"X\\X41\\066\\n^Y"\nEND\n'
    compiled = decode_program_txt(text)
    # Recover the raw ALPHA content bytes directly (mirroring
    # encode_program_txt()'s own global-label header_len formula) to
    # check the escapes resolved to the right byte values, independent
    # of how encode_program_txt() would choose to re-render them.
    header_len = 4 + len("T4")
    content_len = compiled[header_len] & 0x0F
    content = compiled[header_len + 1 : header_len + 1 + content_len]
    assert content == b"X" + b"A" + b"B" + b"\n" + b"^" + b"Y"  # \X41='A', \066='B'


def test_decode_program_txt_alpha_escape_byte_values():
    '''Narrower, less error-prone version of the escape test above --
    each escape checked individually against its own expected byte.'''
    from memory.program_text import _decode_alpha_text_literal

    assert _decode_alpha_text_literal("\\X41") == b"A"  # hex escape
    assert _decode_alpha_text_literal("\\101") == bytes([101])  # DECIMAL, not octal
    assert _decode_alpha_text_literal("\\n") == b"\n"  # C-style
    assert _decode_alpha_text_literal("\\t") == b"\t"
    assert _decode_alpha_text_literal('\\"') == b'"'
    assert _decode_alpha_text_literal("^") == b"^"  # literal, no escape needed
    assert _decode_alpha_text_literal("plain text") == b"plain text"


def test_decode_program_txt_alpha_append_notation():
    text = 'LBL "T4"\n>":"\nEND\n'
    compiled = decode_program_txt(text)
    decompiled = encode_program_txt(compiled).splitlines()
    assert decompiled[1] == '>":"'


def test_decode_program_txt_xrom_category():
    text = 'LBL "T5"\nXROM 25,46\nEND\n'
    compiled = decode_program_txt(text)
    decompiled = encode_program_txt(compiled).splitlines()
    assert decompiled[1] == "XROM 25,46 ;X<>F"


def test_decode_program_txt_xrom_accepts_any_module():
    '''Any module 00-31 / function 00-63 compiles, emulated or not
    (hp41uc does the same). A7 83 is the card reader's XROM 30,03, from
    GhostTown.ppc.'''
    compiled = decode_program_txt(
        'LBL "T6"\nXROM 30,03\nXROM 1,01\nXROM 25,00\nXROM 31,63\nEND\n'
    )
    assert bytes([0xA7, 0x83]) in compiled
    assert bytes([0xA0, 0x41]) in compiled
    assert bytes([0xA6, 0x40]) in compiled
    assert bytes([0xA7, 0xFF]) in compiled
    lines = encode_program_txt(compiled).splitlines()
    assert lines[1:5] == ["XROM 30,03", "XROM 01,01", "XROM 25,00", "XROM 31,63"]


def test_decode_program_txt_xrom_rejects_out_of_range():
    for bad in ("XROM 32,00", "XROM 25,64", "XROM 99,99"):
        with pytest.raises(ValueError, match="out of range"):
            decode_program_txt(f'LBL "T7"\n{bad}\nEND\n')


def test_decode_program_txt_xrom_accepts_extended_function_mnemonic():
    # "SEEKPT" (Extended Functions, module 25) written by name should
    # compile to the exact same bytes as its numeric "XROM 25,42" form.
    by_name = decode_program_txt('LBL "T9"\nSEEKPT\nEND\n')
    by_number = decode_program_txt('LBL "T9"\nXROM 25,42\nEND\n')
    assert by_name == by_number
    assert encode_program_txt(by_name).splitlines()[1] == "XROM 25,42 ;SEEKPT"


def test_decode_program_txt_xrom_accepts_time_function_mnemonic():
    # "TIME" (Time module, module 26) written by name should compile to
    # the exact same bytes as its numeric "XROM 26,28" form.
    by_name = decode_program_txt('LBL "TA"\nTIME\nEND\n')
    by_number = decode_program_txt('LBL "TA"\nXROM 26,28\nEND\n')
    assert by_name == by_number
    assert encode_program_txt(by_name).splitlines()[1] == "XROM 26,28 ;TIME"


def test_decode_program_txt_xrom_mnemonic_is_case_insensitive():
    lower = decode_program_txt('LBL "TB"\nseekpt\nEND\n')
    upper = decode_program_txt('LBL "TB"\nSEEKPT\nEND\n')
    assert lower == upper


def test_decode_program_txt_xrom_mnemonic_ascii_symbol_substitution():
    # memory/mnemonics.py's "sigma" substitution should let
    # an ASCII-typed "sigmareg?" resolve to the real "ΣREG?" XROM name
    # (0xA6,0x78), the same way it already does for single-byte functions.
    by_typed = decode_program_txt('LBL "TC"\nsigmareg?\nEND\n')
    by_number = decode_program_txt('LBL "TC"\nXROM 25,56\nEND\n')
    assert by_typed == by_number


def test_decode_program_txt_xrom_mnemonic_rejects_operand():
    with pytest.raises(ValueError, match="unexpected operand"):
        decode_program_txt('LBL "TD"\nSEEKPT 1\nEND\n')


def test_decode_program_txt_xrom_unknown_mnemonic_still_rejected():
    with pytest.raises(ValueError, match="unrecognized instruction"):
        decode_program_txt('LBL "TE"\nNOTAREALFUNCTION\nEND\n')


def test_decode_program_txt_end_category():
    compiled = decode_program_txt('LBL "T8"\nEND\n')
    assert compiled[-3:].hex() == "c0000d"


def test_decode_program_txt_unknown_opcode_passthrough_round_trips():
    '''A synthetic/unassigned opcode (one of the two confirmed-spare
    bytes, 0xAF -- see this module's own _SPARE_OPCODES) renders as an
    informational comment on decompile; recompiling that comment,
    unedited, must reproduce the exact original bytes -- plan doc sec 5's
    decompile-edit-recompile round-trip requirement.'''
    original = bytes([0xAF, 0x03])
    text = encode_program_txt(original)
    assert text.splitlines() == ["; UNKNOWN OPCODE: AF 03"]
    recompiled_body = decode_program_txt(text + "END\n")
    assert recompiled_body[: len(original)] == original


def test_decode_program_txt_missing_end_is_an_error():
    with pytest.raises(ValueError, match="no terminating END"):
        decode_program_txt('LBL "T9"\nSIN\n')


def test_decode_program_txt_unrecognized_instruction_is_an_error():
    with pytest.raises(ValueError, match="unrecognized instruction"):
        decode_program_txt('LBL "T10"\nNOTAREALINSTRUCTION\nEND\n')


def test_decode_program_txt_content_after_end_is_an_error():
    with pytest.raises(ValueError, match="after program's terminating END"):
        decode_program_txt('LBL "T11"\nEND\nSIN\n')


def test_decode_program_txt_unterminated_quote_is_an_error():
    with pytest.raises(ValueError, match="unterminated"):
        decode_program_txt('LBL "T12"\n"unterminated\nEND\n')


def test_decode_program_txt_compact_vs_general_thresholds():
    '''The compact-vs-general boundary is mechanical (a register/local
    number <= a fixed max gets the compact form, > it gets the general
    form) -- exercised right at the boundary on both sides, for every
    mnemonic that has a compact form at all.'''
    text = (
        'LBL "TH"\n'
        "RCL 15\nRCL 16\n"
        "STO 15\nSTO 16\n"
        "LBL 14\nLBL 15\n"
        "GTO 14\nGTO 15\n"
        "END\n"
    )
    compiled = decode_program_txt(text)
    hexed = compiled.hex()
    assert "2f" in hexed  # RCL 15 compact (0x20+15)
    assert "9010" in hexed  # RCL 16 general (0x90, 0x10)
    assert "3f" in hexed  # STO 15 compact (0x30+15)
    assert "9110" in hexed  # STO 16 general (0x91, 0x10)
    assert "0f" in hexed  # LBL 14 compact (0x01+14)
    assert "cf0f" in hexed  # LBL 15 general (0xCF, 0x0F)
    assert "bf00" in hexed  # GTO 14 compact (0xB1+14, fixed 0x00)
    assert "d0000f" in hexed  # GTO 15 general (0xD0, 0x00, 0x0F)


def test_decode_program_txt_gto_ind_and_xeq_never_compact():
    text = 'LBL "TI"\nGTO IND X\nXEQ 03\nEND\n'
    compiled = decode_program_txt(text)
    assert compiled.hex().find("ae73") != -1  # GTO IND X -> 0xAE, descriptor 0x73
    assert compiled.hex().find("e00003") != -1  # XEQ 03 -> always general 3-byte


# -- NULL bytes and XROMs from other modules (GhostTown.ppc, 2026-09-22) ---


def test_encode_program_txt_skips_null_before_number():
    '''An unpacked program keeps the NULL the HP-41 inserts in front of
    every keyed-in number, even after a non-number instruction. It's a
    no-op, so it's skipped silently, as hp41uc does.'''
    data = bytes([0x91, 0xF0, 0x00, 0x11, 0x92, 0x04])  # STO IND T, NULL, 1, ST+ 04
    assert encode_program_txt(data).splitlines() == ["STO IND T", "1", "ST+ 04"]


def test_null_before_number_dropped_on_round_trip():
    '''The unneeded NULL is gone after a round trip; the one separating
    two adjacent numbers is re-inserted.'''
    text = encode_program_txt(
        bytes([0x91, 0xF0, 0x00, 0x11, 0x00, 0x12, 0xC0, 0x00, 0x0D])
    )  # STO IND T, NULL, 1, NULL, 2, END
    assert text.splitlines()[:3] == ["STO IND T", "1", "2"]
    assert decode_program_txt(text)[:6] == bytes([0x91, 0xF0, 0x11, 0x00, 0x12, 0xC0])


def test_encode_program_txt_skips_stray_nulls():
    data = bytes([0x00, 0x00, 0x87, 0x00, 0x85])  # NULL NULL CLA NULL RTN
    assert encode_program_txt(data).splitlines() == ["CLA", "RTN"]


def test_encode_program_txt_decodes_xrom_from_any_module():
    '''0xA0-0xA7 are all XROM prefixes, not just 0xA6 (Extended
    Functions/Time). Unknown modules get no name comment.'''
    data = bytes(
        [
            0xA7, 0x83,  # XROM 30,03 (card reader)
            0xA0, 0x41,  # XROM 01,01
            0xA6, 0x6A,  # XROM 25,42 ;SEEKPT
        ]
    )
    assert encode_program_txt(data).splitlines() == [
        "XROM 30,03",
        "XROM 01,01",
        "XROM 25,42 ;SEEKPT",
    ]


# -- GTO IND / XEQ IND (shared opcode 0xAE) --------------------------------
#
# The operand's high bit selects XEQ IND; it is not the usual "indirect"
# flag. See _XEQ_IND_FLAG's comment in memory/program_text.py for sources.


def test_encode_program_txt_decodes_xeq_ind():
    data = bytes(
        [
            0xAE, 0x05,  # GTO IND 05
            0xAE, 0x85,  # XEQ IND 05
            0xAE, 0x73,  # GTO IND X
            0xAE, 0xF3,  # XEQ IND X
            0xAE, 0xAA,  # XEQ IND 42 (Wickes' own example)
        ]
    )
    lines = encode_program_txt(data).splitlines()
    assert lines == [
        "GTO IND 05",
        "XEQ IND 05",
        "GTO IND X",
        "XEQ IND X",
        "XEQ IND 42",
    ]


def test_decode_program_txt_xeq_ind():
    compiled = decode_program_txt('LBL "XI"\nXEQ IND 05\nXEQ IND X\nEND\n')
    assert bytes([0xAE, 0x85]) in compiled
    assert bytes([0xAE, 0xF3]) in compiled


def test_decode_program_txt_gto_ind_keeps_high_bit_clear():
    compiled = decode_program_txt('LBL "GI"\nGTO IND 42\nEND\n')
    assert bytes([0xAE, 0x2A]) in compiled  # Wickes: AE 2A is GTO IND 42


def test_decode_program_txt_gto_xeq_ind_round_trip():
    text = (
        'LBL "IND"\n'
        "GTO IND 05\n"
        "XEQ IND 05\n"
        "XEQ IND L\n"
        "GTO IND a\n"
        "XEQ IND e\n"
        "END\n"
    )
    lines = encode_program_txt(decode_program_txt(text)).splitlines()
    assert lines[1:-1] == [
        "GTO IND 05",
        "XEQ IND 05",
        "XEQ IND L",
        "GTO IND a",
        "XEQ IND e",
    ]


def test_decode_program_txt_xeq_ind_rejects_bad_operands():
    for bad in ("XEQ IND", "XEQ IND 05 06", "XEQ IND IND 05", "XEQ IND 100"):
        with pytest.raises(ValueError):
            decode_program_txt(f'LBL "B"\n{bad}\nEND\n')


# -- Synthetic-only status-register postfixes (M/N/O/P/Q/R/a/b/c/d/e) ------
#
# See memory/program_text.py's own module-level comment above
# _STACK_REGISTER_NAMES for the W.C. Wickes citation these bytes are
# confirmed against, and for 0x7A ("R").


def test_encode_program_txt_decodes_synthetic_status_registers():
    data = bytes(
        [
            0x91, 0x75,  # STO M
            0x90, 0x76,  # RCL N
            0xCE, 0x77,  # X<> O
            0x9B, 0x78,  # ARCL P
            0x98, 0x79,  # VIEW Q
            0x91, 0x7B,  # STO a
            0x91, 0x7C,  # STO b
            0x91, 0x7D,  # STO c
            0x91, 0x7E,  # STO d
            0x91, 0x7F,  # STO e
        ]
    )
    lines = encode_program_txt(data).splitlines()
    assert lines == [
        "STO M",
        "RCL N",
        "X<> O",
        "ARCL P",
        "VIEW Q",
        "STO a",
        "STO b",
        "STO c",
        "STO d",
        "STO e",
    ]


def test_encode_program_txt_decodes_synthetic_status_registers_indirect():
    data = bytes(
        [
            0x91, 0xF5,  # STO IND M  (0x80 | 0x75)
            0x90, 0xFB,  # RCL IND a  (0x80 | 0x7B)
            0x91, 0xFE,  # STO IND d  (0x80 | 0x7E)
        ]
    )
    lines = encode_program_txt(data).splitlines()
    assert lines == ["STO IND M", "RCL IND a", "STO IND d"]


def test_encode_program_txt_decodes_0x7a_postfix_as_r():
    '''0x7A is status register R, spelled as hp41uc spells it (see the
    module-level comment above _STACK_REGISTER_NAMES).'''
    data = bytes([0x91, 0x7A, 0x90, 0xFA])  # STO R, RCL IND R
    lines = encode_program_txt(data).splitlines()
    assert lines == ["STO R", "RCL IND R"]


def test_decode_program_txt_0x7a_postfix_round_trip():
    compiled = decode_program_txt('LBL "R"\nSTO R\nRCL IND r\nEND\n')
    assert bytes([0x91, 0x7A]) in compiled
    assert bytes([0x90, 0xFA]) in compiled  # lowercase r folds to R
    lines = encode_program_txt(compiled).splitlines()
    assert lines[1:3] == ["STO R", "RCL IND R"]


def test_decode_program_txt_synthetic_status_registers_round_trip():
    text = (
        'LBL "SYN"\n'
        "STO M\n"
        "RCL N\n"
        "X<> O\n"
        "STO IND P\n"
        "RCL IND Q\n"
        "STO a\n"
        "STO b\n"
        "STO c\n"
        "STO d\n"
        "STO IND e\n"
        "END\n"
    )
    compiled = decode_program_txt(text)
    assert encode_program_txt(compiled).splitlines() == [
        'LBL "SYN"',
        "STO M",
        "RCL N",
        "X<> O",
        "STO IND P",
        "RCL IND Q",
        "STO a",
        "STO b",
        "STO c",
        "STO d",
        "STO IND e",
        "END ;30 BYTES",
    ]


def test_decode_program_txt_synthetic_register_case_distinct_from_local_label():
    '''Lowercase "a".."e" must compile to the synthetic status registers
    (0x7B-0x7F); their uppercase counterparts "A".."E" must keep meaning
    the unrelated, keyboard-reachable local letter labels (0x66-0x6A).
    This is the exact ambiguity _parse_register_base()'s own docstring
    describes -- checked here byte-for-byte in both directions so a
    future change to the lookup order can't silently reintroduce it.'''
    lower = decode_program_txt('LBL "CA"\nSTO a\nEND\n')
    upper = decode_program_txt('LBL "CA"\nSTO A\nEND\n')
    assert lower != upper
    assert bytes([0x91, 0x7B]) in lower  # STO a -> descriptor 0x7B
    assert bytes([0x91, 0x66]) in upper  # STO A -> local label A, 0x66

    lower_e = decode_program_txt('LBL "CE"\nSTO e\nEND\n')
    upper_e = decode_program_txt('LBL "CE"\nSTO E\nEND\n')
    assert bytes([0x91, 0x7F]) in lower_e  # STO e -> descriptor 0x7F
    assert bytes([0x91, 0x6A]) in upper_e  # STO E -> local label E, 0x66+4


def test_decode_program_txt_synthetic_status_register_names_case_insensitive_for_mq():
    '''Unlike a-e, the M/N/O/P/Q names don't collide with anything else
    in the operand-token space (local letter labels stop at J), so they
    keep the same case-insensitive leniency the stack registers T/Z/Y/X/L
    already had.'''
    mixed = decode_program_txt('LBL "CM"\nSTO m\nEND\n')
    canonical = decode_program_txt('LBL "CM"\nSTO M\nEND\n')
    assert mixed == canonical


# -- Resolved jump bytes in GTO/XEQ (manyfiles.dm41's XMBCD, 2026-10-04) ---
#
# A GTO/XEQ's jump-distance bytes are a cache the calculator fills in
# (handbook pp. 45-47); hp41uc's compiler -- the source of every other
# fixture -- always leaves them 0x00 ("unresolved"). Real captured
# programs have real values, and the decompiler must not depend on them.


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("b100", "GTO 00"),  # unresolved (hp41uc style)
        ("b2b1", "GTO 01"),  # backward, 3 bytes + 1 register (XMBCD)
        ("b188", "GTO 00"),  # backward, 0 bytes + 8 registers (XMBCD)
        ("b350", "GTO 02"),  # forward
        ("bfff", "GTO 14"),  # every jump bit set, still GTO 14
    ],
)
def test_encode_program_txt_compact_gto_ignores_jump_byte(raw, expected):
    text = encode_program_txt(bytes.fromhex(raw) + bytes.fromhex("c0000d"))
    assert text.splitlines()[0] == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("d00014", "GTO 20"),  # unresolved
        ("d10014", "GTO 20"),  # bit 0 of the first byte is a register bit
        ("dc0114", "GTO 20"),  # bytes/registers fields set
        ("d00094", "GTO 20"),  # direction bit set in the label byte
        ("e00007", "XEQ 07"),  # unresolved
        ("e42204", "XEQ 04"),  # real forward jump (pi.dm41)
        ("e0009e", "XEQ 30"),  # backward -- not "XEQ IND 30"
        ("ec1f86", "XEQ 06"),  # real backward jump (pi.dm41)
        ("d00068", "GTO C"),  # local letter label
    ],
)
def test_encode_program_txt_long_gto_xeq_ignore_jump_bytes(raw, expected):
    text = encode_program_txt(bytes.fromhex(raw) + bytes.fromhex("c0000d"))
    assert text.splitlines()[0] == expected


def test_encode_program_txt_resolved_jumps_recompile_as_unresolved():
    '''The cached jump distance isn't preserved through text (there's
    nowhere in the text to put it), so recompiling gives the unresolved
    form -- which the calculator re-resolves on first run -- and
    decompiling *that* gives back the same text.'''
    original = bytes.fromhex("b2b1" "b188" "e42204" "c0000d")
    text = encode_program_txt(original)
    assert text.splitlines()[:3] == ["GTO 01", "GTO 00", "XEQ 04"]
    recompiled = decode_program_txt(text)
    assert recompiled.startswith(bytes.fromhex("b200" "b100" "e00004"))
    assert encode_program_txt(recompiled) == text


def test_encode_program_txt_manyfiles_xmbcd_has_no_unknown_opcodes():
    '''The real-hardware program that surfaced the bug: its inner and
    outer loops (`ISG 00 / GTO 01` and `ISG 01 / GTO 00`) came out as
    "; UNKNOWN OPCODE: B2 B1" and "; UNKNOWN OPCODE: B1 88".'''
    from memory import Memory

    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    texts = [
        encode_program_txt(memory.programs.get_program_bytes(p))
        for p in memory.programs.list_programs()
    ]
    (xmbcd,) = [t for t in texts if t.startswith('LBL "XMBCD"')]
    lines = xmbcd.splitlines()
    assert not any("UNKNOWN OPCODE" in line for line in lines)
    assert lines[-7:] == [
        "ISG 00",
        "GTO 01",
        "ISG 01",
        "GTO 00",
        '"DONE"',
        "AVIEW",
        "END ;103 BYTES",
    ]


def test_encode_program_txt_no_unknown_goto_xeq_in_any_fixture():
    '''Sweep: no real sample state has a GTO/XEQ left undecoded.'''
    import os

    from memory import Memory

    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(".dm41"):
            continue
        memory = Memory.from_file(DATA_DIR / filename)
        for program in memory.programs.list_programs():
            text = encode_program_txt(memory.programs.get_program_bytes(program))
            for line in text.splitlines():
                if line.startswith("; UNKNOWN OPCODE:"):
                    first = int(line.split(":")[1].split()[0], 16)
                    assert not (0xB1 <= first <= 0xBF or 0xD0 <= first <= 0xEF), (
                        filename,
                        line,
                    )
