"""
Trigraph encode/decode for the HP41/DM41L character set (docs/trigraphs.md).

FOCAL (the HP41/DM41's character set) is a superset-but-not-quite of ASCII:
most printable characters mean what they'd mean in plain ASCII, but a
handful of byte codes are reassigned to symbols ASCII has no glyph for
(Sigma, an up arrow, a "not equal" sign, and so on) -- including some codes,
like 0x5C/0x5E/0x60/0x7E, that *are* normal ASCII punctuation positions but
mean something else entirely on a real HP41/DM41L display.

Since our plain-text import/export files are 7-bit ASCII (see
ExtendedMemory's ASCII-file records and registers.format_data_line()/
parse_data_line()'s alpha-text branch), any byte that doesn't safely mean
itself in plain ASCII is written out as a "trigraph": a backslash followed
by either a short mnemonic (e.g. "\\T" for the Tee symbol, from
docs/trigraphs.md's table) or, as a general fallback for *any* byte
(including ones that already have a mnemonic), exactly three decimal
digits (e.g. "\\065" for byte 65 / 'A'). The three-decimal-digit form is
always fixed-width -- docs/trigraphs.md's own example ("\\65") omits the
leading zero, but fixed width is what keeps "\\nnn" unambiguous when it's
immediately followed by more digits (a variable-width "\\65" followed by a
literal "12" would be indistinguishable from "\\6" + "512" or "\\651" +
"2").

`decode_trigraphs()`'s optional `restrict_literals` flag adds a further,
separate check: real FOCAL has no true lowercase letters above 'e'
(0x66-0x7A are reassigned to unrelated symbols, not lowercase f-z), so
plain lowercase text like "Test Message" displays as garbage on a real
DM41L.
xm_file.py's NAME_MIN_CHAR/NAME_MAX_CHAR already enforced the same
0x20-0x65 boundary for XM file names (issue #11); `restrict_literals`
brings that same boundary to any other *content* a user types expecting
it to render as itself, without losing the ability to deliberately reach
any other byte value via an explicit trigraph escape.
"""

# byte value -> the text that follows the leading backslash in its
# shorthand trigraph (docs/trigraphs.md's table). These are the specific
# byte codes FOCAL reassigns away from their plain-ASCII meaning; every
# other byte either means itself (if printable ASCII -- see _LITERAL_BYTES
# below) or falls back to the general \nnn numeric form.
_SHORTHAND_BY_BYTE = {
    0x00: "--",  # a high horizontal bar
    0x01: "x",  # times symbol
    0x0C: "u",  # micro symbol
    0x0D: "<)",  # angle symbol
    0x1D: "/=",  # not equal
    0x5C: "\\",  # backslash itself
    0x5E: "^|",  # up arrow
    0x60: "T",  # tee
    0x7E: "E",  # Sigma
    0x7F: "+",  # Append
}
_BYTE_BY_SHORTHAND = {v: k for k, v in _SHORTHAND_BY_BYTE.items()}
# Longest suffix first: none of the current keys are prefixes of one
# another, so match order doesn't matter *yet*, but checking
# longest-first keeps that from silently becoming a bug if the table ever
# grows a new entry that does prefix an existing one.
_SHORTHAND_SUFFIXES_BY_LENGTH = sorted(_BYTE_BY_SHORTHAND, key=len, reverse=True)

# Bytes that mean themselves in plain ASCII text: printable ASCII (0x20
# space through 0x7E) minus the specific codes FOCAL has reassigned to a
# shorthand trigraph above. Everything else (control codes, DEL, byte
# values above 0x7F, and the reassigned codes) always needs an escape.
# NOTE: this is about *round-tripping raw bytes through a text file*
# unambiguously -- it's deliberately broader than "characters FOCAL
# actually renders sensibly," which is what `restrict_literals` below
# checks instead. A byte in this set but above _SAFE_LITERAL_MAX still
# encodes/decodes as itself here; restrict_literals is what stops a
# *caller* from typing one as a bare literal when they almost certainly
# didn't mean to.
_LITERAL_BYTES = frozenset(range(0x20, 0x7F)) - frozenset(_SHORTHAND_BY_BYTE)

# The highest byte value FOCAL treats as a "real" printable literal for
# ordinary typed content -- 0x65 ('e'). FOCAL has no genuine lowercase
# letters above this; 0x66-0x7A are reassigned to unrelated symbols, not
# lowercase f-z (see module docstring). Matches xm_file.py's
# NAME_MAX_CHAR, the same boundary for XM file names (issue #11).
_SAFE_LITERAL_MIN = 0x20
_SAFE_LITERAL_MAX = 0x65


# The Unicode character for each FOCAL byte that a trigraph shorthand
# stands for -- what the HP41/DM41L actually displays for it. Every other
# printable byte (see _LITERAL_BYTES) is its own ASCII character.
_UNICODE_BY_BYTE = {
    0x00: "\u203e",  # ‾ high horizontal bar
    0x01: "\u00d7",  # × times
    0x0C: "\u00b5",  # µ micro
    0x0D: "\u2221",  # ∡ angle
    0x1D: "\u2260",  # ≠ not equal
    0x5C: "\\",  # backslash
    0x5E: "\u2191",  # ↑ up arrow
    0x60: "\u22a4",  # ⊤ tee
    0x7E: "\u03a3",  # Σ Sigma
    0x7F: "\u22a6",  # ⊦ append
}


