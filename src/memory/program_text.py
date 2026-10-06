'''
Converting between raw HP-41 program instruction bytes and a plain-text
keystroke listing -- see docs/program_text_io_plan.md's suggested
phasing (sec. 7): phase 1, `encode_program_txt()` (bytes -> text,
decompile), and phase 2, `decode_program_txt()` (text -> bytes, compile).

`encode_program_txt()` turns a program's instruction bytes (the same
bytes `ProgramMemory.get_program_bytes()`/`decode_program_raw()`/
`decode_program_dat()` deal in) into text closely matching hp41uc's own
decompiler conventions (Leo Duran's HP-41 User-Code File Converter,
~/Work/hp41uc/Source/decomp.c) -- one instruction per line, `;` comments,
quoted ALPHA strings, `XROM mm,ff` with a name comment, and so on.

`decode_program_txt()` is its reverse: it tokenizes that same kind of
text (hp41uc's own `tower.txt`/`tower-update2.txt` sample included, not
just this module's own decompile output) and reassembles the exact
instruction bytes, reusing the same opcode table -- see its own
docstring, further down, for the compile-specific design notes (the
tokenizer, the two-numeric-literals-need-a-0x00-separator quirk on the
way back in, and the "every global marker's chain-link fields are always
zero" finding that makes `LBL "NAME"`/`END` byte-identical to hp41uc's
own compiled output without any cross-instruction bookkeeping).

The opcode table itself was derived by cross-checking three sources
against each other, since ~/Work/hp41uc's C source isn't reachable from
every environment this project is developed in (a fourth source, added
later, covers the synthetic-only status-register postfixes M/N/O/P/Q/
R/a/b/c/d/e -- see the module-level comment above _STACK_REGISTER_NAMES):

  1. `opcode_scan.py`'s byte-length classification (itself a port of
     hp41uc's `seek_end()`) -- which byte ranges are 1/2/3-byte or
     variable-length ALPHA text. This module's own dispatch mirrors
     those exact ranges, so it can never desync from the already-tested
     length scanner.
  2. `functions.py`'s SINGLE_BYTE_FUNCTIONS/XROM_FUNCTIONS tables --
     already confirmed (per docs/program_text_io_plan.md sec 3) to give
     the correct in-*program*-byte instruction for every entry at 0x40
     and above, and the correct XROM (0xA6, byte2) table for the two
     ROM modules (Extended Functions, Time) the DM41L emulates. The
     instruction *names* come from memory/mnemonics.py, which builds on
     those tables: export writes hp41uc's canonical names, and import
     accepts any spelling mnemonics.resolve() knows
     (docs/mnemonic_dialects_plan.md).
  3. `src/tests/data/tower.raw`/`tower.txt` -- a real 1088-byte program
     hp41uc itself compiled and decompiled, used to empirically pin down
     every byte range/format `functions.py` doesn't already cover:
     digit-literal encoding, the RCL/STO compact single-byte forms,
     compact local-numbered LBL/GTO forms, the IND/stack-register operand
     scheme, the "append to ALPHA" leading-`>` notation, the XROM
     "mm,ff" catalog-number formula, and the exact END trailer spacing.
     See that fixture's own module docstring in test_program_export.py
     for its provenance.

A few of the derivations below are marked "inferred, not directly
observed" where tower.txt doesn't happen to exercise that specific byte
value -- they follow the same pattern as a directly-confirmed neighbor
(e.g. the stack-register byte assignments for T/Z/L, sitting either side
of the two directly-observed values for Y and X) rather than being
guesses out of nothing, but should be revisited if a fixture ever
contradicts them.

  4. W.C. Wickes' *Synthetic Programming on the HP-41C* (1980) -- the
     same book docs/program.md sec 5.1 already cites for the chain-marker
     distance math -- cross-checked against docs/pdfs/byte_table.html, a
     second, independent full byte table. Used only for the
     synthetic-only status-register postfixes (M/N/O/P/Q/R/a/b/c/d/e);
     see the module-level comment above _STACK_REGISTER_NAMES for
     exactly what's confirmed and what's merely named-but-undescribed.
'''

import re
from typing import List, Optional, Tuple

from .mnemonics import (
    END,
    GTO,
    LBL,
    XEQ,
    XROM,
    OpKind,
    canonical,
    function_op,
    is_known,
    resolve,
    xrom_op,
)
from .program_chain import decode_chain_marker, encode_chain_marker

# -- Digit-literal ("number") encoding ---------------------------------
#
# A numeric literal is spelled out byte-for-byte in normal left-to-right
# reading order, one byte per character, using this table -- confirmed
# against every number literal in tower.txt (integers, decimals, negative
# numbers, and scientific notation): e.g. "-1" is bytes 0x1C, 0x11 (the
# CHS/minus glyph, then digit '1') and "1.006" is 0x11, 0x1A, 0x10, 0x10,
# 0x16 ('1', '.', '0', '0', '6'). hp41uc's own decompiler inserts a single
# space between the mantissa and a trailing "E<exponent>" (tower.txt's
# "3 E3" for 3000, vs. bare "E2" when there's no mantissa before it at
# all) -- see _render_number_run() below.
_DIGIT_CHARS = {
    0x10: "0", 0x11: "1", 0x12: "2", 0x13: "3", 0x14: "4",
    0x15: "5", 0x16: "6", 0x17: "7", 0x18: "8", 0x19: "9",
    0x1A: ".", 0x1B: "E", 0x1C: "-",
}

# -- Compact single-byte RCL/STO forms (registers 00-15 only) ----------
#
# NOT part of functions.py's tables (which are keyed to the Key
# Assignment Register's encoding, where these compact program-only forms
# don't exist at all -- see that module's own docstring). Confirmed
# against tower.txt: "RCL 07"/"RCL 14"/"RCL 15" all decompile to a single
# byte (0x27, 0x2E, 0x2F -- i.e. 0x20 + register), and "STO 07"..."STO 15"
# likewise (0x37..0x3F, i.e. 0x30 + register). Register 16 and up always
# use the general 2-byte 0x90/0x91-prefixed form instead (also confirmed:
# "STO 16" is 0x91 0x10, not a compact form).
_RCL_COMPACT_BASE = 0x20
_STO_COMPACT_BASE = 0x30
_COMPACT_REGISTER_MAX = 0x0F  # registers 00-15

# -- Compact single-byte local-numbered LBL forms (00-14 only) ---------
#
# Confirmed against tower.txt: "LBL 00".."LBL 09" all decompile to a
# single byte, 0x01 + the label number (0x01 = LBL 00, ..., 0x0A = LBL
# 09). Label numbers 15-99 use the general 2-byte 0xCF-prefixed form
# instead (confirmed: "LBL 20"/"LBL 21"/"LBL 92" are all 0xCF <value>).
# Local *letter* labels A-J also use that same 2-byte 0xCF form, with
# values 0x66-0x6F (see _decode_register_operand below) -- confirmed
# separately via src/tests/data/samplelabels.dm41, per project notes.
_LBL_COMPACT_BASE = 0x01
_LBL_COMPACT_MAX = 14

# -- Compact 2-byte local-numbered GTO forms (00-14 only) ---------------
#
# Confirmed against tower.txt: "GTO 00".."GTO 06" all decompile to 2
# bytes, (0xB1 + target number) followed by a second byte that tower.raw
# only ever has as 0x00. That second byte is NOT a fixed/reserved byte,
# though -- it's the instruction's cached *jump distance*, and 0x00 just
# means "not resolved yet" (hp41uc never resolves jumps, so every GTO it
# compiles carries 0x00). A real calculator fills it in the first time
# the GTO runs (or when the program is packed), so a program captured
# from real hardware has real values here -- e.g. XMBCD's `B2 B1` (GTO 01)
# and `B1 88` (GTO 00) in tests/data/manyfiles.dm41. Per *A Programmer's
# Handbook* v2.07 (docs/pdfs, "GTO 00 - 14", p. 45):
#
#     1011 llll  dbbb rrrr
#       llll = label number + 1 (so 0xB1 = GTO 00 ... 0xBF = GTO 14)
#       d    = direction: 0 = forward, 1 = backward
#       bbb  = number of bytes, rrrr = number of registers (7 bytes each)
#
# The decompiler only needs to know *which label* is the target, so the
# jump byte is ignored on decode (see encode_program_txt()'s 2-byte
# branch) and the compiler always writes 0x00 and leaves the calculator
# to resolve it. Local numbers 15-99 (and local letters) use the general
# 3-byte 0xD0-prefixed form instead -- see _GTO_XEQ_LONG_* below.
_GTO_COMPACT_BASE = 0xB1
_GTO_COMPACT_MAX = 14
_GTO_COMPACT_UNRESOLVED_BYTE2 = 0x00

