'''
A forward HP-41 FOCAL opcode-length scanner: given a stream of raw program
bytes starting at a global label's header, finds exactly how many bytes
that program occupies by walking its opcodes, up to and including its own
terminating END marker.

This is a direct Python port of `seek_end()` from hp41uc (Leo Duran's
HP-41 User-Code File Converter, ~/Work/hp41uc/Source/decomp.c), used there
to find a program's length within a raw byte stream when converting
between HP-41 file formats. It walks forward
byte-by-byte, classifying each opcode by its length (single
byte; 2-byte; 3-byte; global END/LBL; variable-length ALPHA text) and
advancing a small state machine -- it does not understand what
any instruction *means*, only how many bytes it occupies.
'''

from enum import Enum
from typing import Optional

from .program_chain import decode_chain_marker, decode_label_name

# Opcode classification, mirroring hp41uc's decomp.c SEEK_* states.
class State(Enum):
    BYTE1 = 1          # expecting the start of a new instruction
    BYTE2_OF_2 = 2     # 1 more byte of a 2-byte instruction
    BYTE2_OF_3 = 3     # 2 more bytes of a 3-byte instruction
    BYTE3_OF_3 = 4
    BYTE2_GLOBAL = 5   # 2nd byte of a C0-CD END/LBL marker
    BYTE3_GLOBAL = 6   # 3rd byte -- decides END vs. LBL
    BYTE4_GLOBAL = 7   # LBL's key-assignment byte
    BYTE2_ALPHA = 8     # 2nd byte of a 1D-1F ALPHA-text opcode
    BYTE_ALPHA = 9     # remaining ALPHA-text character bytes


def find_program_end(data: bytes) -> Optional[int]:  # pylint: disable=too-many-branches
    '''
    Scans `data` (raw program bytes, starting at a global label's own
    header -- see Memory.get_program_bytes()) forward, opcode by opcode,
    for this program's own terminating END marker.

    Returns the number of bytes from the start of `data` through
    that END marker's 3rd byte, inclusive. This is the program's total
    byte length, the same number CAT 1 reports. Returns None if `data` is
    exhausted before a terminating END is found (the program doesn't fit
    in the bytes given -- see Memory.get_program_bytes() for how the
    caller bounds this against program memory's actual floor).
    '''
    state = State.BYTE1
    alpha_count = 0

    for i, c in enumerate(data):
        if state == State.BYTE1:
            if 0x1D <= c <= 0x1F:
                state = State.BYTE2_ALPHA
            elif (0x90 <= c <= 0xBF) or (0xCE <= c <= 0xCF):
                state = State.BYTE2_OF_2
            elif 0xC0 <= c <= 0xCD:
                state = State.BYTE2_GLOBAL
            elif 0xD0 <= c <= 0xEF:
                state = State.BYTE2_OF_3
            elif 0xF0 <= c <= 0xFF:
                alpha_count = c & 0x0F
                if alpha_count:
                    state = State.BYTE_ALPHA
            # else: a plain single-byte opcode -- stay in BYTE1

        elif state == State.BYTE2_OF_2:
            state = State.BYTE1

        elif state == State.BYTE2_OF_3:
            state = State.BYTE3_OF_3

        elif state == State.BYTE3_OF_3:
            state = State.BYTE1

        elif state == State.BYTE2_GLOBAL:
            state = State.BYTE3_GLOBAL

        elif state == State.BYTE3_GLOBAL:
            if c < 0xF0:
                # High nibble isn't F -- a plain END (or the permanent
                # .END.), not a label. This program is done.
                return i + 1
            alpha_count = c & 0x0F
            state = State.BYTE4_GLOBAL if alpha_count else State.BYTE1

        elif state == State.BYTE4_GLOBAL:
            alpha_count -= 1
            state = State.BYTE_ALPHA if alpha_count else State.BYTE1

        elif state == State.BYTE2_ALPHA:
            if c <= 0xF0:
                state = State.BYTE1
            else:
                alpha_count = c & 0x0F
                state = State.BYTE_ALPHA

        elif state == State.BYTE_ALPHA:
            alpha_count -= 1
            if alpha_count == 0:
                state = State.BYTE1

    return None


