'''
A representation of the memory of a DM41-series emulator (DM41L, DM41X).

The dump consists of three parts: the header ("DM41") the dump of main
memory, and the "special registers" which I believe represent the emulated
HP41 CPU registers.

NOTE: The HP41C and DM41L are "little endian" - the LSB is considered to by
"byte 0" and the MSB is considered "byte 6" - but DM41L dump files print hex
data from MSB to LSB. That is, _data[0] contains the MSB of the register. and
_data[6] contains the LSB. Care must be taken to remember this difference when
comparing HP41 documentation with the implementation of the Register and
Memory classes.

This used to be a single memory.py file; it was split into this package
for readability. The current components are:

  registers.py     Register, AlphaRegister, DM41MemoryError (alias DM41LMemoryError),
                    format_data_line/parse_data_line (DATA line format)
  trigraphs.py     encode_trigraphs/decode_trigraphs/focal_to_unicode (docs/trigraphs.md --
                    the HP41/DM41L FOCAL character set's non-ASCII symbols)
  constants.py     address-range and sentinel-register constants
  regions.py       MemoryRegion (the base class -- a live view of one
                    named span, whose boundaries are recomputed on every
                    access so it can never go stale), RegionSpan (an
                    immutable boundary snapshot, what Memory.regions()
                    returns), and the two derived-only regions VoidRegion
                    and FreeSpace
  status_registers.py
                   StatusRegisters -- the 16 named CPU/system registers,
                    the 56 status flags in register d, and the
                    SIGMA-REG/R00/.END. pointers packed into register c
  key_assignments.py
                   KeyAssignments -- the Key Assignment Registers, the
                    keyboard geometry behind key bytes and KEYFLAGS bits,
                    and the assignment set/delete/list API
  alarms.py        Alarm, Alarms -- the alarms buffer's outer bounds, the
                    block relocation a key-assignment edit forces on it,
                    and per-entry decode/list/add/delete (time/repeat/
                    message parsing, the up-arrow control/conditional
                    type marker, sorted insertion)
  program_memory.py
                   ProgramMemory -- user program storage: the global
                    chain, list_programs(), program import/export/removal,
                    the forward-scan rebuild PACK needs, and global-label
                    key assignments
  data_memory.py   DataMemory -- the primary data registers (R00 upward),
                    addressed by register number as well as by address
  xm_file.py       XMFile, ExtendedMemory (extended-memory file storage)
  program_info.py  ProgramInfo (raw program-memory "global chain" entries)
                    and ProgramLabel/Program (the grouped, END-delimited
                    "real program" view built on top of that chain)
  opcode_scan.py   find_program_end() -- forward HP-41 opcode-length
                    scanner (ported from hp41uc's seek_end(), see
                    ~/Work/hp41uc/Source/decomp.c), used by
                    Memory.get_program_bytes() to find one named
                    program's own byte range for export
  program_files.py encode_program_raw/encode_program_dat and
                    decode_program_raw/decode_program_dat -- hp41uc-
                    compatible single-program file formats (RAW/DAT;
                    see ~/Work/hp41uc/Source/convert.c) -- plus
                    encode_program_ppc for the "PPC" format (DAT's own
                    hex text, word-wrapped) and encode_program_txt/
                    decode_program_txt, thin UTF-8-bytes wrappers around
                    program_text.py's own str-based converters, for the
                    plain-text keystroke-listing format
  program_chain.py byte-level global-chain marker parsing/encoding
                    (walk_chain/decode_chain_marker/decode_label_name/
                    encode_chain_marker) operating on a plain bytes
                    buffer rather than register-addressed Memory state --
                    what Memory.import_program() (memory.py) uses to
                    inspect/patch a standalone program's bytes before
                    splicing them into program memory
  program_text.py  encode_program_txt()/decode_program_txt() -- converts
                    between a program's raw instruction bytes and
                    hp41uc-style plain text (str, not bytes -- see
                    docs/program_text_io_plan.md); program_files.py
                    wraps these as the package's own TXT format entry,
                    matching RAW/DAT/PPC's bytes-in/bytes-out shape
  mnemonics.py     the FOCAL instruction-name registry: resolve() any
                    accepted spelling to an Op, canonical() (hp41uc,
                    ASCII, for export) and display() (HP-41 native, for
                    the GUI) names -- docs/mnemonic_dialects_plan.md
  mnemonic_dialects.py
                   hand-maintained alternate-spelling dialects that
                    mnemonics.py folds into its registry
  mnemonic_doc.py  the generated FOCAL mnemonic reference: rows for the
                    Help menu dialog, and docs/mnemonics.md
                    (`python -m memory.mnemonic_doc`)
  memory.py        Memory (the top-level dump: parsing, serialization,
                    raw register access, whole-dump pack(), and the
                    region lookup -- Memory.region(key) plus the named
                    properties .status_registers/.key_assignments/
                    .alarms/.free_space/.programs/.data_memory/
                    .extended_memory -- through which all region-specific
                    behavior is reached)
'''