# -- General 3-byte GTO/XEQ forms (any local label) ---------------------
#
# Handbook pp. 46-47 ("GTO 15 - 99", "XEQ"):
#
#     1101 bbbr  rrrr rrrr  dlll llll      (GTO: 0xD0-0xDF)
#     1110 bbbr  rrrr rrrr  dlll llll      (XEQ: 0xE0-0xEF)
#       bbb          = number of bytes
#       r rrrr rrrr  = number of registers (bit 0 of the first byte is the
#                      top bit of the 9-bit register count)
#       d            = direction: 0 = forward, 1 = backward
#       lll llll     = label number / letter code (same descriptor values
#                      as _decode_register_operand's direct range)
#
# Again the jump distance is only a cache: hp41uc's compiler writes
# 0xD0/0xE0, 0x00 (unresolved) and the plain label byte -- tower.txt's
# "GTO 20"/"GTO 73" are D0 00 14 / D0 00 49, "XEQ 07" is E0 00 07 -- while
# a real calculator has resolved most of them (e.g. E4 22 04, or a
# backward jump with the direction bit set in the third byte: E0 00 9E is
# XEQ 30, *not* "XEQ IND 30" -- the high bit means "backward" here, never
# "indirect"). Decoding therefore ignores the jump bits and the direction
# bit entirely and takes only the low 7 bits of the third byte.
_GTO_LONG_FIRST = 0xD0
_XEQ_LONG_FIRST = 0xE0
_LONG_PREFIX_MASK = 0xF0
_LONG_UNRESOLVED_BYTE2 = 0x00
_LONG_LABEL_MASK = 0x7F  # bit 7 is the jump direction, not "indirect"

# 0xAF and 0xB0 -- the two bytes between GTO/XEQ IND (0xAE) and the compact
# GTO block (0xB1-0xBF) -- are confirmed-spare, unassigned opcodes: see
# docs/program_text_io_plan.md sec 2.3 ("hp41uc also emits informational
# ... comments for two truly unassigned 'spare' opcode bytes (0xAF,
# 0xB0)"). They fall in opcode_scan's 2-byte-instruction range, so each
# still consumes one operand byte; see _decode_2byte() below.
_SPARE_OPCODES = frozenset({0xAF, 0xB0})

# 0xAE is shared by GTO IND and XEQ IND. Its operand's high bit selects
# which one: clear = GTO IND, set = XEQ IND; the low 7 bits are the
# register. So unlike every other register-operand instruction, 0xAE's
# high bit is NOT the usual "indirect" flag -- both forms are always
# indirect. Confirmed by three independent sources: Wickes' *Synthetic
# Programming on the HP-41C* ("AE 2A is 'GTO IND 42', whereas AE AA is
# 'XEQ IND 42'"), *A Programmer's Handbook* v2.07 ("1010 1110 trrr rrrr
# ... t is 0=GTO IND or 1=XEQ IND"), and hp41uc's decomp.c.
# docs/pdfs/byte_table.html labels 0xAE "GTO/XEQ IND".
_GTO_XEQ_IND_OPCODE = 0xAE
_XEQ_IND_FLAG = 0x80

# -- Register/flag/data "descriptor" operand byte -----------------------
#
# Shared by every 2-byte instruction that takes a register, flag, or
# other small numeric operand (RCL, STO, ST+/-/*//, ISG, DSE, VIEW,
# SREG, ASTO, ARCL, SF, CF, FS?/FC?(C), X<>, and the compact/general
# LBL and GTO/XEQ forms above). The high bit (0x80) flags "indirect
# through" -- confirmed: "STO IND 16" is STO + 0x90 (0x80 | 0x10), vs.
# plain "STO 16" being STO + 0x10. Below that:
#   0x00-0x63 (0-99): direct register/flag/label number.
#   0x66-0x6F: local letter labels A-J (10 slots -- confirmed elsewhere,
#     see samplelabels.dm41 per project notes; not exercised by name in
#     tower.txt, since it has no letter-labelled GTO/XEQ/LBL, but the
#     encoding matches the already-established local-letter table).
#   0x70-0x74: the stack registers T/Z/Y/X/L. Only X (0x73) and Y (0x72)
#     are directly confirmed in tower.txt ("GTO IND X" and "ARCL X" both
#     decode operand 0x73; "STO IND Y" decodes operand 0xF2 = 0x80 |
#     0x72). T (0x70), Z (0x71), and L/LASTX (0x74) are *inferred* by
#     the consistent surrounding pattern (the four stack registers listed
#     in their conventional T,Z,Y,X display order, with L immediately
#     after) rather than directly observed -- flagged here in case a
#     future fixture disagrees.
#   0x75-0x7F: the "synthetic-only" status-register postfixes.
#     These are unreachable from the keyboard on real hardware (the ALPHA
#     key is disabled while entering an RCL/STO/etc. postfix, per W.C.
#     Wickes' *Synthetic Programming on the HP-41C* (1980) sec 4A -- this
#     project's own already-trusted source for the chain-marker distance
#     math in docs/program.md sec 5.1) but are perfectly valid two-byte
#     instructions once the postfix byte is written by other means.
#     Wickes sec 4A, verbatim: "postfixes 75 through 7F display as 'M',
#     'N', '0', 'P', 'Q', '+', 'a', 'b', 'c', 'd', and 'e', respectively."
#     M/N/O/P are the four physical registers making up the 24-character
#     ALPHA register (sec 4B); Q is a general-purpose scratch register
#     (sec 4C); d holds all 56 system/user flags (sec 4D -- directly
#     demonstrated there: "STO d" with X=0 clears every flag at once);
#     e holds the key-assignment flags (sec 4E). docs/pdfs/
#     byte_table.html independently lists the same ten names for the
#     same ten byte values, cross-confirming them against a second
#     source. a/b/c aren't individually described in the excerpted text
#     beyond a warning that storing into them can be destructive on real
#     hardware ("'0, STO c' causes MEMORY LOST") -- included here anyway
#     since the name itself is unambiguous, and this module's job is
#     representing the byte accurately, not judging its safety to use.
#     Deliberately lowercase, matching Wickes' own glyphs -- see
#     _parse_register_base()'s own docstring for why the encode side
#     must check case *before* any folding: the uppercase spellings A-E
#     are already local letter labels (see 0x66-0x6F above), a completely
#     different, keyboard-reachable feature.
#
# Anything else doesn't decode to a known operand -- callers should treat
# that as an unrecognized/spare instruction (see _format_unknown()).
_STACK_REGISTER_NAMES = {
    0x70: "T",   # inferred -- not directly observed in tower.txt
    0x71: "Z",   # inferred -- not directly observed in tower.txt
    0x72: "Y",   # confirmed: "STO IND Y" -> STO 0xF2 (0x80 | 0x72)
    0x73: "X",   # confirmed: "GTO IND X" -> GTO IND 0x73; "ARCL X" -> ARCL 0x73
    0x74: "L",   # inferred -- not directly observed in tower.txt
    0x75: "M",   # Wickes sec 4A/4B -- one of the 4 ALPHA-register registers
    0x76: "N",   # Wickes sec 4A/4B -- one of the 4 ALPHA-register registers
    0x77: "O",   # Wickes sec 4A/4B -- one of the 4 ALPHA-register registers
    0x78: "P",   # Wickes sec 4A/4B -- one of the 4 ALPHA-register registers
    0x79: "Q",   # Wickes sec 4A/4C -- general-purpose scratch register
    0x7A: "R",   # hp41uc's name; Wickes/byte_table.html show its glyph (a sideways T)
    0x7B: "a",   # Wickes sec 4A -- named, but not individually described
    0x7C: "b",   # Wickes sec 4A -- named, but not individually described
    0x7D: "c",   # Wickes sec 4A -- named, but not individually described
    0x7E: "d",   # Wickes sec 4A/4D -- the 56-bit system/user flag register
    0x7F: "e",   # Wickes sec 4A/4E -- the key-assignment flag register
}