def scan_global_markers_forward(data: bytes) -> list:
    '''
    Walks `data` forward, opcode by opcode -- the exact same length-
    classification rules `find_program_end()` uses -- but instead of
    stopping at the first END-type marker, continues all the way through
    `data`, recording EVERY global marker (a label header, a plain END, or
    the permanent `.END.`) it passes, in physical/forward-discovery order.

    Never raises -- like `find_program_end()`, an opcode that would run
    past the end of `data` (a genuinely truncated/corrupt stream) simply
    ends the scan at whatever's already been found, rather than raising.
    This includes a label marker whose own header/name would run past
    the end of `data`: `decode_label_name()` is only called once its
    bytes are confirmed to fit (see `program_chain.walk_chain()`, which
    guards the same way).
    '''
    entries = []
    state = State.BYTE1
    alpha_count = 0
    marker_start = None

    for i, c in enumerate(data):
        if state == State.BYTE1:
            if 0x1D <= c <= 0x1F:
                state = State.BYTE2_ALPHA
            elif (0x90 <= c <= 0xBF) or (0xCE <= c <= 0xCF):
                state = State.BYTE2_OF_2
            elif 0xC0 <= c <= 0xCD:
                marker_start = i
                state = State.BYTE2_GLOBAL
            elif 0xD0 <= c <= 0xEF:
                state = State.BYTE2_OF_3
            elif 0xF0 <= c <= 0xFF:
                alpha_count = c & 0x0F
                if alpha_count:
                    state = State.BYTE_ALPHA
            # else: a plain single-byte opcode -- stay in BYTE1

        elif state == State.BYTE2_OF_2:
            state = State.BYTE1

        elif state == State.BYTE2_OF_3:
            state = State.BYTE3_OF_3

        elif state == State.BYTE3_OF_3:
            state = State.BYTE1

        elif state == State.BYTE2_GLOBAL:
            state = State.BYTE3_GLOBAL

        elif state == State.BYTE3_GLOBAL:
            marker = decode_chain_marker(data, marker_start)
            entry = dict(marker)
            entry["index"] = marker_start
            if marker["is_label"]:
                label_end = marker_start + 4 + max(marker["label_length"], 0)
                if label_end > len(data):
                    break  # label's own header/name runs past data's end --
                           # truncated/corrupt stream, stop here like
                           # find_program_end() would
                name, key = decode_label_name(data, marker_start, marker["label_length"])
                entry["name"] = name
                entry["key_assignment"] = key
            entries.append(entry)

            if c < 0xF0:
                # A plain END (or the permanent .END.) -- not a label.
                state = State.BYTE1
            else:
                alpha_count = c & 0x0F
                state = State.BYTE4_GLOBAL if alpha_count else State.BYTE1

        elif state == State.BYTE4_GLOBAL:
            alpha_count -= 1
            state = State.BYTE_ALPHA if alpha_count else State.BYTE1

        elif state == State.BYTE2_ALPHA:
            if c <= 0xF0:
                state = State.BYTE1
            else:
                alpha_count = c & 0x0F
                state = State.BYTE_ALPHA

        elif state == State.BYTE_ALPHA:
            alpha_count -= 1
            if alpha_count == 0:
                state = State.BYTE1

    return entries


# -- Instruction-level walking and the byte-level half of PACK -------------

# Number-entry bytes: the digits 0-9, '.', EEX and CHS-in-a-number
# (0x10-0x1C). The HP-41 puts a NULL in front of every number keyed into a
# program, and a real PACK deletes it again unless it's what keeps two
# consecutive numbers from running together.
NUMBER_ENTRY_FIRST = 0x10
NUMBER_ENTRY_LAST = 0x1C


