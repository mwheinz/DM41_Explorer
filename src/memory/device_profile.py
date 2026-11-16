"""
DeviceProfile: the per-model facts a Memory needs that differ between DM41
models.

Every DM41-series calculator shares one HP-41CX memory model and one state
text format (a DM41L `.dm41` state and a DM41X `.d41` state file are the same
format, with the same `DM41` header line), so a file cannot say which model
wrote it. The caller says: Memory.from_string(text, profile=DM41X). The
PX-41CX, an HP-41CX clone, has the same memory model and its own variant of
the format, with a `PX41` header (#82).

A profile holds the list of extended-memory regions, the set of XROM
functions the model has built in, and whether the model can be reached over
a serial console. Each region is (lo, hi): `lo` is the region's reserved
pointer/link register and the usable registers are lo+1 .. hi inclusive --
see docs/extended_memory.md. An XROM is identified by its (byte1, byte2)
pair, as in memory/functions.py.

DeviceMode, at the end of this module, is the user-facing choice of which
profile the application works in (GitHub issue #43). The
mode is the single source of truth for the active profile: not the file's
extension, and not what is plugged in.
"""

import enum
from dataclasses import dataclass
from functools import cached_property
from typing import FrozenSet, Optional, Tuple

from .xrom_library import builtin_xrom_names


@dataclass(frozen=True)
class DeviceProfile:
    """An immutable description of one DM41 model's memory map and the XROM
    functions built into it.

    `builtin_xroms` is only about what the *calculator* has: the functions
    of the model's built-in XROM library (memory/xroms/, GitHub issue #51),
    section headers included -- they are in its ROM, and a key can hold
    one (XROM 25,00, issue #58).
    The name registry in memory/mnemonics.py always knows every built-in
    library's names, whatever profile is in use, so a program using
    a DM41X function still compiles and decompiles. Something that needs to
    know whether a given model can run it (the check before sending a state
    to a DM41L) asks the profile.

    `supports_serial` is whether this model has a serial console at all, and
    so whether the application's Connect/Get/Send actions mean anything in
    its mode. It is a property of the model, deliberately not a test against
    `name`: the DM41XN has both a FAT disk and a serial interface, so adding
    its profile later must not require revisiting every gating decision.
    Defaults to False, so a new profile has to opt in.

    `loads_modules` is whether the model can plug in other ROM modules
    (.mod files), so whether an XROM outside `builtin_xroms` may still
    run on it. The DM41X can; the DM41L has only its built-in ROMs. Also
    defaults to False.

    `state_header` is the header line of the state files the model writes,
    "DM41" or "PX41" (#82): the one a new, empty state for it gets, so it is
    saved in the model's own layout."""

    name: str
    xm_regions: Tuple[Tuple[int, int], ...]
    builtin_xroms: FrozenSet[Tuple[int, int]]
    supports_serial: bool = False
    loads_modules: bool = False
    state_header: str = "DM41"

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

    @cached_property
    def builtin_modules(self) -> FrozenSet[int]:
        """The XROM module numbers of the model's built-in ROMs, such as 25
        (Extended Functions) and 26 (Time). A plugged-in module cannot use
        one of these numbers, so a function numbered in one of them that
        is not in `builtin_xroms` is not on the model at all (#82). The
        module number is decoded as in mnemonics.xrom_numbers()."""
        return frozenset(
            ((byte1 & 0x07) << 2) | (byte2 >> 6) for byte1, byte2 in self.builtin_xroms
        )

    @property
    def xm_address_range(self) -> Tuple[int, int]:
        """Outermost extent XM can occupy: the first region's pointer
        register through the last region's ceiling. The gaps between
        regions are inside this range but are not XM."""
        return (self.xm_regions[0][0], self.xm_regions[-1][1])

    @property
    def display_end(self) -> int:
        """Highest address of the model's addressable memory map (the last
        XM region's ceiling) -- the end of the Hex View's range."""
        return self.xm_regions[-1][1]

    @property
    def xm_raw_registers(self) -> int:
        """Structural XM capacity in registers, excluding each region's
        reserved pointer register."""
        return sum(hi - lo for lo, hi in self.xm_regions)


# The extended-memory regions the calculator can address. Region 0 emulates
# an Extended Functions module and the later regions emulate Extended Memory
# modules. (The HP-41CX supports one Extended Functions module and up to 2
# Extended Memory modules.)
#
# The DM41L has region 0 plus ONE Extended Memory module. The register just
# below its second region's pointer (0x200) is RAM the module provides, but
# it is undocumented and cannot be used, so it is not part of the region.
#
# Its XROMs are only those of the original HP-41CX modules (Extended
# Functions/Memory and Time), its built-in library hp41cx.json; it cannot
# load any other module.
DM41L = DeviceProfile(
    "DM41L",
    ((0x40, 0xBF), (0x201, 0x2EF)),
    frozenset(builtin_xrom_names("DM41L", headers=True)),
    supports_serial=True,
)