def _decode_register_operand(byte: int) -> Optional[str]:
    '''Decodes a "descriptor" operand byte (see the module-level comment
    above) into its display text -- a 2-digit register/flag number, a
    local letter label, a stack register name, or an "IND "-prefixed
    version of any of those. Returns None if `byte` doesn't decode to
    anything recognized.'''
    indirect = bool(byte & 0x80)
    base = byte & 0x7F
    if 0 <= base <= 0x63:
        core = f"{base:02d}"
    elif 0x66 <= base <= 0x6F:
        core = chr(ord("A") + (base - 0x66))
    elif base in _STACK_REGISTER_NAMES:
        core = _STACK_REGISTER_NAMES[base]
    else:
        return None
    return f"IND {core}" if indirect else core


def _decode_small_digit_operand(byte: int) -> Optional[str]:
    '''FIX/SCI/ENG/TONE's own operand style: a bare 0-9 digit with no
    zero-padding (confirmed: "FIX 0"/"FIX 2"/"TONE 0"/"TONE 2", never
    "FIX 00"/"TONE 02" -- contrast with _decode_register_operand()'s
    2-digit style used by every other operand-taking instruction). Falls
    back to the general register-descriptor decode for anything outside
    0-9 (e.g. an IND form), which isn't exercised by tower.txt but is a
    reasonable, safe fallback rather than silently misrendering it.'''
    if 0 <= byte <= 9:
        return str(byte)
    return _decode_register_operand(byte)


# Every 2-byte-instruction prefix byte this module knows how to decode,
# split by which operand-formatting rule applies. Their names come from
# memory/mnemonics.py (canonical() on export, resolve() on import).
_REGISTER_OPERAND_PREFIX_CODES = frozenset(
    {
        0x90, 0x91, 0x92, 0x93, 0x94, 0x95,  # RCL STO ST+ ST- ST* ST/
        0x96, 0x97, 0x98, 0x99, 0x9A, 0x9B,  # ISG DSE VIEW SREG ASTO ARCL
        0xA8, 0xA9, 0xAA, 0xAB, 0xAC, 0xAD,  # SF CF FS?C FC?C FS? FC?
        0xCE,  # X<>
    }
)
_SMALL_DIGIT_OPERAND_PREFIX_CODES = frozenset(
    {0x9C, 0x9D, 0x9E, 0x9F}  # FIX SCI ENG TONE
)
_RCL_PREFIX = 0x90
_STO_PREFIX = 0x91


def _name_for_byte(byte: int) -> str:
    return canonical(function_op(byte))


def _append_mnemonic(lines, mnemonic, operand_text, data, start, length):
    '''Appends "<mnemonic> <operand_text>", or an unknown-opcode fallback
    comment if `operand_text` is None (an unrecognized operand byte) --
    a small shared helper for the several 2-/3-byte instruction forms
    below that all follow this same "mnemonic + decoded operand, or give
    up" shape.'''
    if operand_text is None:
        lines.append(_format_unknown(data, start, length))
    else:
        lines.append(f"{mnemonic} {operand_text}")


def _format_unknown(data: bytes, start: int, length: int) -> str:
    '''hp41uc's own decompiler emits an informational, non-recompilable
    comment line for a synthetic/unassigned opcode byte rather than
    silently dropping it (docs/program_text_io_plan.md sec 2.3/5) -- e.g.
    the two confirmed-spare bytes 0xAF/0xB0. This module follows the same
    policy for anything else it doesn't recognize (an unexpected operand
    byte, an unassigned prefix range, etc.), rather than guessing.'''
    hex_bytes = " ".join(f"{b:02X}" for b in data[start : start + length])
    return f"; UNKNOWN OPCODE: {hex_bytes}"


def _render_number_run(chars: List[str]) -> str:
    '''Renders a run of digit-literal characters (see _DIGIT_CHARS)
    exactly as hp41uc's own decompiler does: the characters in order,
    except a single space is inserted immediately before an 'E' that
    isn't the very first character -- confirmed by tower.txt's "3 E3"
    (mantissa "3" then a space then the exponent marker) vs. bare "E2"
    (no mantissa, no leading space).'''
    out = []
    for i, ch in enumerate(chars):
        if ch == "E" and i > 0:
            out.append(" ")
        out.append(ch)
    return "".join(out)


def _encode_alpha_content(data: bytes) -> str:
    '''Renders raw ALPHA-text character bytes as a quoted string body
    (the text between the double quotes, not including them).

    Deliberately does NOT reuse trigraphs.py's own escape scheme here --
    confirmed against tower.txt's `"Y ^ X ?"` (a PROMPT string), whose
    raw content byte is 0x5E: trigraphs.py's shorthand table treats 0x5E
    as the FOCAL "up arrow" glyph and would render it as its own "\\^|"
    escape (correct for this project's *other* file formats, per that
    module's own docstring), but hp41uc's own decompiler prints it as a
    bare, literal '^' -- i.e. hp41uc doesn't care that FOCAL has
    reassigned some ASCII punctuation positions to other glyphs; it just
    emits every printable-ASCII-range byte (0x20-0x7E) as its own ASCII
    character, unconditionally. This module follows that same rule, with
    two necessary exceptions for bytes that would otherwise collide with
    this format's own syntax: a literal double-quote (0x22) would be
    ambiguous with the closing quote, and a literal backslash (0x5C)
    would be ambiguous with an escape sequence -- both instead go through
    the \\nnn numeric fallback, matching this project's other file
    formats' own canonical escape spelling (docs/program_text_io_plan.md
    sec 3.1's decision) since there's no fixture evidence favoring any
    other spelling. Anything outside the printable range at all (control
    bytes, DEL, high bytes) also falls back to \\nnn for the same reason.'''
    out = []
    for b in data:
        if b in (0x22, 0x5C):
            out.append(f"\\{b:03d}")
        elif 0x20 <= b <= 0x7E:
            out.append(chr(b))
        else:
            out.append(f"\\{b:03d}")
    return "".join(out)


def _decode_alpha_instruction(
    data: bytes, start: int, prefix_len: int, count: int
) -> str:
    '''Renders a direct ALPHA-text-load instruction (the 0xF0-0xFF class,
    prefix_len=1) or a GTO"/XEQ"-style global-name reference (the
    0x1D-0x1F class, prefix_len=2) as quoted text. A leading "Append"
    control byte (0x7F, trigraphs.py's own "\\+" shorthand) is rendered
    as hp41uc's own leading ">" notation instead, with the rest of the
    string quoted normally -- confirmed by tower.txt's `>":" ` (append a
    colon) and `>" " ` (append a space), both of which have 0x7F as
    their very first content byte.'''
    content = data[start + prefix_len : start + prefix_len + count]
    if content and content[0] == 0x7F:
        return ">" + '"' + _encode_alpha_content(content[1:]) + '"'
    return '"' + _encode_alpha_content(content) + '"'


