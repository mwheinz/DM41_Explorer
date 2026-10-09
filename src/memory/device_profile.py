'''
DeviceProfile: the per-model facts a Memory needs that differ between DM41
models.

Every DM41-series calculator shares one HP-41CX memory model and one state
text format (a DM41L `.dm41` state and a DM41X `.d41` state file are the same
format, with the same `DM41` header line), so a file cannot say which model
wrote it. The caller says: Memory.from_string(text, profile=DM41X).

A profile holds the list of extended-memory regions, the set of XROM
functions the model has built in, and whether the model can be reached over
a serial console. Each region is (lo, hi): `lo` is the region's reserved
pointer/link register and the usable registers are lo+1 .. hi inclusive --
see docs/extended_memory.md. An XROM is identified by its (byte1, byte2)
pair, as in memory/functions.py.

DeviceMode, at the end of this module, is the user-facing choice of which
profile the application works in (docs/dm41x_explorer_plan.md phase 6). The
mode is the single source of truth for the active profile: not the file's
extension, and not what is plugged in.
'''

import enum
from dataclasses import dataclass
from typing import FrozenSet, Tuple

from .functions import CX_XROM_FUNCTIONS, XROM_FUNCTIONS


@dataclass(frozen=True)
class DeviceProfile:
    '''An immutable description of one DM41 model's memory map and the XROM
    functions built into it.

    `builtin_xroms` is only about what the *calculator* has. The name
    registry in memory/mnemonics.py always knows every XROM in
    functions.XROM_FUNCTIONS, whatever profile is in use, so a program using
    a DM41X function still compiles and decompiles. Something that needs to
    know whether a given model can run it (the check before sending a state
    to a DM41L) asks the profile.

    `supports_serial` is whether this model has a serial console at all, and
    so whether the application's Connect/Get/Send actions mean anything in
    its mode. It is a property of the model, deliberately not a test against
    `name`: the DM41XN has both a FAT disk and a serial interface, so adding
    its profile later must not require revisiting every gating decision.
    Defaults to False, so a new profile has to opt in.'''

    name: str
    xm_regions: Tuple[Tuple[int, int], ...]
    builtin_xroms: FrozenSet[Tuple[int, int]]
    supports_serial: bool = False

    def __post_init__(self):
        if not self.xm_regions:
            raise ValueError("A DeviceProfile needs at least one XM region")
        previous_hi = -1
        for lo, hi in self.xm_regions:
            # Regions must be ascending and disjoint; a region needs its
            # pointer register (lo) plus at least one usable register.
            if not previous_hi < lo < hi:
                raise ValueError(
                    f"Bad XM region (0x{lo:x}, 0x{hi:x}) in profile {self.name}"
                )
            previous_hi = hi

    @property
    def xm_address_range(self) -> Tuple[int, int]:
        '''Outermost extent XM can occupy: the first region's pointer
        register through the last region's ceiling. The gaps between
        regions are inside this range but are not XM.'''
        return (self.xm_regions[0][0], self.xm_regions[-1][1])

    @property
    def display_end(self) -> int:
        '''Highest address of the model's addressable memory map (the last
        XM region's ceiling) -- the end of the Hex View's range.'''
        return self.xm_regions[-1][1]

    @property
    def xm_raw_registers(self) -> int:
        '''Structural XM capacity in registers, excluding each region's
        reserved pointer register.'''
        return sum(hi - lo for lo, hi in self.xm_regions)


# The extended-memory regions the calculator can address. Region 0 emulates
# an Extended Functions module and the later regions emulate Extended Memory
# modules. (The HP-41CX supports one Extended Functions module and up to 2
# Extended Memory modules.)
#
# The DM41L has region 0 plus ONE Extended Memory module. Note that its
# second region actually extends down to address 0x200, but that register is
# never used on a DM41L and is always zero.
#
# Its XROMs are only those of the original HP-41CX modules (Extended
# Functions/Memory and Time); it cannot load any other module.
DM41L = DeviceProfile(
    "DM41L",
    ((0x40, 0xBF), (0x201, 0x2EF)),
    frozenset(CX_XROM_FUNCTIONS),
    supports_serial=True,
)

# The DM41X adds a second Extended Memory module at 0x301-0x3EF -- confirmed
# against tests/data/dm41x_manyfiles.dm41, a real state whose files span all
# three regions. Its XROMs are the CX set plus its own additions (X<I>Y, TRNG
# and the DM41X module), which is all of functions.XROM_FUNCTIONS. The
# DM41XN is treated identically (docs/dm41x_explorer_plan.md), except that
# its newer USB interface does offer a serial console; when its own profile
# is added it will set supports_serial=True. The DM41X is strictly
# file-based, so its mode offers no serial actions.
DM41X = DeviceProfile(
    "DM41X",
    ((0x40, 0xBF), (0x201, 0x2EF), (0x301, 0x3EF)),
    frozenset(XROM_FUNCTIONS),
    supports_serial=False,
)

PROFILES = {profile.name: profile for profile in (DM41L, DM41X)}


class DeviceMode(enum.Enum):
    '''Which calculator the application is working as, chosen by the user
    and persisted in the preferences file (config.py's "mode").

    One mode per profile, and the mode picks the profile for everything:
    the memory map a state is opened with, the XM capacity the Overview
    reports, the keyboard layout the Key Assignments tab draws, the
    functions its edit dialog offers, and whether the serial actions are
    available at all.

    A real enumerated type rather than bare strings, because this is a
    closed set of named modes; `value` is the stored/display name, which is
    also the profile's own `name`.'''

    DM41L = "DM41L"
    DM41X = "DM41X"

    @property
    def profile(self) -> DeviceProfile:
        '''The DeviceProfile this mode works in.'''
        return PROFILES[self.value]

    @property
    def supports_serial(self) -> bool:
        '''Whether the serial actions mean anything in this mode. Asks the
        profile; never tests the mode's name.'''
        return self.profile.supports_serial

    @classmethod
    def from_value(cls, value, default: "DeviceMode" = None) -> "DeviceMode":
        '''The mode named `value`, or `default` (DEFAULT_MODE when that is
        omitted too) for anything unrecognised -- a hand-edited preferences
        file, or a key written by a newer version. Never raises: a bad
        setting must not stop the application starting.'''
        if isinstance(value, cls):
            return value
        for mode in cls:
            if mode.value == value:
                return mode
        return DEFAULT_MODE if default is None else default


# What a user with no `mode` in their preferences file gets. The DM41X is
# the more common device (Mike, 2026-10-09), so it is the default -- note
# that this means an existing DM41L owner's states report the DM41X's XM
# totals until they set the mode themselves.
DEFAULT_MODE = DeviceMode.DM41X
