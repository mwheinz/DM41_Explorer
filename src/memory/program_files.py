'''
Encoders for exporting a single HP41 program file.

Supports RAW, DAT, "PPC" (a DAT that's been broken into multiple lines, as
seen in PPC Calculator Journal program listings), and TXT (a plain-text
HP-41 keystroke listing -- see memory/program_text.py and
docs/program_text_io_plan.md).

`encode_program_[raw, dat, ppc, txt]()` convert a program's instruction
bytes into a file's own bytes.

`decode_program_[raw, dat, txt]()` are the reverse direction -- read a
RAW/DAT/TXT file's own bytes, and recover a program's instruction bytes.
(There's no `decode_program_ppc()` of its own: PPC is just word-wrapped
DAT, and `decode_program_dat()` already strips whitespace before decoding,
so it reads a PPC file directly -- see that function's own docstring.)
'''

from .opcode_scan import find_program_end
from .program_text import encode_program_txt as _encode_program_txt
from .program_text import decode_program_txt as _decode_program_txt
from .registers import DM41MemoryError


def _checksum(data: bytes) -> int:
    '''Plain byte-sum checksum, mod 256 -- hp41uc's copy_raw_blocks()/
    copy_dat_blocks() checksum loop (`*pchksum += buf[j]`).'''
    return sum(data) % 256


def encode_program_raw(data: bytes) -> bytes:
    '''
    RAW format: (Source/hp41uc.c's `/r` help text):
    `[compiled code] + [1-byte checksum] + [trailer]`, where the trailer
    is zero-padding so the total file length is a multiple of 256 bytes
    (hp41uc's write_raw_checksum(), bufsize=256) -- if `data` is already
    an exact multiple of 256 bytes, a full extra 256-byte block is
    appended (checksum byte + 255 zero bytes), matching hp41uc exactly.
    '''
    checksum = _checksum(data)
    pad_len = 256 - (len(data) % 256)
    trailer = bytes([checksum]) + bytes(pad_len - 1)
    return data + trailer


def encode_program_dat(data: bytes) -> bytes:
    '''
    DAT format: (Source/hp41uc.c's `/d` help text):
    `[4-byte header] + [compiled code] + [2-byte checksum]`, all as
    upper-case ASCII hex digits. The header is `data`'s length as a
    big-endian 16-bit value; the checksum is the sum of `data`'s bytes
    plus the header's own two raw bytes (hp41uc's copy_file() FILE_DAT
    branch: `chksum += size_lo_byte + size_hi_byte` on top of
    copy_dat_blocks()'s data-byte sum), all mod 256.

    Raises ValueError if `data` is longer than 65535 bytes (the format's
    4-hex-digit header can't represent a larger size).
    '''
    length = len(data)
    if length > 0xFFFF:
        raise ValueError(
            f"Program is {length} bytes, too long for DAT format's "
            "16-bit length header (max 65535)."
        )
    size_hi = (length >> 8) & 0xFF
    size_lo = length & 0xFF
    checksum = (_checksum(data) + size_lo + size_hi) % 256
    header = f"{length:04X}".encode("ascii")
    body = data.hex().upper().encode("ascii")
    trailer = f"{checksum:02X}".encode("ascii")
    return header + body + trailer


def decode_program_raw(data: bytes) -> bytes:
    '''
    Recovers a program's instruction bytes from an HP41 RAW file
    (`encode_program_raw()`'s own format). RAW carries no length header
    of its own, so this uses `find_program_end()` -- the same forward
    opcode scan `Memory.get_program_bytes()` uses -- to find where the
    compiled code ends and the checksum trailer begins, then verifies
    that checksum byte.

    Raises DM41MemoryError if no terminating END is found, if there's no
    checksum byte after it, or if the checksum doesn't match.
    '''
    length = find_program_end(data)
    if length is None:
        raise DM41MemoryError(
            "Could not find a terminating END in this RAW file's compiled code."
        )
    if length >= len(data):
        raise DM41MemoryError(
            "RAW file has no checksum byte after its compiled code."
        )
    instruction_bytes = data[:length]
    checksum = data[length]
    expected = _checksum(instruction_bytes)
    if checksum != expected:
        raise DM41MemoryError(
            f"RAW file checksum mismatch: expected 0x{expected:02X}, "
            f"found 0x{checksum:02X}."
        )
    return instruction_bytes


# ASCII whitespace decode_program_dat() strips before
# decoding the hex data. This allows dat files to be formatted for
# readability (i.e., as in a "PPC" formatted file).

_DAT_WHITESPACE = b" \t\r\n\v\f"

_ASCII_HEX_DIGITS = frozenset(b"0123456789ABCDEFabcdef")


def _parse_strict_hex(data: bytes) -> int:
    '''A strict ASCII-hex-digits-ONLY parse. Unlike plain `int(data, 16)`,
    which also accepts a leading sign, an `0x`/`0X` prefix, and PEP 515
    underscores, this rejects anything that isn't one of the 16 hex-digit
    characters -- see `decode_program_dat()`'s own docstring for why that
    distinction matters for a file's length header specifically. (A
    stricter parser already exists elsewhere in this codebase for base
    addresses, but that one's own leniency is deliberate there, so this
    is a separate, narrower helper rather than a shared one.) Raises
    ValueError if `data` is empty or contains any non-hex-digit byte.'''
    if not data or any(b not in _ASCII_HEX_DIGITS for b in data):
        raise ValueError(f"not all ASCII hex digits: {data!r}")
    return int(data, 16)