def _decode_xrom(byte1: int, byte2: int) -> str:
    '''Renders an XROM instruction the way hp41uc's own decompiler does:
    always as "XROM mm,ff" (never the friendly function name as the
    mnemonic itself), with the function's name as a trailing comment when
    it's a known Extended Functions/Time function -- confirmed against
    every XROM instance in tower.txt (e.g. "XROM 25,46 ;X<>F"). mm/ff are
    recovered from the two raw bytes via mm = ((byte1 & 0x07) << 2) |
    (byte2 >> 6), ff = byte2 & 0x3F -- a formula confirmed by cross-
    checking multiple known (byte1, byte2) pairs from functions.py's own
    XROM_FUNCTIONS table against tower.txt's "mm,ff" comments (SEEKPT,
    ARCLREC, GETKEY, X<>F all check out exactly).'''
    mm = ((byte1 & 0x07) << 2) | (byte2 >> 6)
    ff = byte2 & 0x3F
    text = f"{canonical(XROM)} {mm:02d},{ff:02d}"  # hp41uc: "%02d" (decomp.c)
    op = xrom_op(byte1, byte2)
    if is_known(op):
        text += f" ;{canonical(op)}"
    return text


def encode_program_txt(data: bytes) -> str:
    '''
    Decompiles one program's raw instruction bytes (as returned by
    ProgramMemory.get_program_bytes(), decode_program_raw(), or
    decode_program_dat() -- a single program's bytes, ending in its own
    terminating END or the permanent .END. marker) into a plain-text
    keystroke listing, closely matching hp41uc's own decompile
    conventions (see this module's docstring for how that convention was
    derived and confirmed).

    Per docs/program_text_io_plan.md sec 5's round-trip decision, this is
    *not* expected to exactly reproduce a hand-authored source file's own
    prose comments (those are discarded when a file is compiled and can
    never be recovered from the compiled bytes alone) -- only hp41uc's
    own mechanical annotations (the "XROM mm,ff" name comment, the END
    trailer's own byte count) are reproduced.

    An opcode or operand byte this module doesn't recognize (an
    unassigned/spare opcode, or a malformed operand) renders as an
    informational "; UNKNOWN OPCODE: ..." comment line rather than
    raising or guessing -- see _format_unknown().
    '''
    lines: List[str] = []
    i = 0
    n = len(data)

    while i < n:
        c = data[i]

        # -- NULL (0x00). A no-op on real hardware: the HP-41 inserts one
        # in front of every number keyed into a program, and PACK removes
        # the ones that aren't needed to keep two consecutive numbers
        # apart. An unpacked program (e.g. GhostTown.ppc, "STO IND T" /
        # NULL / "1") still has them. hp41uc's decompiler skips 0x00
        # silently (decomp.c, BYTE1 state), and so do we. The compiler
        # re-inserts a NULL only where two number lines are adjacent.
        if c == 0x00:
            i += 1
            continue

        # -- A digit-literal run (one number). hp41uc's decompiler emits
        # each run as its own line; two back-to-back numbers are kept
        # apart in the bytes by a NULL, which the branch above skips (so
        # the run ends at it, and "12345" NULL "67890" -- numtest.dm41's
        # real hardware capture -- decompiles as two lines, not one).
        # decode_program_txt() re-inserts that NULL between adjacent
        # number lines.
        if c in _DIGIT_CHARS:
            chars = [_DIGIT_CHARS[c]]
            i += 1
            while i < n and data[i] in _DIGIT_CHARS:
                chars.append(_DIGIT_CHARS[data[i]])
                i += 1
            lines.append(_render_number_run(chars))
            continue

        # -- GTO"/XEQ"-with-a-global-name, or an unrecognized 2-byte form
        # sharing the same prefix range (opcode_scan's BYTE2_ALPHA class).
        if 0x1D <= c <= 0x1F:
            if i + 1 >= n:
                lines.append(_format_unknown(data, i, n - i))
                break
            c2 = data[i + 1]
            if c2 <= 0xF0:
                # Not actually an alpha-name reference -- not exercised
                # by tower.txt and not otherwise documented.
                lines.append(_format_unknown(data, i, 2))
                i += 2
                continue
            count = c2 & 0x0F
            total = 2 + count
            if i + total > n:
                lines.append(_format_unknown(data, i, n - i))
                break
            name = _decode_alpha_instruction(data, i, 2, count)
            if c == 0x1D:
                lines.append(f"{canonical(GTO)} {name}")
            elif c == 0x1E:
                lines.append(f"{canonical(XEQ)} {name}")
            else:  # 0x1F -- not confirmed to mean anything; see docstring
                lines.append(_format_unknown(data, i, total))
            i += total
            continue

        # -- 2-byte instructions: 0x90-0xBF, plus X<>/LBL at 0xCE/0xCF.
        if (0x90 <= c <= 0xBF) or (0xCE <= c <= 0xCF):
            if i + 1 >= n:
                lines.append(_format_unknown(data, i, n - i))
                break
            operand = data[i + 1]

            if c in _REGISTER_OPERAND_PREFIX_CODES:
                mnemonic = _name_for_byte(c)
                text = _decode_register_operand(operand)
                _append_mnemonic(lines, mnemonic, text, data, i, 2)
            elif c in _SMALL_DIGIT_OPERAND_PREFIX_CODES:
                mnemonic = _name_for_byte(c)
                text = _decode_small_digit_operand(operand)
                _append_mnemonic(lines, mnemonic, text, data, i, 2)
            elif 0xA0 <= c <= 0xA7:
                # Every XROM, not just the Extended Functions/Time pair at
                # 0xA6 -- e.g. A7 83 is XROM 30,03 (card reader). Same
                # "XROM mm,ff" rendering hp41uc uses for any module.
                lines.append(_decode_xrom(c, operand))
            elif c == _GTO_XEQ_IND_OPCODE:
                # High bit picks GTO vs XEQ, not "indirect" -- see
                # _XEQ_IND_FLAG's comment.
                target_op = XEQ if operand & _XEQ_IND_FLAG else GTO
                mnemonic = f"{canonical(target_op)} IND"
                text = _decode_register_operand(operand & ~_XEQ_IND_FLAG)
                _append_mnemonic(lines, mnemonic, text, data, i, 2)
            elif c in _SPARE_OPCODES:
                lines.append(_format_unknown(data, i, 2))
            elif _GTO_COMPACT_BASE <= c <= _GTO_COMPACT_BASE + _GTO_COMPACT_MAX:
                # `operand` is the cached jump distance (0 = unresolved,
                # anything else = a real calculator already resolved it) --
                # see _GTO_COMPACT_BASE's comment. It doesn't change which
                # instruction this is, so it's deliberately not checked.
                target = c - _GTO_COMPACT_BASE
                lines.append(f"{canonical(GTO)} {target:02d}")
            elif c == 0xCF:
                text = _decode_register_operand(operand)
                _append_mnemonic(lines, canonical(LBL), text, data, i, 2)
            else:
                lines.append(_format_unknown(data, i, 2))
            i += 2
            continue

        # -- Global chain marker: an END, the permanent .END., or a
        # global (quoted-name) LBL header.
        if 0xC0 <= c <= 0xCD:
            marker = decode_chain_marker(data, i)
            if marker is None:
                lines.append(_format_unknown(data, i, n - i))
                break
            if marker["is_label"]:
                length = marker["label_length"]
                header_len = 4 + max(length, 0)
                if i + header_len > n:
                    lines.append(_format_unknown(data, i, n - i))
                    break
                name_bytes = data[i + 4 : i + header_len]
                name = _encode_alpha_content(name_bytes)
                lines.append(f'{canonical(LBL)} "{name}"')
                i += header_len
                continue
            # A plain END or the permanent .END. -- this program's own
            # terminator. Its byte count is *this* marker's own ending
            # position (i + 3), never len(data): a caller that
            # accidentally passes extra trailing bytes (e.g. a RAW
            # file's checksum-and-zero-padding trailer, still attached
            # because decode_program_raw() wasn't called first) must
            # not have that padding counted into the reported size.
            # Stopping here (rather than falling through to `continue`
            # and walking whatever comes next) is what keeps that same
            # trailing padding from being misdecoded as more
            # instructions -- see the opcode-length classifier's own
            # find_program_end(), which this mirrors: a program's real
            # end is always its own first terminating END, never
            # wherever the caller's buffer happens to stop.
            lines.append(f"{canonical(END)} ;{i + 3} BYTES")
            break

        # -- 3-byte instructions: GTO (0xD0-0xDF) / XEQ (0xE0-0xEF)'s
        # general long form. The low nibble of the first byte and the whole
        # second byte are the cached jump distance, and the third byte's
        # high bit is the jump direction -- none of which say anything
        # about *which* instruction this is, so only the label (the third
        # byte's low 7 bits) is decoded. See _GTO_LONG_FIRST's comment.
        if 0xD0 <= c <= 0xEF:
            if i + 3 > n:
                lines.append(_format_unknown(data, i, n - i))
                break
            label_byte = data[i + 2] & _LONG_LABEL_MASK
            is_gto = (c & _LONG_PREFIX_MASK) == _GTO_LONG_FIRST
            mnemonic = canonical(GTO if is_gto else XEQ)
            text = _decode_register_operand(label_byte)
            _append_mnemonic(lines, mnemonic, text, data, i, 3)
            i += 3
            continue

        # -- Direct ALPHA-text-load instruction (or an "Append" variant --
        # see _decode_alpha_instruction()).
        if 0xF0 <= c <= 0xFF:
            count = c & 0x0F
            total = 1 + count
            if i + total > n:
                lines.append(_format_unknown(data, i, n - i))
                break
            lines.append(_decode_alpha_instruction(data, i, 1, count))
            i += total
            continue

        # -- Plain single-byte instruction (0x20-0x8F, minus the RCL/STO
        # compact blocks and the digit-literal range already handled
        # above; also 0x01-0x0F's compact local LBL block).
        if _LBL_COMPACT_BASE <= c <= _LBL_COMPACT_BASE + _LBL_COMPACT_MAX:
            lines.append(f"{canonical(LBL)} {c - _LBL_COMPACT_BASE:02d}")
        elif _RCL_COMPACT_BASE <= c <= _RCL_COMPACT_BASE + _COMPACT_REGISTER_MAX:
            lines.append(f"{_name_for_byte(_RCL_PREFIX)} {c - _RCL_COMPACT_BASE:02d}")
        elif _STO_COMPACT_BASE <= c <= _STO_COMPACT_BASE + _COMPACT_REGISTER_MAX:
            lines.append(f"{_name_for_byte(_STO_PREFIX)} {c - _STO_COMPACT_BASE:02d}")
        elif c >= 0x40 and is_known(function_op(c)):
            lines.append(_name_for_byte(c))
        else:
            lines.append(_format_unknown(data, i, 1))
        i += 1

    return "\n".join(lines) + "\n"


