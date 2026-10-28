"""
A representation of the memory of a DM41-series emulator (DM41L, DM41X).

A state has three parts: the "DM41" header, the main memory registers, and
the "special registers" (apparently the emulated HP-41 CPU registers).
`Memory` (memory.py) holds one, and reaches each kind of memory through a
region object; everything public is re-exported here.

Byte order: the HP-41 numbers a register's bytes from the least
significant (byte 0) to the most significant (byte 6), but state files
print them most significant first, and Register stores them in that
printed order (`_data[0]` is the MSB). Keep this in mind when comparing
HP-41 documentation with Register and Memory.

Modules, by area (each module's docstring has the details):

  State and registers
    memory.py            Memory: parsing, saving, register access, pack()
    registers.py         Register, AlphaRegister, the DATA line format
    constants.py         address ranges and sentinel registers
    device_profile.py    per-model memory maps (DeviceProfile, DeviceMode)
    profile_fit.py       what in a state won't fit a given model
    atomic_write.py      replacing a text file in one step

  Regions
    regions.py           MemoryRegion base class, RegionSpan, VoidRegion,
                         FreeSpace
    status_registers.py  the 16 status registers, flags, R00/.END. pointers
    key_assignments.py   the Key Assignment Registers and KEYFLAGS
    alarms.py            the alarms buffer
    program_memory.py    user programs and the global chain
    data_memory.py       the primary data registers (R00 upward)
    xm_file.py           extended memory and its files

  Programs
    program_info.py      chain entries and the grouped program view
    program_chain.py     global-chain markers in a plain byte buffer
    opcode_scan.py       forward opcode scanning (program length, PACK)
    program_files.py     RAW/DAT/PPC/TXT program file formats
    program_text.py      instruction bytes <-> plain-text listings

  FOCAL names and characters
    functions.py         built-in and XROM function codes
    mnemonics.py         the instruction-name registry
    mnemonic_dialects.py alternate spellings folded into the registry
    mnemonic_doc.py      the generated mnemonic reference
    trigraphs.py         the FOCAL character set's non-ASCII symbols
    xrom_library.py      XROM library files: loading, priority, conflicts
"""

from .registers import (
    DM41MemoryError,
    Register,
    AlphaRegister,
    format_data_line,
    parse_data_line,
)
from .trigraphs import encode_trigraphs, decode_trigraphs, focal_to_unicode
from .device_profile import (
    DeviceProfile,
    DeviceMode,
    DEFAULT_MODE,
    DM41L,
    DM41X,
    PROFILES,
)
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
    MIN_SANE_R00,
)
from .regions import MemoryRegion, RegionSpan, VoidRegion, FreeSpace
from .status_registers import StatusRegisters
from .key_assignments import KeyAssignment, KeyAssignments
from .alarms import Alarm, Alarms
from .program_memory import ProgramMemory
from .data_memory import DataMemory
from .xm_file import XMFile, XMHeader, ExtendedMemory, NAME_MIN_CHAR, NAME_MAX_CHAR
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
    split_programs,
)
from .program_chain import (
    ChainMarker,
    walk_chain,
    decode_chain_marker,
    decode_label_name,
    encode_chain_marker,
)
from .memory import Memory
from .atomic_write import atomic_write_text
from .profile_fit import (
    ERROR,
    WARNING,
    ProfileFinding,
    check_profile_fit,
    evaluate_mode_switch,
    format_findings,
)
from .xrom_library import (
    ActiveSet,
    XromFinding,
    XromFunction,
    XromLibrary,
    XromLibraryError,
    XromModule,
    active_set,
    builtin_library,
    find_conflicts,
    load_libraries,
    load_library,
)

__all__ = [
    "DM41MemoryError",
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
    "DeviceProfile",
    "DeviceMode",
    "DEFAULT_MODE",
    "DM41L",
    "DM41X",
    "PROFILES",
    "MIN_SANE_R00",
    "MemoryRegion",
    "RegionSpan",
    "VoidRegion",
    "FreeSpace",
    "StatusRegisters",
    "KeyAssignment",
    "KeyAssignments",
    "Alarm",
    "Alarms",
    "ProgramMemory",
    "DataMemory",
    "XMFile",
    "XMHeader",
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
    "split_programs",
    "ChainMarker",
    "walk_chain",
    "decode_chain_marker",
    "decode_label_name",
    "encode_chain_marker",
    "Memory",
    "atomic_write_text",
    "ERROR",
    "WARNING",
    "ProfileFinding",
    "check_profile_fit",
    "evaluate_mode_switch",
    "format_findings",
    "ActiveSet",
    "XromFinding",
    "XromFunction",
    "XromLibrary",
    "XromLibraryError",
    "XromModule",
    "active_set",
    "builtin_library",
    "find_conflicts",
    "load_libraries",
    "load_library",
]