def decode_program_dat(data: bytes) -> bytes:
    '''
    Recovers a program's instruction bytes from an HP41 DAT (or a "PPC" file
    (DAT that has newlines in it...) Reads the header, decodes that many
    ASCII-hex-encoded bytes, and verifies the trailing 2-hex-digit checksum.

    Raises DM41MemoryError if `data` is too short for its own declared
    length, contains non-hex-digit data, or the checksum doesn't match.

    The header is parsed with `_parse_strict_hex()`, not bare
    `int(data, 16)`: the latter also accepts a leading sign, an `0x`/`0X`
    prefix, and PEP 515 underscores, so a header like `b'-001'` would
    otherwise silently parse as -1 instead of being rejected here, and
    only fail much later with a confusing "DAT file is shorter than its
    own declared length (-1 bytes)" error from the length check below.
    '''
    stripped = bytes(b for b in data if b not in _DAT_WHITESPACE)

    if len(stripped) < 6:
        raise DM41MemoryError(
            "DAT file is too short for a 4-byte header and 2-byte checksum."
        )

    try:
        length = _parse_strict_hex(stripped[:4])
    except ValueError as e:
        raise DM41MemoryError(
            f"DAT file's 4-byte header isn't hex digits: {stripped[:4]!r}."
        ) from e

    body_hex = stripped[4 : 4 + 2 * length]
    checksum_hex = stripped[4 + 2 * length : 4 + 2 * length + 2]
    if len(body_hex) != 2 * length or len(checksum_hex) != 2:
        raise DM41MemoryError(
            f"DAT file is shorter than its own declared length ({length} bytes)."
        )
    try:
        instruction_bytes = bytes.fromhex(body_hex.decode("ascii"))
        checksum = int(checksum_hex, 16)
    except (ValueError, UnicodeDecodeError) as e:
        raise DM41MemoryError("DAT file contains non-hex-digit data.") from e

    size_hi = (length >> 8) & 0xFF
    size_lo = length & 0xFF
    expected = (_checksum(instruction_bytes) + size_lo + size_hi) % 256
    if checksum != expected:
        raise DM41MemoryError(
            f"DAT file checksum mismatch: expected 0x{expected:02X}, "
            f"found 0x{checksum:02X}."
        )
    return instruction_bytes


_PPC_LINE_WIDTH = 50

def encode_program_ppc(data: bytes) -> bytes:
    '''
    PPC format: DAT format that's word-wrapped to `_PPC_LINE_WIDTH` (50)
    characters per line, with a trailing newline after the last line. Based on
    the formatting of HP41 programs in the PPC Calculator Journal program
    listings.

    This is entirely to make the output file easier for a human to read.
    '''
    dat_text = encode_program_dat(data)
    lines = [
        dat_text[i : i + _PPC_LINE_WIDTH]
        for i in range(0, len(dat_text), _PPC_LINE_WIDTH)
    ]
    return b"\n".join(lines) + b"\n"


def encode_program_txt(data: bytes) -> bytes:
    '''
    TXT format: a plain-text HP-41 keystroke listing, UTF-8 encoded --
    the actual decompiling is memory/program_text.py's own
    `encode_program_txt()` (bytes -> str); this is only the "-> UTF-8
    bytes" file-format step on top of it, matching this module's own
    bytes-in/bytes-out convention for RAW/DAT/PPC above (and what
    gui/program_tab.py's `_EXPORT_FORMATS` dispatch, `Path.write_bytes()`,
    expects -- see that module's docstring). Not an hp41uc file format of
    its own the way RAW/DAT are, but hp41uc-style text either way -- see
    docs/program_text_io_plan.md.
    '''
    return _encode_program_txt(data).encode("utf-8")


def _decode_txt_bytes(data: bytes) -> str:
    '''
    Turns a TXT file's raw bytes into text: UTF-8 (a leading byte-order
    mark is dropped), falling back to CP437 if the file isn't valid
    UTF-8. hp41uc-era listings are 8-bit DOS text, and hp41uc's own
    compiler recognizes CP437 byte 0xE4 as Sigma (Σ+, CLΣ, ΣREG). CP437
    maps every byte to a character, so this never fails; a file that
    was really some other encoding shows up as a compile error instead
    (docs/mnemonic_dialects_plan.md sec 3.7).
    '''
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp437")


def decode_program_txt(data: bytes) -> bytes:
    '''
    Recovers a program's instruction bytes from a TXT file's own raw
    bytes -- the reverse of `encode_program_txt()` above. Decodes `data`
    to text (see `_decode_txt_bytes()` below), then compiles it via
    memory/program_text.py's own `decode_program_txt()` (str -> bytes;
    see that function's own docstring for the full compile-time rules
    and error conditions).

    Raises DM41MemoryError -- not the plain ValueError
    program_text.decode_program_txt() itself raises -- if `data` doesn't
    compile, so this matches decode_program_raw()/decode_program_dat()'s
    own error type (and what gui/program_tab.py's `_IMPORT_FORMATS`
    dispatch already catches).
    '''
    text = _decode_txt_bytes(data)
    try:
        return _decode_program_txt(text)
    except ValueError as e:
        raise DM41MemoryError(str(e)) from e