# ===========================================================================
# Phase 2 -- decode_program_txt(): compiling text back into instruction
# bytes. Everything below is the reverse of the opcode table above; see
# decode_program_txt()'s own docstring for the compile-specific design
# notes (the tokenizer, the numeric-literal-separator quirk, and the
# chain-marker "always zero" finding).
# ===========================================================================

# Reverse of _DIGIT_CHARS: display character -> raw byte.
_DIGIT_BYTES = {v: k for k, v in _DIGIT_CHARS.items()}
_NUMBER_LITERAL_CHARS = frozenset("0123456789.E-")
# A bare "-" is genuinely ambiguous as *text* -- SINGLE_BYTE_FUNCTIONS'
# 0x41 (the SUBTRACT arithmetic function) and _DIGIT_CHARS' 0x1C (the
# CHS glyph, valid only as part of an in-progress digit run, e.g. the
# leading byte of "-1") both render as the exact same one-character line
# "-" on decode -- confirmed by decompiling tower.raw itself, where a
# solitary "-" line (immediately after a plain, already-terminated "3"
# literal, with no other digit characters anywhere nearby) turns out to
# be byte 0x41, not 0x1C. Checked this is the *only* such collision --
# no other SINGLE_BYTE_FUNCTIONS entry at 0x40+ renders as text made up
# purely of "0123456789.E-" characters -- so a bare "E" (real, if rare:
# src/tests/data/targ-packed.dm41 decompiles a standalone exponent-marker
# digit with no mantissa at all anywhere near it) or a bare "." both
# still need to read back as digit-literal bytes, just not a bare "-".
_AMBIGUOUS_NUMBER_LITERAL_TEXT = "-"

# Reverse of _STACK_REGISTER_NAMES: display name -> raw base value.
_STACK_REGISTER_CODES = {v: k for k, v in _STACK_REGISTER_NAMES.items()}

# hp41uc's own C-style single-letter escapes (docs/program_text_io_plan.md
# sec 5's decision: "accepted on decode ... none collide with
# trigraphs.py's shorthand table"). Accept-only -- _encode_alpha_content()
# above never emits these, only the canonical \nnn form; see this
# module's own top-of-file docstring.
_C_STYLE_ESCAPES = {
    "a": 0x07,  # BEL
    "b": 0x08,  # backspace
    "f": 0x0C,  # form feed
    "n": 0x0A,  # line feed
    "r": 0x0D,  # carriage return
    "t": 0x09,  # horizontal tab
    "v": 0x0B,  # vertical tab
    "?": 0x3F,  # '?' (only needed to defeat C trigraphs; harmless here)
    '"': 0x22,
    "'": 0x27,
    "\\": 0x5C,
}

# hp41uc/§4.4's own two-marker comment style for a byte-for-byte-unknown
# instruction (see _format_unknown() above) -- recognized specially on
# decode so a decompile -> (unedited) -> recompile round trip can carry
# a synthetic/unassigned opcode straight through untouched, per §5's
# decision ("A decompile-edit-recompile round trip must be able to carry
# a byte-for-byte-unknown instruction through untouched as long as the
# user doesn't try to hand-edit that particular line").
_UNKNOWN_OPCODE_RE = re.compile(
    r"^;\s*UNKNOWN OPCODE:\s*([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})*)\s*$"
)

# "XROM mm,ff" -- hp41uc's own spelling, no space around the comma
# (confirmed throughout tower.txt: "XROM 25,46", "XROM 25,05"), but a
# stray space either side is tolerated for hand-authored source.
_XROM_RE = re.compile(r"^(\d{1,2})\s*,\s*(\d{1,2})$")


def _tokenize_line(line: str) -> List[str]:
    '''Quote-and-escape-aware tokenizer for one line of program-text
    source: splits on whitespace outside a quoted ALPHA string, and
    treats a backslash inside quotes as escaping the very next character
    (so `\\"` inside a string doesn't end it early -- necessary for the
    `\\XHH`/`\\nnn`/C-style escapes _decode_alpha_text_literal() accepts
    below, several of which start with a digit or letter that would
    otherwise look like ordinary text, but `\\"` specifically needs this
    to avoid ending the string on the escaped quote itself).

    Stops -- discarding the token that triggered it and the rest of the
    line -- at the first token, outside quotes, that begins with `;` or
    `#` (hp41uc's own two comment markers, plan doc sec 2.1). A token can
    only begin with one of those characters right at a whitespace/line
    boundary (checked here via "is `cur` still empty"), which is exactly
    what keeps a mnemonic like "X#Y?" (a single token whose first
    character is 'X') from ever being mistaken for a comment -- matching
    src/tests/test_program_text.py's own `_strip_trailing_comment()`
    reasoning on the decode side.

    Raises ValueError if the line ends with an unterminated quoted
    string (an unmatched `"`).'''
    tokens: List[str] = []
    cur: List[str] = []
    in_quotes = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if in_quotes and ch == "\\" and i + 1 < n:
            cur.append(ch)
            cur.append(line[i + 1])
            i += 2
            continue
        if ch == '"':
            in_quotes = not in_quotes
            cur.append(ch)
            i += 1
            continue
        if ch.isspace() and not in_quotes:
            if cur:
                tokens.append("".join(cur))
                cur = []
            i += 1
            continue
        if not in_quotes and not cur and ch in ";#":
            break  # a comment token boundary -- stop, discard the rest
        cur.append(ch)
        i += 1
    if in_quotes:
        raise ValueError(f"unterminated quoted text in line: {line!r}")
    if cur:
        tokens.append("".join(cur))
    return tokens