# The DM41X adds a second Extended Memory module at 0x301-0x3EF (0x300 below
# it is inaccessible, like 0x200 and 0x2F0-0x2FF) -- confirmed
# against tests/data/dm41x_manyfiles.dm41, a real state whose files span all
# three regions. Its XROMs are the CX set plus its own additions (X<I>Y, TRNG
# and the DM41X module): its built-in library, dm41x-cx.json. The
# DM41XN is treated identically (the DM41X plan, archived on #78), except that
# its newer USB interface does offer a serial console; when its own profile
# is added it will set supports_serial=True. The DM41X is strictly
# file-based, so its mode offers no serial actions.
DM41X = DeviceProfile(
    "DM41X",
    ((0x40, 0xBF), (0x201, 0x2EF), (0x301, 0x3EF)),
    frozenset(builtin_xrom_names("DM41X", headers=True)),
    supports_serial=False,
    loads_modules=True,
)

# The PX-41CX is an HP-41CX clone (#82). Its dumps hold extended memory in
# the same three regions as the DM41X (tests/data/px41cx_SIZE300.px41 has
# rows from 0x220 to 0x2ec and at 0x300; px41cx_ASN.px41 and
# px41cx_CLOCK.px41 have data at 0x301-0x3ef). It pre-loads only the
# original HP-41CX's ROMs, Extended Functions (XROM 25) and Time (XROM 26),
# so its built-in library is the DM41L's hp41cx.json, without the DM41X's
# additions; other ROMs are loaded by the user, as on the DM41X. Its
# USB serial link speaks COMDUMP, not the DM41L's console, so its mode offers
# no serial actions for now.
PX41CX = DeviceProfile(
    "PX-41CX",
    ((0x40, 0xBF), (0x201, 0x2EF), (0x301, 0x3EF)),
    frozenset(builtin_xrom_names("PX-41CX", headers=True)),
    supports_serial=False,
    loads_modules=True,
    state_header="PX41",
)

PROFILES = {profile.name: profile for profile in (DM41L, DM41X, PX41CX)}


class DeviceMode(enum.Enum):
    """Which calculator the application is working as, chosen by the user
    and persisted in the preferences file (config.py's "mode").

    One mode per profile, and the mode picks the profile for everything:
    the memory map a state is opened with, the XM capacity the Overview
    reports, the keyboard layout the Key Assignments tab draws, the
    functions its edit dialog offers, and whether the serial actions are
    available at all.

    A real enumerated type rather than bare strings, because this is a
    closed set of named modes; `value` is the stored/display name, which is
    also the profile's own `name`."""

    DM41L = "DM41L"
    DM41X = "DM41X"
    PX41CX = "PX-41CX"

    @property
    def profile(self) -> DeviceProfile:
        """The DeviceProfile this mode works in."""
        return PROFILES[self.value]

    @property
    def supports_serial(self) -> bool:
        """Whether the serial actions mean anything in this mode. Asks the
        profile; never tests the mode's name."""
        return self.profile.supports_serial

    @classmethod
    def that_wrote(cls, header: str) -> Optional["DeviceMode"]:
        """The mode whose calculator writes states with `header`, if only
        one does (#82). A PX41 header means a PX-41CX wrote the file; a
        DM41 one could be a DM41L's or a DM41X's, so it names no mode, and
        the file opens in the current one. Asks each profile's
        `state_header`; never tests a mode's name."""
        modes = [mode for mode in cls if mode.profile.state_header == header]
        return modes[0] if len(modes) == 1 else None

    @classmethod
    def from_value(cls, value, default: "DeviceMode" = None) -> "DeviceMode":
        """The mode named `value`, or `default` (DEFAULT_MODE when that is
        omitted too) for anything unrecognised -- a hand-edited preferences
        file, or a key written by a newer version. Never raises: a bad
        setting must not stop the application starting."""
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except (ValueError, TypeError):
            return DEFAULT_MODE if default is None else default


# What a user with no `mode` in their preferences file gets. The DM41X is
# the more common device, so it is the default -- note
# that this means an existing DM41L owner's states report the DM41X's XM
# totals until they set the mode themselves.
DEFAULT_MODE = DeviceMode.DM41X
