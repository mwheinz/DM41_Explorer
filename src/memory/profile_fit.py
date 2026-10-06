"""
check_profile_fit(): will a memory state work on a given model?

Every state file opens with the DM41X profile, which is a superset of the
DM41L's (see device_profile.py), so a state can hold things a DM41L cannot
have. Before one is sent to a DM41L the app asks this module what would not
fit. The check is pure and synchronous, like the rest of the core, and it
changes nothing.

Findings come in two levels:

  ERROR    extended memory (XM) that lies where the model has no memory, so
           the state cannot be loaded as it stands. The app refuses to send.
  WARNING  something that loads but will not run on the model: an XROM
           function the model does not have built in, found in a program
           step or a key assignment; or leftover data in memory the model
           lacks. The app asks the user to confirm.

XM is judged by the file directory (ExtendedMemory.list_files()), never by
raw non-zero registers: a state can carry stale data above 0x300 that
belongs to no file (tests/data/dm41x_retpfl_before.d41), and that is only a
warning.

XROM steps are looked for in main-memory programs, in program files stored
in XM (a SAVEP file loaded back with GETP would still hold the step) and in
the Key Assignment Registers. A step is an HP-41 program line: the digits of
a number are one step, as on the calculator.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .device_profile import DeviceProfile, PROFILES
from .functions import XROM_FUNCTIONS
from .memory import Memory
from .opcode_scan import NUMBER_ENTRY_FIRST, NUMBER_ENTRY_LAST, iter_instructions
from .registers import DM41MemoryError
from .xm_file import XMFile

ERROR = "error"
WARNING = "warning"

# First bytes of a two-byte XROM instruction: 0xA0 + (module // 4).
_XROM_FIRST_BYTES = range(0xA0, 0xA8)


@dataclass(frozen=True)
class ProfileFinding:
    """One thing in a memory state that does not fit a model.

    `level` is ERROR or WARNING. `location` says where it was found
    ("Extended memory", 'Program "TEST", step 3', "Key 12 (unshifted)") and
    `message` says what is wrong there. str() joins them for display."""

    level: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


def check_profile_fit(memory: Memory, profile: DeviceProfile) -> List[ProfileFinding]:
    """What in `memory` will not fit a `profile` calculator, errors first.

    An empty list means the state loads and runs on that model as it is.
    `memory` can have been opened with any profile: it is read again under
    the widest known one when its own is narrower, so the answer does not
    depend on how the state was loaded."""
    view = _wide_view(memory)

    # Errors come only from the XM check, which runs first, so they are
    # listed ahead of the warnings.
    findings: List[ProfileFinding] = []
    files, xm_findings = _check_extended_memory(view, profile)
    findings += xm_findings
    if not xm_findings:
        findings += _check_leftover_data(view, profile)
    findings += _check_programs(view, profile)
    findings += _check_xm_programs(files, profile)
    findings += _check_key_assignments(view, profile)
    return findings


def format_findings(findings: List[ProfileFinding], limit: int = 12) -> str:
    """`findings` as a bulleted list for a dialog, one line each, cut off
    after `limit` with a count of how many more there are."""
    lines = [f"- {finding}" for finding in findings[:limit]]
    if len(findings) > limit:
        lines.append(f"...and {len(findings) - limit} more.")
    return "\n".join(lines)


def _wide_view(memory: Memory) -> Memory:
    """`memory` itself if its profile already covers the widest known
    model, otherwise a copy read under that model's profile (a state
    opened as a DM41L but holding DM41X memory would otherwise fail to
    walk its own XM directory)."""
    widest = max(PROFILES.values(), key=lambda p: p.xm_raw_registers)
    if memory.profile.xm_raw_registers >= widest.xm_raw_registers:
        return memory
    return Memory.from_string(memory.to_string(), profile=widest)


def _in_regions(addr: int, regions) -> bool:
    """True if `addr` is a usable register of one of `regions` (the
    pointer register at a region's `lo` is not usable)."""
    return any(lo < addr <= hi for lo, hi in regions)


# -- Extended memory ------------------------------------------------------


def _segment_outside(file: XMFile, regions) -> Optional[Tuple[int, int]]:
    """The first (start, end) range of `file`'s own data that is not in
    `regions`, or None. A file's empty placeholder segment (see
    XMFile.spans_regions) holds no data and is ignored."""
    for start, end in file.segments:
        if start <= end and not (
            _in_regions(start, regions) and _in_regions(end, regions)
        ):
            return start, end
    return None


def _check_extended_memory(
    view: Memory, profile: DeviceProfile
) -> Tuple[List[XMFile], List[ProfileFinding]]:
    """Returns the XM files (empty if the directory cannot be read) and
    the errors found."""
    try:
        files = view.extended_memory.list_files()
    except DM41MemoryError as e:
        return [], [
            ProfileFinding(
                ERROR,
                "Extended memory",
                f"the file directory cannot be read ({e}), so it cannot be "
                f"checked against a {profile.name}.",
            )
        ]

    regions = profile.xm_regions
    limit = f"0x{profile.display_end:03X}"
    outside = [(f, _segment_outside(f, regions)) for f in files]
    outside = [(f, segment) for f, segment in outside if segment is not None]
    if outside:
        file, (start, end) = outside[0]
        return files, [
            ProfileFinding(
                ERROR,
                "Extended memory",
                f"{len(outside)} of {len(files)} XM files use memory a "
                f"{profile.name} does not have (the first is "
                f"{file.name.strip()!r}, at 0x{start:03X}-0x{end:03X}). "
                f"A {profile.name}'s extended memory ends at {limit}: "
                "delete or shrink XM files until everything fits.",
            )
        ]

    # No file's data is out of range, but the directory's own end marker
    # (where the next file's name register would go) can still be.
    if files:
        terminator = files[-1].data_start - 1
        if not _in_regions(terminator, regions):
            return files, [
                ProfileFinding(
                    ERROR,
                    "Extended memory",
                    f"the XM file directory ends at 0x{terminator:03X}, where "
                    f"a {profile.name} has no memory (its extended memory "
                    f"ends at {limit}): delete or shrink XM files until "
                    "everything fits.",
                )
            ]
    return files, []


def _check_leftover_data(view: Memory, profile: DeviceProfile) -> List[ProfileFinding]:
    """Warns about non-zero registers in a region the model lacks that no
    XM file accounts for (the XM check has already passed, so none do)."""
    findings = []
    for lo, hi in view.profile.xm_regions:
        if (lo, hi) in profile.xm_regions:
            continue
        for addr in range(lo, hi + 1):
            register = view.get_register(addr)
            if register is not None and any(register.get_bytes()):
                findings.append(
                    ProfileFinding(
                        WARNING,
                        f"Registers 0x{lo:03X}-0x{hi:03X}",
                        f"hold leftover data that belongs to no XM file; a "
                        f"{profile.name} has no memory there.",
                    )
                )
                break
    return findings


# -- XROM functions -------------------------------------------------------


def _xrom_message(pair: Tuple[int, int], profile: DeviceProfile) -> str:
    """ "LKAOFF is not built into a DM41L", or the XROM's numbers when the
    function is not one the registry knows."""
    byte1, byte2 = pair
    name = XROM_FUNCTIONS.get(pair)
    if name is None:
        module = ((byte1 & 0x07) << 2) | (byte2 >> 6)
        name = f"XROM {module:02d},{byte2 & 0x3F:02d}"
    return f"{name} is not built into a {profile.name}"


def _check_instructions(
    data: bytes, where: str, profile: DeviceProfile
) -> List[ProfileFinding]:
    """One warning for every XROM step in `data` (a program's instruction
    bytes) that `profile` does not have built in. `where` names the program
    in the finding's location."""
    findings = []
    step = 0
    in_number = False
    for start, length in iter_instructions(data):
        first = data[start]
        is_number = NUMBER_ENTRY_FIRST <= first <= NUMBER_ENTRY_LAST
        if not (is_number and in_number):
            step += 1  # the digits of one number are one program line
        in_number = is_number
        if first in _XROM_FIRST_BYTES and length == 2:
            pair = (first, data[start + 1])
            if pair not in profile.builtin_xroms:
                findings.append(
                    ProfileFinding(
                        WARNING,
                        f"{where}, step {step}",
                        _xrom_message(pair, profile),
                    )
                )
    return findings


def _check_programs(view: Memory, profile: DeviceProfile) -> List[ProfileFinding]:
    findings = []
    try:
        for program in view.programs.list_programs():
            if program.labels:
                where = f'Program "{program.names_label}"'
            else:
                where = f"Unlabelled program at {program.address_label}"
            findings += _check_instructions(
                view.programs.get_program_bytes(program), where, profile
            )
    except (DM41MemoryError, ValueError) as e:
        findings.append(
            ProfileFinding(
                WARNING,
                "Program memory",
                f"cannot be read ({e}), so its steps were not checked.",
            )
        )
    return findings


def _check_xm_programs(
    files: List[XMFile], profile: DeviceProfile
) -> List[ProfileFinding]:
    findings = []
    for file in files:
        if file.file_type == XMFile.TYPE_PROGRAM:
            findings += _check_instructions(
                file.get_instruction_bytes(),
                f'XM file "{file.name.strip()}"',
                profile,
            )
    return findings


def _check_key_assignments(
    view: Memory, profile: DeviceProfile
) -> List[ProfileFinding]:
    findings = []
    for assignment in view.key_assignments.list_assignments():
        byte1, byte2 = assignment["fn_byte1"], assignment["fn_byte2"]
        if byte2 is None or byte1 not in _XROM_FIRST_BYTES:
            continue
        if (byte1, byte2) in profile.builtin_xroms:
            continue
        key_number = assignment["key_number"]
        if key_number is None:
            where = f"Key assignment (key byte 0x{assignment['raw_key_byte']:02X})"
        else:
            shift = "shifted" if assignment["shifted"] else "unshifted"
            where = f"Key {key_number:02d} ({shift})"
        findings.append(
            ProfileFinding(WARNING, where, _xrom_message((byte1, byte2), profile))
        )
    return findings