from .registers import (
    DM41MemoryError,
    DM41LMemoryError,
    Register,
    AlphaRegister,
    format_data_line,
    parse_data_line,
)
from .trigraphs import encode_trigraphs, decode_trigraphs, focal_to_unicode
from .constants import (
    STATUS_REGISTERS_RANGE,
    VOID_RANGE,
    KEY_ASSIGNMENTS_RANGE,
    PRIMARY_DATA_END,
    ZERO_REGISTER_HEX,
    EOM_REGISTER_HEX,
    zero_register,
    eom_register,
    STATUS_REGISTER_LABELS,
    XM_REGIONS,
    MIN_SANE_R00,
)
from .regions import MemoryRegion, RegionSpan, VoidRegion, FreeSpace
from .status_registers import StatusRegisters
from .key_assignments import KeyAssignments
from .alarms import Alarm, Alarms
from .program_memory import ProgramMemory
from .data_memory import DataMemory
from .xm_file import XMFile, ExtendedMemory, NAME_MIN_CHAR, NAME_MAX_CHAR
from .program_info import ProgramInfo, ProgramLabel, Program
from .opcode_scan import find_program_end, scan_global_markers_forward
from .program_files import (
    encode_program_raw,
    encode_program_dat,
    encode_program_ppc,
    encode_program_txt,
    decode_program_raw,
    decode_program_dat,
    decode_program_txt,
)
from .program_chain import (
    walk_chain,
    decode_chain_marker,
    decode_label_name,
    encode_chain_marker,
)
from .memory import Memory

__all__ = [
    "DM41MemoryError",
    "DM41LMemoryError",
    "Register",
    "AlphaRegister",
    "format_data_line",
    "parse_data_line",
    "encode_trigraphs",
    "decode_trigraphs",
    "focal_to_unicode",
    "STATUS_REGISTERS_RANGE",
    "VOID_RANGE",
    "KEY_ASSIGNMENTS_RANGE",
    "PRIMARY_DATA_END",
    "ZERO_REGISTER_HEX",
    "EOM_REGISTER_HEX",
    "zero_register",
    "eom_register",
    "STATUS_REGISTER_LABELS",
    "XM_REGIONS",
    "MIN_SANE_R00",
    "MemoryRegion",
    "RegionSpan",
    "VoidRegion",
    "FreeSpace",
    "StatusRegisters",
    "KeyAssignments",
    "Alarm",
    "Alarms",
    "ProgramMemory",
    "DataMemory",
    "XMFile",
    "ExtendedMemory",
    "NAME_MIN_CHAR",
    "NAME_MAX_CHAR",
    "ProgramInfo",
    "ProgramLabel",
    "Program",
    "find_program_end",
    "scan_global_markers_forward",
    "encode_program_raw",
    "encode_program_dat",
    "encode_program_ppc",
    "decode_program_raw",
    "decode_program_dat",
    "encode_program_txt",
    "decode_program_txt",
    "walk_chain",
    "decode_chain_marker",
    "decode_label_name",
    "encode_chain_marker",
    "Memory",
]