def _is_number_literal_tokens(tokens: List[str]) -> bool:
    '''True if `tokens`, concatenated back together with no separator,
    forms nothing but digit-literal characters (see _DIGIT_CHARS) -- the
    compile-side mirror of _render_number_run()'s own single-space-
    before-a-non-initial-'E' rule: decompiling "3 E3" splits it into two
    whitespace-separated tokens ("3", "E3") purely for display, so
    joining them back together (undoing that one cosmetic space) is what
    recovers the original one-instruction digit run. No real mnemonic
    this module recognizes is spelled using only "0123456789.E-" (every
    one has at least one letter outside that set -- "ST-" has 'S'/'T',
    "SIN" has 'S'/'I'/'N', etc.), so this check can never misfire against
    an actual instruction. The one exception -- a bare "-" alone is
    *not* treated as a number literal -- is what tells a real number
    literal like "-1" apart from a bare "-" that's actually the
    unrelated SUBTRACT function; see _AMBIGUOUS_NUMBER_LITERAL_TEXT's
    own comment above.'''
    joined = "".join(tokens)
    if joined == _AMBIGUOUS_NUMBER_LITERAL_TEXT:
        return False
    return bool(joined) and all(ch in _NUMBER_LITERAL_CHARS for ch in joined)


def _decode_alpha_text_literal(text: str) -> bytes:
    '''Reverses _encode_alpha_content() -- turns the text between a pair
    of quotes back into raw content bytes. A literal printable-ASCII
    character (0x20-0x7E) maps to its own byte value, matching hp41uc's
    "FOCAL's reassigned punctuation prints literally" behavior confirmed
    in this module's own docstring/`_encode_alpha_content()`. `\\` starts
    an escape: `\\XHH` (2 hex digits, the capital-X spelling settled on
    in docs/program_text_io_plan.md sec 3.1 to avoid colliding with
    trigraphs.py's lowercase `\\x` "times" shorthand), `\\nnn` (3 decimal
    digits, this project's own canonical fallback -- what
    _encode_alpha_content() actually emits), or one of the C-style
    single-letter escapes in _C_STYLE_ESCAPES (accepted per the plan's
    sec 5 decision, for smoother acceptance of hp41uc/community source --
    never emitted by the encoder). Raises ValueError on a trailing
    backslash or an escape sequence that matches none of those forms.'''
    out = bytearray()
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != "\\":
            b = ord(ch)
            if b > 0xFF:
                raise ValueError(f"non-8-bit character {ch!r} in ALPHA text: {text!r}")
            out.append(b)
            i += 1
            continue
        if i + 1 >= n:
            raise ValueError(f"trailing backslash in ALPHA text: {text!r}")
        nxt = text[i + 1]
        if nxt == "X":
            hex_digits = text[i + 2 : i + 4]
            is_hex = all(c in "0123456789ABCDEFabcdef" for c in hex_digits)
            if len(hex_digits) == 2 and is_hex:
                out.append(int(hex_digits, 16))
                i += 4
                continue
            raise ValueError(f"malformed \\X escape (need 2 hex digits) in: {text!r}")
        if nxt.isdigit():
            digits = text[i + 1 : i + 4]
            if len(digits) == 3 and digits.isdigit():
                value = int(digits)
                if value > 0xFF:
                    raise ValueError(f"\\{digits} out of byte range in: {text!r}")
                out.append(value)
                i += 4
                continue
            raise ValueError(f"malformed \\nnn escape (need 3 digits) in: {text!r}")
        if nxt in _C_STYLE_ESCAPES:
            out.append(_C_STYLE_ESCAPES[nxt])
            i += 2
            continue
        raise ValueError(f"unrecognized escape '\\{nxt}' in ALPHA text: {text!r}")
    return bytes(out)


def _parse_quoted_token(token: str) -> bytes:
    '''Parses a single tokenized quoted-string token -- e.g. `'"SCORE: "'`
    or `'>":"'` (a leading `>`, hp41uc's own "append to ALPHA" notation,
    see _decode_alpha_instruction()'s own docstring) -- into raw content
    bytes, with the 0x7F "Append" control byte prepended when `>` was
    present. Raises ValueError if `token` isn't a well-formed quoted
    string (missing/mismatched quotes).'''
    append = token.startswith(">")
    body = token[1:] if append else token
    if len(body) < 2 or body[0] != '"' or body[-1] != '"':
        raise ValueError(f"malformed quoted ALPHA text: {token!r}")
    content = _decode_alpha_text_literal(body[1:-1])
    return bytes([0x7F]) + content if append else content


def _parse_register_base(token: str) -> int:
    '''Parses a bare (non-indirect) register/label/flag descriptor
    token -- a decimal register/flag number 0-99, a local letter label
    A-J, a stack register name T/Z/Y/X/L, or one of the synthetic-only
    status-register names M/N/O/P/Q/R/a/b/c/d/e (see the module-level
    comment above _decode_register_operand(), which this reverses).
    Raises ValueError if `token` doesn't match any of those forms.

    Checks an exact-case match against _STACK_REGISTER_CODES *before*
    any case-folding, then falls back to a case-insensitive match, then
    the A-J local-label range. This order matters specifically for the
    lowercase-only a/b/c/d/e names: they must resolve to the synthetic
    status registers (0x7B-0x7F) while their uppercase counterparts A-E
    keep meaning the unrelated, keyboard-reachable local letter labels
    (0x66-0x6A) -- e.g. "a" -> register a (0x7B), but "A" -> local label
    A (0x66). Checking the raw token first is what keeps those separate;
    a blanket `token.upper()` before lookup (as this function used to do,
    back when the only names here were the already-uppercase T/Z/Y/X/L)
    would silently fold "a" into "A" and never reach register a at all.
    The case-insensitive fallback below preserves the previous lenient
    behavior for the genuinely case-insensitive names (T/Z/Y/X/L and the
    new M/N/O/P/Q), e.g. "t" still resolves to stack register T.'''
    if token in _STACK_REGISTER_CODES:
        return _STACK_REGISTER_CODES[token]
    upper = token.upper()
    if upper in _STACK_REGISTER_CODES:
        return _STACK_REGISTER_CODES[upper]
    if len(upper) == 1 and "A" <= upper <= "J":
        return 0x66 + (ord(upper) - ord("A"))
    if token.isdigit():
        n = int(token)
        if 0 <= n <= 99:
            return n
    raise ValueError(f"not a valid register/label operand: {token!r}")


def _parse_register_operand(tokens: List[str]) -> int:
    '''Parses an operand token list that may lead with a literal "IND"
    token (e.g. `["IND", "16"]` or just `["16"]`) into a full descriptor
    byte, indirect bit included -- the reverse of
    _decode_register_operand(). Raises ValueError if `tokens` isn't
    exactly an optional "IND" followed by one base-operand token.'''
    indirect = bool(tokens) and tokens[0].upper() == "IND"
    rest = tokens[1:] if indirect else tokens
    if len(rest) != 1:
        raise ValueError(f"expected exactly one register operand, got: {tokens!r}")
    base = _parse_register_base(rest[0])
    return (0x80 if indirect else 0x00) | base


def _encode_register_operand_instruction(prefix: int, tokens: List[str]) -> bytes:
    '''RCL/STO/ST+/ST-/ST*/ST//ISG/DSE/VIEW/SREG/ASTO/ARCL/SF/CF/
    FS?C/FC?C/FS?/FC?/X<> -- every mnemonic sharing the register/flag
    "descriptor" operand byte scheme. RCL/STO additionally prefer the
    compact single-byte form for a direct (non-IND) register 00-15,
    mirroring encode_program_txt()'s own compact-vs-general split exactly
    (see _RCL_COMPACT_BASE/_STO_COMPACT_BASE's own module-level
    comment) -- every other mnemonic in this group always uses the
    general 2-byte form, since it has no compact form to begin with.
    `prefix` is the instruction's prefix byte; `tokens[0]` is its name
    as written, used only in error messages.'''
    mnemonic = tokens[0]
    operand_tokens = tokens[1:]
    if not operand_tokens:
        raise ValueError(f"{mnemonic} needs an operand: {' '.join(tokens)!r}")
    indirect = operand_tokens[0].upper() == "IND"
    base_tokens = operand_tokens[1:] if indirect else operand_tokens
    if len(base_tokens) != 1:
        raise ValueError(
            f"unexpected extra tokens for {mnemonic}: {' '.join(tokens)!r}"
        )
    base = _parse_register_base(base_tokens[0])

    is_compact_eligible = (
        prefix in (_RCL_PREFIX, _STO_PREFIX)
        and not indirect
        and 0 <= base <= _COMPACT_REGISTER_MAX
    )
    if is_compact_eligible:
        compact_base = _RCL_COMPACT_BASE if prefix == _RCL_PREFIX else _STO_COMPACT_BASE
        return bytes([compact_base + base])

    descriptor = (0x80 if indirect else 0x00) | base
    return bytes([prefix, descriptor])


