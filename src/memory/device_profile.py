'''
DeviceProfile: the per-model facts a Memory needs that differ between DM41
models.

Every DM41-series calculator shares one HP-41CX memory model and one dump
text format (a DM41L `.dm41` dump and a DM41X `.d41` state file are the same
format, with the same `DM41` header line), so a file cannot say which model
wrote it. The caller says: Memory.from_string(text, profile=DM41X).

A profile holds the list of extended-memory regions and the set of XROM
functions the model has built in. Each region is (lo, hi): `lo` is the
region's reserved pointer/link register and the usable registers are
lo+1 .. hi inclusive -- see docs/extended_memory.md. An XROM is identified by
its (byte1, byte2) pair, as in memory/functions.py.
'''

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
    to a DM41L) asks the profile.'''

    name: str
    xm_regions: Tuple[Tuple[int, int], ...]
    builtin_xroms: FrozenSet[Tuple[int, int]]

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
)

# The DM41X adds a second Extended Memory module at 0x301-0x3EF -- confirmed
# against tests/data/dm41x_manyfiles.dm41, a real dump whose files span all
# three regions. Its XROMs are the CX set plus its own additions (X<I>Y, TRNG
# and the DM41X module), which is all of functions.XROM_FUNCTIONS. The
# DM41XN is treated identically (docs/dm41x_explorer_plan.md).
DM41X = DeviceProfile(
    "DM41X",
    ((0x40, 0xBF), (0x201, 0x2EF), (0x301, 0x3EF)),
    frozenset(XROM_FUNCTIONS),
)

PROFILES = {profile.name: profile for profile in (DM41L, DM41X)}