def _instruction_length(data, i: int) -> int:
    '''
    Byte length of the instruction starting at `data[i]` (a global END/LBL
    marker counts as one instruction, including a label's key byte and
    name), using exactly the classification `find_program_end()` uses.
    Never runs past `len(data)`: a truncated final instruction just gets
    whatever bytes are left.
    '''
    c = data[i]
    if 0x1D <= c <= 0x1F:
        if i + 1 >= len(data):
            return 1
        c2 = data[i + 1]
        length = 2 if c2 <= 0xF0 else 2 + (c2 & 0x0F)
    elif (0x90 <= c <= 0xBF) or (0xCE <= c <= 0xCF):
        length = 2
    elif 0xC0 <= c <= 0xCD:
        if i + 2 >= len(data):
            return len(data) - i
        third = data[i + 2]
        length = 3 + (third & 0x0F if third >= 0xF0 else 0)
    elif 0xD0 <= c <= 0xEF:
        length = 3
    elif 0xF0 <= c <= 0xFF:
        length = 1 + (c & 0x0F)
    else:
        length = 1
    return min(length, len(data) - i)


def iter_instructions(data):
    '''Yields `(start_index, length)` for every instruction in `data`, in
    order. A global END/LBL marker is one instruction.'''
    i = 0
    while i < len(data):
        length = _instruction_length(data, i)
        yield i, length
        i += length


def _clear_jump_cache(instruction: bytearray):
    '''Zeroes the cached jump-distance bits of one GTO/XEQ instruction, in
    place (docs/program.md; Handbook "GTO"/"XEQ"): the second byte of the
    compact `0xB1-0xBF` form, and in the general 3-byte `0xD0-0xEF` form
    the low nibble of the first byte, all of the second, and the direction
    bit (top bit) of the third, which holds the label itself in its low 7
    bits.'''
    first = instruction[0]
    if 0xB1 <= first <= 0xBF and len(instruction) == 2:
        instruction[1] = 0x00
    elif 0xD0 <= first <= 0xEF and len(instruction) == 3:
        instruction[0] = first & 0xF0
        instruction[1] = 0x00
        instruction[2] &= 0x7F


def clear_jump_caches(data: bytes) -> bytes:
    '''`data` with every GTO/XEQ's cached jump distance reset to "not
    resolved yet" (zero) -- the calculator works the real distance out
    again when the program next runs. Everything else is copied verbatim,
    including global END/LBL markers.'''
    out = bytearray(data)
    for start, length in iter_instructions(data):
        instruction = bytearray(out[start : start + length])
        _clear_jump_cache(instruction)
        out[start : start + length] = instruction
    return bytes(out)


def compact_program_stream(data: bytes) -> bytes:
    '''
    The byte-level half of what a real PACK does to program memory,
    confirmed against before/after captures from a real DM41L/DM41X
    (tests/data/manyfiles*.dm41 and the lander/targ pairs):

    - every standalone NULL is deleted, except one that sits between two
      number-entry instructions (it is what keeps them apart: "12345 NULL
      67890" is two numbers, "1234567890" is one);
    - every GTO/XEQ's cached jump distance is cleared (`clear_jump_caches`)
      because deleting bytes can invalidate it.

    Markers (END, labels, and `.END.`) are copied verbatim -- the caller
    is responsible for re-linking them, since this changes the distances
    between them. Operand bytes that happen to be zero (`FIX 0`, `ISG 00`,
    ...) are never touched: only a NULL that is itself an instruction goes.
    '''
    instructions = list(iter_instructions(data))
    out = bytearray()
    previous_is_number = False
    for position, (start, length) in enumerate(instructions):
        first = data[start]
        if first == 0x00:
            # Only the *last* NULL of a run can matter -- peek past the run
            # to see what follows it.
            following = position + 1
            while following < len(instructions) and data[instructions[following][0]] == 0x00:
                following += 1
            if following == position + 1:  # last NULL of its run
                next_is_number = following < len(instructions) and (
                    NUMBER_ENTRY_FIRST <= data[instructions[following][0]] <= NUMBER_ENTRY_LAST
                )
                if previous_is_number and next_is_number:
                    out.append(0x00)
                    previous_is_number = False
            continue
        instruction = bytearray(data[start : start + length])
        _clear_jump_cache(instruction)
        out += instruction
        previous_is_number = NUMBER_ENTRY_FIRST <= first <= NUMBER_ENTRY_LAST
    return bytes(out)
