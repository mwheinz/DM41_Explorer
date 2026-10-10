'''
Byte-level parsing/encoding of the HP-41 "global chain" marker format
(docs/program.md sec 5.1/5.2), operating directly on a plain `bytes` buffer
rather than register-addressed `Memory` state.

This is the one implementation of that decoding. `ProgramMemory`
(program_memory.py) uses it for live registers too: its
`_decode_chain_marker()`/`_decode_label_name()` read the bytes forward from
program memory (`read_bytes_forward()`) and hand them to the functions
here. `ProgramMemory.import_program()` applies it directly to a standalone
RAW/DAT-decoded instruction-byte blob (program_files.py) *before* it's
spliced into program memory at all -- there's no register addressing to
lean on yet at that point, just the bytes themselves.

Marker format (docs/program.md sec 5.1/5.2), 3 bytes, in on-calculator
forward reading order (data[index] is the highest-address/first-read byte):

    byte 0: 1100 bbb d   (top nibble 0xC; bbb is 3 bits; d is the 9th/top
                           bit of distance_registers)
    byte 1: dddddddd     (the low 8 bits of distance_registers)
    byte 2: either `eeee ffff` (plain END: eeee=0 normal/2=.END., ffff=
             packed-status) or `F` + (name length + 1) for a global label

byte 0/1 together encode `distance_registers`/`bbb`; byte 2 ("third_byte"
here) means something different depending on whether this marker is a
label or not, and is never touched by import_program()'s own splicing
logic except to flip a `.END.`-turned-real-`END`'s high nibble -- see
ProgramMemory.import_program().
'''

from dataclasses import dataclass, replace
from typing import Optional

from .registers import REGISTER_SIZE_BYTES


@dataclass(frozen=True)
class ChainMarker:
    '''One decoded 3-byte global-chain marker (see the module docstring).

    `end_type` is set only for an END (0 = normal END, 2 = the permanent
    `.END.`), `label_length` only for a global label. `index`, `name` and
    `key_assignment` are filled in by walk_chain() and
    opcode_scan.scan_global_markers_forward(), which know where in their
    buffer the marker sits; `name`/`key_assignment` only for a label.'''

    bbb: int
    distance_registers: int
    is_label: bool
    end_type: Optional[int]
    label_length: Optional[int]
    third_byte: int
    index: Optional[int] = None
    name: Optional[str] = None
    key_assignment: Optional[int] = None

    @property
    def distance_bytes(self) -> int:
        '''The link back to the previous marker, in bytes (0 = none).'''
        return self.distance_registers * REGISTER_SIZE_BYTES + self.bbb


def decode_chain_marker(data: bytes, index: int) -> Optional[ChainMarker]:
    '''
    Decodes the 3-byte marker at `data[index:index+3]`. Returns None if
    `index` is out of bounds or the byte there doesn't start with the
    0xC0-0xCD marker range (docs/program.md sec 5.1) -- `bbb`, the 3-bit
    field packed into this byte alongside the 0xC nibble, is a byte
    offset within a 7-byte register and so is only ever 0-6 for a real
    marker; `0xCE`/`0xCF` (bbb == 7) are never markers, and are in active
    use as ordinary 2-byte opcodes elsewhere in FOCAL code (`X<>` on a
    synthetic status register, `LBL nn` in its general/non-compact form
    -- see src/tests/test_program_text.py).
    '''
    if index < 0 or index + 3 > len(data):
        return None
    raw = data[index : index + 3]
    if not 0xC0 <= raw[0] <= 0xCD:
        return None
    val = (raw[0] << 16) | (raw[1] << 8) | raw[2]
    is_label = (raw[2] >> 4) == 0xF
    return ChainMarker(
        bbb=(val >> 17) & 0x7,
        distance_registers=(val >> 8) & 0x1FF,
        is_label=is_label,
        end_type=None if is_label else (raw[2] >> 4),
        label_length=(raw[2] & 0x0F) - 1 if is_label else None,
        third_byte=raw[2],
    )


def decode_label_name(data: bytes, index: int, length: int) -> tuple:
    '''Decodes a global label's name and key-assignment byte given where
    its 4-byte header starts in `data` (docs/program.md sec 5.2).
    Non-printable name bytes come back as "?". Returns
    (name, key_assignment).'''
    end = index + 4 + max(length, 0)
    combined = data[index:end]
    key_assignment = combined[3]
    name = "".join(chr(b) if 0x20 <= b <= 0x7E else "?" for b in combined[4:])
    return name, key_assignment


