'''
MemoryRegion and RegionSpan -- two different answers to "what named span
of registers is this."

A `MemoryRegion` is a live view onto one named span of a `Memory`, with
whatever behavior is appropriate for that kind of memory attached to it
(`StatusRegisters` has named register accessors and the flag bits,
`KeyAssignments` decodes/encodes assignment entries, `ProgramMemory` owns
the global chain, and so on -- one module each, listed in the package
`__init__.py`). `Memory` hands them out on demand (`Memory.region()`, or
the named properties `Memory.status_registers` / `.key_assignments` /
`.alarms` / `.programs` / `.data_memory` / `.extended_memory`).

**A region's boundaries are computed, not stored.** `start` and `end` are
properties that ask the `Memory` (or re-derive from its registers) every
time they're read, so a region instance is safe to hold across an edit
that moves it: adding a key assignment moves `KeyAssignments.end`, which
moves `Alarms` wholesale, which moves where free program space begins,
independently of `.END.`/`R00` also moving as programs are added and
removed.

The one thing a region does NOT do is describe itself to a caller who
just wants boundaries as data. `RegionSpan` is for that: a small
immutable `(key, label, start, end)` descriptor with no reference back to
a `Memory` and no read/write behavior, produced by `MemoryRegion.span()`
and returned in a fresh list by `Memory.regions()`. `hex_view_tab.py` and
`overview_tab.py` both classify addresses and count registers from those
snapshots rather than hand-rolling the arithmetic.

Extended memory (`XMFile`/`ExtendedMemory`) lives in `xm_file.py` instead
of getting its own module here, since it has a whole file-system-like
structure built on top of its region.
'''

from typing import Dict, Optional, Sequence, TYPE_CHECKING

from .registers import Register
from .constants import PRIMARY_DATA_END, VOID_RANGE

if TYPE_CHECKING:
    from .memory import Memory


class RegionSpan:
    '''
    A plain, immutable descriptor for one region's boundaries at one
    moment -- what `MemoryRegion.span()` and `Memory.regions()` hand back.
    Deliberately NOT a `MemoryRegion`: it carries no reference to a
    `Memory` and no read/write behavior, just the boundaries and label a
    caller asked "what's here" for.

    `start`/`end` are both inclusive, matching `MemoryRegion`'s own
    convention (`start <= addr <= end`).
    '''

    __slots__ = ("key", "label", "start", "end")

    def __init__(self, key: str, label: str, start: int, end: int):
        self.key = key
        self.label = label
        self.start = start
        self.end = end

    @property
    def count(self) -> int:
        '''Number of registers this span covers -- 0 (not negative) for an
        empty span, e.g. "key" in a state with no key assignments at all.'''
        return max(0, self.end - self.start + 1)

    def __contains__(self, addr: int) -> bool:
        return self.start <= addr <= self.end

    def __eq__(self, other):
        if not isinstance(other, RegionSpan):
            return NotImplemented
        return (
            self.key == other.key
            and self.label == other.label
            and self.start == other.start
            and self.end == other.end
        )

    def __repr__(self) -> str:
        return f"RegionSpan({self.key!r}, 0x{self.start:03X}-0x{self.end:03X})"


