"""Converting between HP-41 program bytes and plain-text listings
(memory/program_text.py): encode_program_txt() decompiles, and
decode_program_txt() compiles.

The strongest checks use tests/data/tower.txt and tower.raw -- hp41uc's own
source and compiled output for a real 1088-byte program -- which this
module must reproduce line for line and byte for byte. Every real program
in tests/data must also survive a text round trip, and test_golden.py pins
each one's decompiled listing. The tables below cover each instruction
category and the compiler's error messages.
"""

import os
from pathlib import Path

import pytest

from memory.opcode_scan import find_program_end, scan_global_markers_forward
from memory.program_files import decode_program_raw
from memory.program_text import decode_program_txt, encode_program_txt

DATA_DIR = Path(__file__).parent / "data"

APPTEST_BYTES = bytes.fromhex("c000f8004150505445535410021140111010468475b200c40309")


def _strip_trailing_comment(line: str) -> str:
    """Quote-aware removal of a trailing `;` comment (tower.txt only ever
    uses `;`, never `#`, for a real comment -- and a couple of its
    mnemonics, like "X#Y?", contain a literal '#' that must NOT be
    mistaken for one)."""
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


def test_decode_program_txt_matches_tower_raw_bytes():
    """The strongest available check: tower.raw is
    *hp41uc's own compiled output* from tower.txt, so a byte-identical
    match here confirms this module's compiler against a real,
    third-party-generated program, not just against its own decompiler.
    tower.txt's own END comment now correctly says ";1088 BYTES" (the
    user corrected the fixture's stale "1084" after independently
    confirming 1088 on real DM41 hardware -- see this module's own
    docstring's "point 2", now historical) -- decode_program_txt() never
    looks at that comment text either way, only at the actual END
    keyword."""
    raw_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    assert len(raw_bytes) == 1088

    tower_text = (DATA_DIR / "tower.txt").read_text(encoding="utf-8")
    compiled = decode_program_txt(tower_text)

    assert compiled == raw_bytes


def test_decode_program_txt_round_trips_encode_program_txt_on_tower():
    """A second, independent round trip in the other direction: feed
    tower.raw's own bytes through this module's own decompiler, then
    straight back through the compiler, and expect the exact same bytes
    back out -- decode(encode(x)) == x, not just decode(hp41uc's text) ==
    hp41uc's bytes."""
    raw_bytes = decode_program_raw((DATA_DIR / "tower.raw").read_bytes())
    assert decode_program_txt(encode_program_txt(raw_bytes)) == raw_bytes


def _normalize_memory_state_fields(data: bytes) -> bytes:
    """Zeroes out exactly the fields decode_program_txt() can never
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
        spliced into is exactly what ProgramMemory.import_program()'s own
        pack()/_forward_scan_programs() repair mechanism already
        handles, per docs/program.md sec 5.4, and belongs there, not in
        a text compiler).
      - A global label header's key-assignment byte -- there's no
        key-assignment syntax anywhere in the program-text format to
        begin with.
      - An END's packed-status nibble -- packing is a separate,
        user-invoked operation,
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
    below) check it directly instead."""
    out = bytearray(data)
    for marker in scan_global_markers_forward(data):
        idx = marker.index
        out[idx] = 0xC0
        out[idx + 1] = 0x00
        if marker.is_label:
            out[idx + 3] = 0x00  # key assignment byte
        else:
            end_type_nibble = marker.third_byte & 0xF0
            out[idx + 2] = end_type_nibble  # packed-status nibble -> 0
    _zero_cached_jump_bytes(data, out)
    return bytes(out)


def _zero_cached_jump_bytes(data: bytes, out: bytearray) -> None:
    """Walks `data` instruction by instruction (the same length classes
    opcode_scan.find_program_end() uses) and clears every GTO/XEQ's cached
    jump distance in `out` -- see _normalize_memory_state_fields()."""
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
    """Yields (filename, program, instruction_bytes) for every real
    program in every .dm41 sample state -- the same fixture set
    test_encode_program_txt_never_raises_on_every_sample_program()
    already exercises for decompile-only coverage."""
    from memory import Memory

    for filename in sorted(os.listdir(DATA_DIR)):
        if not filename.endswith(".dm41"):
            continue
        memory = Memory.from_file(DATA_DIR / filename)
        for program in memory.programs.list_programs():
            yield filename, program, memory.programs.get_program_bytes(program)