def located_marker(data: bytes, index: int) -> Optional[ChainMarker]:
    '''decode_chain_marker() plus where it sits: the marker at `index`
    with `index` set and, for a global label, its `name` and
    `key_assignment` too. None if there is no marker at `index`, or if a
    label's header/name would run past the end of `data` (truncated or
    corrupt input, not a real label).'''
    marker = decode_chain_marker(data, index)
    if marker is None:
        return None
    if not marker.is_label:
        return replace(marker, index=index)
    if index + 4 + max(marker.label_length, 0) > len(data):
        return None
    name, key = decode_label_name(data, index, marker.label_length)
    return replace(marker, index=index, name=name, key_assignment=key)


def encode_chain_marker(bbb: int, distance_registers: int, third_byte: int) -> bytes:
    '''
    The counterpart to `decode_chain_marker()`: packs
    `bbb`/`distance_registers` (docs/program.md sec 5.1's `bbb`/`rrrrrrrrr`
    fields) plus a caller-supplied `third_byte` (passed through completely
    unchanged -- callers own whatever it should mean, label-length-plus-key
    byte or END type/packed-status) into the 3 raw marker bytes.

    Raises ValueError if `bbb` or `distance_registers` don't fit their
    field widths (3 and 9 bits respectively).
    '''
    if not 0 <= bbb <= 0x7:
        raise ValueError(f"bbb must fit in 3 bits (0-7), got {bbb}")
    if not 0 <= distance_registers <= 0x1FF:
        raise ValueError(
            f"distance_registers must fit in 9 bits (0-511), got {distance_registers}"
        )
    if not 0 <= third_byte <= 0xFF:
        raise ValueError(f"third_byte must fit in a byte (0-255), got {third_byte}")
    byte0 = 0xC0 | (bbb << 1) | ((distance_registers >> 8) & 0x1)
    byte1 = distance_registers & 0xFF
    return bytes([byte0, byte1, third_byte])


def walk_chain(data: bytes) -> list:
    '''
    Walks `data`'s own internal global chain backward, starting from its
    trailing 3-byte marker (`data[-3:]` -- every well-formed single-program
    byte blob, per `ProgramMemory.get_program_bytes()`/`decode_program_raw()`/
    `decode_program_dat()`, ends in one), and following each marker's own
    `bbb`/`distance_registers` field exactly as
    `ProgramMemory.list_global_chain()` does against live registers --
    except entirely within `data`'s own bounds, using byte index instead
    of absolute address (index increases in the same forward-reading
    direction `ProgramMemory.read_bytes_forward()` uses, i.e. toward the
    *end* of `data`).

    Returns a list of ChainMarkers with `index` (and, for a label,
    `name`/`key_assignment`) filled in, ordered
    newest-to-oldest: `result[0]` is always the trailing marker itself,
    and `result[-1]` is the *outermost* one -- the marker whose own
    distance field pointed *outside* `data` (or reported no predecessor at
    all, i.e. `bbb == distance_registers == 0`) in `data`'s original
    source memory. `ProgramMemory.import_program()` is the only caller that needs
    this: `result[-1]` is exactly the marker whose distance field has to
    be recomputed to link into the *destination* memory instead (every
    other entry's distance is an internal, position-independent
    relationship between two markers both inside `data`, and stays valid
    as-is when `data` is copied verbatim).

    Never raises -- stops (silently, the same defensive posture as
    `list_global_chain()`) if a computed target lands out of `data`'s own
    bounds (this is the normal/expected way the walk ends, not an error),
    if a target lands in-bounds but doesn't decode to a valid marker, or
    decodes as a global label whose own header/name would run past the
    end of `data` (shouldn't happen for well-formed HP-41 code; treated
    as if the current entry were outermost rather than raising, in case
    of a slightly-off fixture or a truncated/corrupt buffer), or after a
    generous iteration cap as a backstop against a corrupt circular
    chain.
    '''
    if len(data) < 3:
        return []

    entries = []
    index = len(data) - 3
    visited = set()
    for _ in range(256):
        if index in visited:
            break
        visited.add(index)

        # None also covers a label whose header/name runs past data's
        # end -- truncated/corrupt input, not a real label here.
        marker = located_marker(data, index)
        if marker is None:
            break
        entries.append(marker)

        if marker.distance_bytes == 0:
            break  # no predecessor -- outermost by construction

        target_index = index - marker.distance_bytes
        if target_index < 0:
            break  # points outside `data` -- this entry is outermost
        index = target_index

    return entries