class MemoryRegion:
    '''
    Base class for a named, contiguous span of registers within a
    `Memory`.

    Subclasses represent the different kinds of memory and add whatever
    access is appropriate for that kind. They define their extent by
    overriding the `start` and `end` properties, which must re-derive the
    boundaries on every read (see this module's docstring) -- a subclass
    whose extent genuinely never moves can instead pass a fixed
    `address_range` to `__init__` and inherit both.

    `start` and `end` are both INCLUSIVE. A region can legitimately be
    empty, in which case `end == start - 1`, `count == 0`, iteration
    yields nothing and `addr in region` is never true -- so callers
    classifying addresses don't need to special-case an empty region.
    '''

    key: str = "region"
    label: str = "Region"

    def __init__(self, memory: "Memory", address_range: Optional[Sequence[int]] = None):
        self._memory = memory
        self._fixed_range = None if address_range is None else tuple(address_range)

    # -- Extent ----------------------------------------------------------

    @property
    def start(self) -> int:
        '''Lowest address in this region (inclusive).'''
        if self._fixed_range is None:
            raise NotImplementedError(
                f"{type(self).__name__} must override start (or be "
                "constructed with a fixed address_range)"
            )
        return self._fixed_range[0]

    @property
    def end(self) -> int:
        '''Highest address in this region (inclusive), or `start - 1` when
        the region is currently empty.'''
        if self._fixed_range is None:
            raise NotImplementedError(
                f"{type(self).__name__} must override end (or be "
                "constructed with a fixed address_range)"
            )
        return self._fixed_range[1]

    @property
    def end_exclusive(self) -> int:
        '''One past `end` -- the half-open upper bound, handy for
        `range()` and for regions that stack directly on top of each
        other (`Alarms` starts exactly at `KeyAssignments.end_exclusive`).'''
        return self.end + 1

    @property
    def address_range(self) -> tuple:
        '''`(start, end)`, both inclusive -- a live read, not a stored
        value.'''
        return (self.start, self.end)

    @property
    def count(self) -> int:
        '''Number of registers currently in this region; 0 (never
        negative) when it's empty.'''
        return max(0, self.end - self.start + 1)

    @property
    def is_empty(self) -> bool:
        return self.count == 0

    def span(self) -> RegionSpan:
        '''An immutable snapshot of this region's current boundaries --
        what `Memory.regions()` collects. Safe to keep; unlike the region
        itself it will NOT follow subsequent edits.'''
        return RegionSpan(self.key, self.label, self.start, self.end)

    def __contains__(self, addr: int) -> bool:
        return self.start <= addr <= self.end

    def __iter__(self):
        return iter(range(self.start, self.end + 1))

    def __len__(self) -> int:
        return self.count

    def __repr__(self) -> str:
        if self.is_empty:
            return f"{type(self).__name__}(empty at 0x{self.start:03X})"
        return f"{type(self).__name__}(0x{self.start:03X}-0x{self.end:03X})"

    # -- Register access -------------------------------------------------

    def _check_addr(self, addr: int):
        if addr not in self:
            raise ValueError(
                f"Address 0x{addr:03X} is outside {type(self).__name__} "
                f"(0x{self.start:03X}-0x{self.end:03X})"
            )

    def get_register(self, addr: int) -> Register:
        '''Reads a register at an absolute address within this region.'''
        self._check_addr(addr)
        return self._memory.get_register(addr)

    def set_register(self, addr: int, register: Register):
        '''Writes a register at an absolute address within this region.'''
        self._check_addr(addr)
        self._memory.set_register(addr, register)

    def registers(self) -> Dict[int, Register]:
        '''All registers in this region, keyed by absolute address.'''
        return {addr: self.get_register(addr) for addr in self}

    def clear(self):
        '''Zeroes every register currently in this region. Does not move
        any boundary -- a region whose extent is derived from its own
        contents (`KeyAssignments`, `Alarms`) will report itself empty
        afterward as a consequence, not because this said so.'''
        for addr in self:
            self._memory.set_register(addr, Register(size=7))


class VoidRegion(MemoryRegion):
    '''The addressable-but-nonexistent hole between the status registers
    and the first extended-memory region. Nothing lives here; it exists as
    a region only so that every address in the displayed range belongs to
    exactly one of them.'''

    key = "nonexistent"
    label = "Inaccessible"

    def __init__(self, memory: "Memory"):
        super().__init__(memory, VOID_RANGE)


class FreeSpace(MemoryRegion):
    '''
    Whatever is left over between the top of the Alarms buffer and the
    bottom of program memory -- genuinely unallocated registers, available
    for a program import or for more key assignments/alarms to grow into.

    Purely derived: it owns no data of its own and has no behavior beyond
    reporting how much room there currently is. When the state has no sane
    R00/`.END.` partition to bound it -- a corrupt or never-loaded one,
    see `Memory.has_program_partition()` -- it runs all the way up to
    `PRIMARY_DATA_END`, since there is no meaningful program/data split to
    stop at.
    '''

    key = "unused"
    label = "Unused / Free"

    @property
    def start(self) -> int:
        return self._memory.alarms.end_exclusive

    @property
    def end(self) -> int:
        if not self._memory.has_program_partition():
            return PRIMARY_DATA_END
        return self._memory.status_registers.DotEnd() - 1