def test_decode_program_txt_round_trips_every_sample_program_modulo_memory_state():
    """Broader coverage than the tower fixture alone: every real program
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
    same listing (apart from the END trailer's byte count)."""
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
    """Documents the one known, inherent round-trip gap: a program whose
    real terminator is the permanent ".END." sentinel (end_type 2, not a
    plain END's end_type 0) decompiles to text indistinguishable from a
    plain END (encode_program_txt()'s own docstring says as much), so
    recompiling that text always produces a plain END back -- not a bug,
    a property of the text format itself having no distinct spelling for
    ".END."."""
    from memory import Memory
    from memory.program_chain import decode_chain_marker

    memory = Memory.from_file(DATA_DIR / "twolabels.dm41")
    program = next(
        p for p in memory.programs.list_programs() if p.terminator == ".END."
    )
    instruction_bytes = memory.programs.get_program_bytes(program)
    original_end = decode_chain_marker(instruction_bytes, len(instruction_bytes) - 3)
    assert original_end.end_type == 2  # confirms this fixture exercises .END.

    recompiled = decode_program_txt(encode_program_txt(instruction_bytes))
    recompiled_end = decode_chain_marker(recompiled, len(recompiled) - 3)
    assert recompiled_end.end_type == 0  # always a plain END, never .END.


# -- Instruction categories ---------------------------------------------------
#
# Each row: source lines (wrapped in LBL "T" ... END), the bytes they compile
# to between the label and the END, and the listing they decompile back to
# (None: the same as the source).
ROUND_TRIPS = [
    (["SIN", "COS", "+"], "595a40", None),
    (["LN1+X"], "65", None),
    # Compact single-byte RCL for 00-15, general two-byte form above it.
    (["RCL 07", "RCL 16", "STO IND Y", "FIX 3"], "27901091f29c03", None),
    (['XEQ "SUB1"', 'GTO "MAIN"'], "1ef4535542311df44d41494e", None),
    # \X hex and \nnn decimal escapes; ^ (0x5E) needs none.
    (['"X\\X41\\066\\n^Y"'], "f65841420a5e59", ['"XAB\\010^Y"']),
    (['>":"'], "f27f3a", None),
    (["XROM 25,46"], "a66e", ["XROM 25,46 ;X<>F"]),
    # Any module 00-31 and function 00-63, emulated or not, as hp41uc does.
    (
        ["XROM 30,03", "XROM 1,01", "XROM 25,00", "XROM 31,63"],
        "a783a041a640a7ff",
        ["XROM 30,03", "XROM 01,01", "XROM 25,00", "XROM 31,63"],
    ),
    # XROM functions by name, in any case and with ASCII stand-ins.
    (["SEEKPT"], "a66a", ["XROM 25,42 ;SEEKPT"]),
    (["seekpt"], "a66a", ["XROM 25,42 ;SEEKPT"]),
    (["TIME"], "a69c", ["XROM 26,28 ;TIME"]),
    (["sigmareg?"], "a678", ["XROM 25,56 ;SREG?"]),
    # Compact forms stop at RCL/STO 15, LBL/GTO 14.
    (
        [
            "RCL 15",
            "RCL 16",
            "STO 15",
            "STO 16",
            "LBL 14",
            "LBL 15",
            "GTO 14",
            "GTO 15",
        ],
        "2f90103f91100fcf0fbf00d0000f",
        None,
    ),
    # GTO IND and XEQ never use a compact form; XEQ IND sets the operand's
    # high bit (Wickes: AE 2A is GTO IND 42).
    (["GTO IND X", "XEQ 03"], "ae73e00003", None),
    (["XEQ IND 05", "XEQ IND X", "GTO IND 42"], "ae85aef3ae2a", None),
    (
        ["GTO IND 05", "XEQ IND 05", "XEQ IND L", "GTO IND a", "XEQ IND e"],
        "ae05ae85aef4ae7baeff",
        None,
    ),
    # Synthetic status registers M-Q and a-e (0x75-0x7F) and R (0x7A).
    (
        [
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
        ],
        "91759076ce7791f890f9917b917c917d917e91ff",
        None,
    ),
    (["STO R", "RCL IND r"], "917a90fa", ["STO R", "RCL IND R"]),
    # Lowercase a-e are status registers, uppercase A-E local labels;
    # M-Q are case-insensitive.
    (
        ["STO a", "STO A", "STO e", "STO E", "STO m"],
        "917b9166917f916a9175",
        ["STO a", "STO A", "STO e", "STO E", "STO M"],
    ),
]