def _encode_small_digit_operand_instruction(prefix: int, tokens: List[str]) -> bytes:
    '''FIX/SCI/ENG/TONE -- the bare-0-9-digit operand style (see
    _decode_small_digit_operand()). Falls back to the general
    register-descriptor encode (supporting an "IND" operand) for anything
    that isn't a single bare digit 0-9, mirroring
    _decode_small_digit_operand()'s own fallback.'''
    operand_tokens = tokens[1:]
    if not operand_tokens:
        raise ValueError(f"{tokens[0]} needs an operand: {' '.join(tokens)!r}")
    is_bare_digit = (
        len(operand_tokens) == 1
        and operand_tokens[0].isdigit()
        and len(operand_tokens[0]) == 1
    )
    if is_bare_digit:
        return bytes([prefix, int(operand_tokens[0])])
    descriptor = _parse_register_operand(operand_tokens)
    return bytes([prefix, descriptor])


def _encode_xrom(tokens: List[str]) -> bytes:
    '''"XROM mm,ff" -- reverses _decode_xrom()'s mm/ff-recovery formula
    (byte1 = 0xA0 | ((mm>>2)&7), byte2 = ((mm&3)<<6) | (ff&0x3F)).

    Accepts any module 00-31 and function 00-63, whether or not the
    DM41L emulates that module -- matching hp41uc, and matching RAW/DAT/
    PPC import, which already accept these bytes. (Changed 2026-09-22:
    this used to reject anything outside Extended Functions/Time, so a
    program using e.g. the card reader's XROM 30,03 could be exported to
    .txt but not imported back.) On the calculator, an XROM for a module
    that isn't present shows as "XROM mm,ff" and only errors
    (NONEXISTENT) if executed.

    This is only reached for the numeric "XROM mm,ff" spelling -- a
    function spelled by its own name (e.g. "SEEKPT") resolves directly
    to its XROM op in _encode_instruction() and never gets here.'''
    if len(tokens) != 2:
        raise ValueError(
            f"XROM needs exactly one 'mm,ff' operand: {' '.join(tokens)!r}"
        )
    match = _XROM_RE.match(tokens[1])
    if not match:
        raise ValueError(f"malformed XROM operand (expected 'mm,ff'): {tokens[1]!r}")
    mm, ff = int(match.group(1)), int(match.group(2))
    if not (0 <= mm <= 31 and 0 <= ff <= 63):
        raise ValueError(
            f"XROM {mm},{ff:02d} out of range -- module must be 00-31 and "
            "function 00-63"
        )
    byte1 = 0xA0 | ((mm >> 2) & 0x07)
    byte2 = ((mm & 0x03) << 6) | (ff & 0x3F)
    return bytes([byte1, byte2])


def _encode_gto_xeq(is_xeq: bool, tokens: List[str]) -> bytes:
    '''GTO/XEQ -- three forms, matching encode_program_txt()'s own three
    GTO/XEQ decode branches exactly: a quoted global name reference
    (`GTO "NAME"`/`XEQ "NAME"`, the 0x1D/0x1E-prefixed form), `GTO IND
    <reg>`/`XEQ IND <reg>` (the shared 0xAE opcode, with the operand's
    high bit set for XEQ -- see _XEQ_IND_FLAG), or a bare local
    target number/letter (GTO additionally prefers the compact 2-byte
    form for a local number 00-14, matching _GTO_COMPACT_BASE's own
    module-level comment; XEQ has no compact form at all and always uses
    the general 3-byte form). `tokens[0]` is the name as written (GTO,
    GOTO, XEQ, ...), used only in error messages.'''
    mnemonic = tokens[0]
    operand_tokens = tokens[1:]
    if not operand_tokens:
        raise ValueError(f"{mnemonic} needs an operand: {' '.join(tokens)!r}")
    first_operand = operand_tokens[0]

    if first_operand.startswith('"') or first_operand.startswith('>"'):
        if len(operand_tokens) != 1:
            raise ValueError(
                f"unexpected extra tokens after {mnemonic} name: {' '.join(tokens)!r}"
            )
        content = _parse_quoted_token(first_operand)
        if not 1 <= len(content) <= 15:
            raise ValueError(
                f"{mnemonic} global name must be 1-15 bytes, got {len(content)}: "
                f"{first_operand!r}"
            )
        prefix = 0x1E if is_xeq else 0x1D
        return bytes([prefix, 0xF0 | len(content)]) + content

    if first_operand.upper() == "IND":
        if len(operand_tokens) != 2:
            raise ValueError(
                f"{mnemonic} IND needs exactly one register operand: {tokens!r}"
            )
        # A bare (non-IND) base, so always < 0x80 -- leaves the high bit
        # free to carry the GTO/XEQ flag.
        base = _parse_register_base(operand_tokens[1])
        flag = _XEQ_IND_FLAG if is_xeq else 0x00
        return bytes([_GTO_XEQ_IND_OPCODE, flag | base])

    if len(operand_tokens) != 1:
        raise ValueError(
            f"unexpected extra tokens after {mnemonic}: {' '.join(tokens)!r}"
        )
    base = _parse_register_base(first_operand)
    if not is_xeq and 0 <= base <= _GTO_COMPACT_MAX:
        return bytes([_GTO_COMPACT_BASE + base, _GTO_COMPACT_UNRESOLVED_BYTE2])
    prefix = _XEQ_LONG_FIRST if is_xeq else _GTO_LONG_FIRST
    return bytes([prefix, _LONG_UNRESOLVED_BYTE2, base])