def shorthand_trigraphs() -> list:
    """Every shorthand trigraph as (escape text, byte value, the Unicode
    symbol it stands for), in byte order -- for reference listings.
    The escape text includes its leading backslash, e.g. "\\E"."""
    return [
        ("\\" + suffix, byte, _UNICODE_BY_BYTE[byte])
        for byte, suffix in sorted(_SHORTHAND_BY_BYTE.items())
    ]


_BYTE_BY_UNICODE = {ch: b for b, ch in _UNICODE_BY_BYTE.items() if ch != "\\"}


def unicode_to_trigraphs(text: str) -> str:
    """The reverse of focal_to_unicode() for display names: each FOCAL
    symbol (Σ, ↑, ≠, ...) becomes its shorthand trigraph, everything else
    stays as it is. ΣREG -> \\EREG."""
    return "".join(
        (
            "\\" + _SHORTHAND_BY_BYTE[_BYTE_BY_UNICODE[ch]]
            if ch in _BYTE_BY_UNICODE
            else ch
        )
        for ch in text
    )


def focal_to_unicode(data: bytes) -> str:
    """Renders raw HP41/DM41L character bytes as the Unicode text they
    display as: a trigraph shorthand's byte becomes its real symbol
    (0x7E -> Σ, 0x5E -> ↑, 0x1D -> ≠, ...), printable ASCII stays itself,
    and any other byte falls back to its \\nnn trigraph so nothing is
    lost. For display only -- decode_trigraphs() is the way back."""
    out = []
    for b in data:
        if b in _UNICODE_BY_BYTE:
            out.append(_UNICODE_BY_BYTE[b])
        elif b in _LITERAL_BYTES:
            out.append(chr(b))
        else:
            out.append(f"\\{b:03d}")
    return "".join(out)


def encode_trigraphs(data: bytes) -> str:
    """Renders raw HP41/DM41L character bytes as plain-ASCII text, using a
    trigraph escape (see the module docstring) for any byte that doesn't
    safely mean itself in plain ASCII."""
    out = []
    for b in data:
        if b in _LITERAL_BYTES:
            out.append(chr(b))
        elif b in _SHORTHAND_BY_BYTE:
            out.append("\\" + _SHORTHAND_BY_BYTE[b])
        else:
            out.append(f"\\{b:03d}")
    return "".join(out)


def decode_trigraphs(text: str, *, restrict_literals: bool = False) -> bytes:
    """Parses plain-ASCII text (which may contain trigraph escapes -- see
    the module docstring) back into raw HP41/DM41L character bytes. Raises
    ValueError for a backslash that isn't followed by a recognized
    shorthand or exactly three decimal digits, or for a source character
    that isn't a single byte.

    If `restrict_literals` is True, a *literal* (non-escaped) character
    outside FOCAL's real printable range (0x20-0x65, space through
    lowercase 'e' -- see `_SAFE_LITERAL_MAX`) also raises ValueError,
    instead of being silently accepted and written out as a byte that
    won't actually display as that character on real hardware. An
    explicit trigraph escape is exempt either way -- the caller asked for
    that exact byte value on purpose."""
    out = bytearray()
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != "\\":
            code = ord(ch)
            if code > 0xFF:
                raise ValueError(
                    f"Character {ch!r} at position {i} isn't representable "
                    "as a single byte -- use a \\nnn trigraph instead."
                )
            if restrict_literals and not (
                _SAFE_LITERAL_MIN <= code <= _SAFE_LITERAL_MAX
            ):
                raise ValueError(
                    f"Character {ch!r} at position {i} (code {code}) isn't "
                    "a valid literal FOCAL character -- FOCAL has no real "
                    "lowercase letters above 'e' (0x66-0x7A are reassigned "
                    "to unrelated symbols). Use uppercase, or a \\nnn "
                    "trigraph if you specifically want that byte value."
                )
            out.append(code)
            i += 1
            continue

        matched = False
        for suffix in _SHORTHAND_SUFFIXES_BY_LENGTH:
            if text[i + 1 : i + 1 + len(suffix)] == suffix:
                out.append(_BYTE_BY_SHORTHAND[suffix])
                i += 1 + len(suffix)
                matched = True
                break
        if matched:
            continue

        digits = text[i + 1 : i + 4]
        if len(digits) == 3 and digits.isdigit():
            value = int(digits)
            if value > 255:
                raise ValueError(
                    f"\\{digits} at position {i} is out of range -- a "
                    "trigraph's numeric value must be 000-255."
                )
            out.append(value)
            i += 4
            continue

        raise ValueError(
            f"Unrecognized trigraph at position {i}: {text[i:i + 4]!r} "
            "isn't a known shorthand (see docs/trigraphs.md) or "
            "\\ + exactly 3 decimal digits."
        )
    return bytes(out)