@pytest.mark.parametrize("source, body, listing", ROUND_TRIPS, ids=lambda v: None)
def test_an_instruction_compiles_and_decompiles(source, body, listing):
    compiled = decode_program_txt('LBL "T"\n' + "\n".join(source) + "\nEND\n")
    assert compiled[:5] == bytes.fromhex("c000f20054")  # LBL "T"
    assert compiled[5:-3].hex() == body
    assert compiled[-3:].hex() == "c0000d"  # END
    assert encode_program_txt(compiled).splitlines()[1:-1] == (listing or source)


# Bytes that decompile to a listing but aren't produced by the compiler:
# unpacked programs' NULLs, and the jump distances a calculator caches in
# GTO/XEQ (hp41uc never sets them).
DECOMPILES = [
    ("91f0001192 04", ["STO IND T", "1", "ST+ 04"]),  # NULL before a number
    ("0000870085", ["CLA", "RTN"]),  # stray NULLs
    ("a783a041a66a", ["XROM 30,03", "XROM 01,01", "XROM 25,42 ;SEEKPT"]),
    (
        "ae05ae85ae73aef3aeaa",
        ["GTO IND 05", "XEQ IND 05", "GTO IND X", "XEQ IND X", "XEQ IND 42"],
    ),
    ("91f590fb91fe", ["STO IND M", "RCL IND a", "STO IND d"]),
    ("917a90fa", ["STO R", "RCL IND R"]),
    # Compact GTO: the second byte is the cached jump, never part of the label.
    ("b100c0000d", ["GTO 00", "END ;5 BYTES"]),
    ("b2b1c0000d", ["GTO 01", "END ;5 BYTES"]),
    ("b188c0000d", ["GTO 00", "END ;5 BYTES"]),
    ("b350c0000d", ["GTO 02", "END ;5 BYTES"]),
    ("bfffc0000d", ["GTO 14", "END ;5 BYTES"]),
    # Long GTO/XEQ: only the label byte's low 7 bits name the label.
    ("d00014c0000d", ["GTO 20", "END ;6 BYTES"]),
    ("d10014c0000d", ["GTO 20", "END ;6 BYTES"]),
    ("dc0114c0000d", ["GTO 20", "END ;6 BYTES"]),
    ("d00094c0000d", ["GTO 20", "END ;6 BYTES"]),
    ("e00007c0000d", ["XEQ 07", "END ;6 BYTES"]),
    ("e42204c0000d", ["XEQ 04", "END ;6 BYTES"]),
    ("e0009ec0000d", ["XEQ 30", "END ;6 BYTES"]),  # not "XEQ IND 30"
    ("ec1f86c0000d", ["XEQ 06", "END ;6 BYTES"]),
    ("d00068c0000d", ["GTO C", "END ;6 BYTES"]),  # local letter label
]


@pytest.mark.parametrize("raw, listing", DECOMPILES, ids=lambda v: None)
def test_bytes_decompile_to_a_listing(raw, listing):
    assert (
        encode_program_txt(bytes.fromhex(raw.replace(" ", ""))).splitlines() == listing
    )