def _encode_instruction(tokens: List[str]) -> Tuple[bytes, bool]:
    '''Encodes one non-numeric-literal instruction's tokens (as produced
    by _tokenize_line()) into its raw bytes. Returns (bytes, is_end) --
    `is_end` is True only for the program's own terminating END, so
    decode_program_txt() knows to stop there. Raises ValueError, with a
    message naming the offending token(s), for anything it doesn't
    recognize -- the same "give up and be informative" posture
    _format_unknown() takes on the decode side, just as an exception
    instead of a comment line, since a program that doesn't compile has
    no bytes to fall back to.'''
    first = tokens[0]

    # A bare quoted string (optionally `>`-prefixed) with no leading
    # mnemonic word at all is a direct ALPHA-text-load instruction (the
    # 0xF0-0xFF class) -- see _decode_alpha_instruction()'s prefix_len=1
    # case.
    if first.startswith('"') or first.startswith('>"'):
        if len(tokens) != 1:
            raise ValueError(f"unexpected extra tokens after ALPHA text: {tokens!r}")
        content = _parse_quoted_token(first)
        if not 0 <= len(content) <= 15:
            raise ValueError(
                f"ALPHA text must be 0-15 bytes, got {len(content)}: {first!r}"
            )
        return bytes([0xF0 | len(content)]) + content, False

    # Any accepted spelling of the instruction (mnemonics.resolve()).
    # Raises UnknownMnemonicError, a ValueError, for anything else.
    op = resolve(first)
    mnemonic = first

    if op == END:
        if len(tokens) != 1:
            raise ValueError(f"unexpected extra tokens after END: {tokens!r}")
        # Always bbb=0/distance_registers=0 (an unlinked/"no predecessor"
        # marker) and third_byte=0x0D (a normal, non-.END., "needs
        # packing" END) -- confirmed to be hp41uc's own compiled output
        # for *every* global marker in tower.raw (not just its trailing
        # END: `scan_global_markers_forward()` on tower.raw's decoded
        # bytes shows the same bbb=0/distance=0 on its `LBL "TWR"` marker
        # too), even though `LBL "TWR"` demonstrably precedes it in the
        # very same 1088-byte buffer. Real HP-41/DM41L hardware *does*
        # maintain real backward links (see APPTEST_BYTES's own non-zero
        # bbb/distance in test_program_export.py, captured live off a
        # real calculator) -- but that linking is exactly what this
        # project's own existing pack()/_forward_scan_programs() repair
        # mechanism (docs/program.md sec 5.4) already exists to fix up
        # for "a state written by a tool other than a real HP-41/DM41L (or
        # this app)", so this compiler doesn't need to track any
        # cross-instruction chain position itself -- it just needs to
        # match what hp41uc's own compiler actually emits.
        return encode_chain_marker(0, 0, 0x0D), True

    if op == LBL:
        if len(tokens) != 2:
            raise ValueError(f"LBL needs exactly one operand: {tokens!r}")
        operand = tokens[1]
        if operand.startswith('"'):
            name = _parse_quoted_token(operand)
            if len(name) > 14:
                raise ValueError(
                    f"global label name must be at most 14 bytes, got "
                    f"{len(name)}: {operand!r}"
                )
            third_byte = 0xF0 | ((len(name) + 1) & 0x0F)
            # Same "always zero" chain-link fields as END above, plus a
            # key-assignment byte of 0x00 -- confirmed against tower.raw's
            # own `LBL "TWR"` marker (key_assignment == 0), consistent
            # with there being no key-assignment syntax anywhere in the
            # program-text format to begin with.
            return encode_chain_marker(0, 0, third_byte) + bytes([0x00]) + name, False
        base = _parse_register_base(operand)
        if 0 <= base <= _LBL_COMPACT_MAX:
            return bytes([_LBL_COMPACT_BASE + base]), False
        return bytes([0xCF, base]), False

    if op in (GTO, XEQ):
        return _encode_gto_xeq(op == XEQ, tokens), False

    if op == XROM:
        return _encode_xrom(tokens), False

    if op.kind is OpKind.XROM:
        if len(tokens) != 1:
            raise ValueError(
                f"unexpected operand(s) for {mnemonic}: {' '.join(tokens)!r}"
            )
        return bytes(op.code), False

    # Everything left is a functions.py byte: a 2-byte prefix taking an
    # operand, or a plain single-byte instruction.
    byte = op.code
    if byte in _REGISTER_OPERAND_PREFIX_CODES:
        return _encode_register_operand_instruction(byte, tokens), False

    if byte in _SMALL_DIGIT_OPERAND_PREFIX_CODES:
        return _encode_small_digit_operand_instruction(byte, tokens), False

    if not 0x40 <= byte <= 0x8F:
        # A programmable functions.py byte this compiler has no encoding
        # rule for -- a gap in this module's tables, not bad input.
        raise ValueError(f"no encoding rule for instruction {mnemonic!r}")
    if len(tokens) != 1:
        raise ValueError(
            f"unexpected operand(s) for {mnemonic}: {' '.join(tokens)!r}"
        )
    return bytes([byte]), False


def decode_program_txt(text: str) -> bytes:
    '''
    Compiles a plain-text keystroke listing (as produced by
    `encode_program_txt()`, or an hp41uc/community-authored `.txt` file
    such as `src/tests/data/tower.txt` itself) back into one program's
    raw instruction bytes, ready for `Memory.import_program()` (per
    docs/program_text_io_plan.md sec 4.3/sec 5: packing is a separate,
    already-implemented, user-invoked operation and is never this
    function's job).

    One line is (usually) one instruction; see _tokenize_line() for the
    quote/escape-aware tokenizer and comment handling (`;`/`#`, matching
    encode_program_txt()'s own comment conventions). An Extended
    Functions/Time function may be written either the way
    encode_program_txt() itself decompiles it, numerically ("XROM
    25,42"), or by its own bare mnemonic name ("SEEKPT") -- see
    memory/mnemonics.py -- so program text imported from other
    sources (which typically use the name, not hp41uc's own "mm,ff"
    catalog numbering) doesn't need manual find-and-replace before it
    compiles here. Instruction names can be any spelling
    memory/mnemonics.py's resolve() accepts (canonical hp41uc names, the
    HP-41's own display names, and their dialect/substitution variants;
    docs/mnemonic_dialects_plan.md). Two more exceptions:

    - A numeric literal that encode_program_txt() rendered with a
      cosmetic space before a non-initial "E" (`_render_number_run()`,
      e.g. "3 E3") tokenizes as two whitespace-separated tokens but is
      still one instruction -- see _is_number_literal_tokens(), checked
      before general per-mnemonic dispatch on every line.
    - Two numeric-literal instructions back to back need a `0x00`
      separator byte reinserted between them (digit bytes 0x10-0x1C have
      no length prefix and would otherwise run together on the way back
      onto a real calculator) -- confirmed against real hardware, both by
      a purpose-built fixture (`src/tests/data/numtest.dm41`) and,
      independently, by tower.txt's own back-to-back "3"/"-" lines (a
      digit-run immediately followed by a lone CHS-glyph digit-run,
      which only round-trips correctly through encode_program_txt() if a
      0x00 separator actually sits between them in tower.raw). This
      function tracks whether the immediately preceding instruction it
      emitted was a numeric literal and inserts the separator whenever
      the current one is too.

    An `; UNKNOWN OPCODE: <hex bytes>` line (encode_program_txt()'s own
    informational comment for a synthetic/unassigned opcode -- see
    _format_unknown()) is recognized specially and passed straight
    through as those exact raw bytes, so a decompile -> (unedited) ->
    recompile round trip never loses or corrupts a byte-for-byte-unknown
    instruction, per the plan's sec 5 decision.

    Raises ValueError -- with the 1-based line number and a description
    of the problem -- for a line that doesn't parse as any recognized
    instruction, an operand that doesn't fit its field width, an XROM
    module/function number out of range, or a missing/misplaced terminating
    END. Never silently drops or guesses at malformed input.
    '''
    out = bytearray()
    last_was_number = False
    end_seen = False

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        if end_seen:
            stripped_after_end = raw_line.strip()
            if stripped_after_end and not stripped_after_end.startswith((";", "#")):
                raise ValueError(
                    f"line {lineno}: unexpected content after program's "
                    f"terminating END: {raw_line!r}"
                )
            continue

        stripped = raw_line.strip()
        unknown_match = _UNKNOWN_OPCODE_RE.match(stripped)
        if unknown_match is not None:
            try:
                hex_bytes = bytes(int(h, 16) for h in unknown_match.group(1).split())
            except ValueError as exc:
                raise ValueError(
                    f"line {lineno}: malformed UNKNOWN OPCODE comment: {raw_line!r}"
                ) from exc
            out.extend(hex_bytes)
            last_was_number = False
            continue

        try:
            tokens = _tokenize_line(raw_line)
        except ValueError as exc:
            raise ValueError(f"line {lineno}: {exc}") from exc
        if not tokens:
            continue  # blank line, or a comment-only line

        if _is_number_literal_tokens(tokens):
            try:
                digit_bytes = bytes(_DIGIT_BYTES[ch] for ch in "".join(tokens))
            except KeyError as exc:
                raise ValueError(
                    f"line {lineno}: invalid digit-literal character in: "
                    f"{' '.join(tokens)!r}"
                ) from exc
            if last_was_number:
                out.append(0x00)
            out.extend(digit_bytes)
            last_was_number = True
            continue

        try:
            instr_bytes, is_end = _encode_instruction(tokens)
        except ValueError as exc:
            raise ValueError(f"line {lineno}: {exc}") from exc
        out.extend(instr_bytes)
        last_was_number = False
        if is_end:
            end_seen = True

    if not end_seen:
        raise ValueError("program text has no terminating END line")
    return bytes(out)