COMPILE_ERRORS = [
    ('LBL "T"\nSIN\n', "no terminating END"),
    ('LBL "T"\nNOTAREALINSTRUCTION\nEND\n', "unrecognized instruction"),
    ('LBL "T"\nEND\nSIN\n', "line 3: the program starting here has no terminating"),
    ('LBL "T"\n"unterminated\nEND\n', "unterminated"),
    ('LBL "T"\nXROM 32,00\nEND\n', "out of range"),
    ('LBL "T"\nXROM 25,64\nEND\n', "out of range"),
    ('LBL "T"\nXROM 99,99\nEND\n', "out of range"),
    ('LBL "T"\nSEEKPT 1\nEND\n', "unexpected operand"),
    ('LBL "T"\nXEQ IND\nEND\n', None),
    ('LBL "T"\nXEQ IND 05 06\nEND\n', None),
    ('LBL "T"\nXEQ IND IND 05\nEND\n', None),
    ('LBL "T"\nXEQ IND 100\nEND\n', None),
]


@pytest.mark.parametrize("text, message", COMPILE_ERRORS, ids=lambda v: None)
def test_bad_program_text_is_rejected(text, message):
    with pytest.raises(ValueError, match=message):
        decode_program_txt(text)


# -- Escapes, NULLs and other round-trip details ---------------------------------


def test_decode_program_txt_alpha_escape_byte_values():
    """Narrower, less error-prone version of the escape test above --
    each escape checked individually against its own expected byte."""
    from memory.program_text import _decode_alpha_text_literal

    assert _decode_alpha_text_literal("\\X41") == b"A"  # hex escape
    assert _decode_alpha_text_literal("\\101") == bytes([101])  # DECIMAL, not octal
    assert _decode_alpha_text_literal("\\n") == b"\n"  # C-style
    assert _decode_alpha_text_literal("\\t") == b"\t"
    assert _decode_alpha_text_literal('\\"') == b'"'
    assert _decode_alpha_text_literal("^") == b"^"  # literal, no escape needed
    assert _decode_alpha_text_literal("plain text") == b"plain text"


def test_decode_program_txt_unknown_opcode_passthrough_round_trips():
    """A synthetic/unassigned opcode (one of the two confirmed-spare
    bytes, 0xAF -- see this module's own _SPARE_OPCODES) renders as an
    informational comment on decompile; recompiling that comment,
    unedited, must reproduce the exact original bytes, so a
    decompile-edit-recompile round trip is lossless."""
    original = bytes([0xAF, 0x03])
    text = encode_program_txt(original)
    assert text.splitlines() == ["; UNKNOWN OPCODE: AF 03"]
    recompiled_body = decode_program_txt(text + "END\n")
    assert recompiled_body[: len(original)] == original


def test_null_before_number_dropped_on_round_trip():
    """The unneeded NULL is gone after a round trip; the one separating
    two adjacent numbers is re-inserted."""
    text = encode_program_txt(
        bytes([0x91, 0xF0, 0x00, 0x11, 0x00, 0x12, 0xC0, 0x00, 0x0D])
    )  # STO IND T, NULL, 1, NULL, 2, END
    assert text.splitlines()[:3] == ["STO IND T", "1", "2"]
    assert decode_program_txt(text)[:6] == bytes([0x91, 0xF0, 0x11, 0x00, 0x12, 0xC0])


def test_encode_program_txt_resolved_jumps_recompile_as_unresolved():
    """The cached jump distance isn't preserved through text (there's
    nowhere in the text to put it), so recompiling gives the unresolved
    form -- which the calculator re-resolves on first run -- and
    decompiling *that* gives back the same text."""
    original = bytes.fromhex("b2b1b188e42204c0000d")
    text = encode_program_txt(original)
    assert text.splitlines()[:3] == ["GTO 01", "GTO 00", "XEQ 04"]
    recompiled = decode_program_txt(text)
    assert recompiled.startswith(bytes.fromhex("b200b100e00004"))
    assert encode_program_txt(recompiled) == text
