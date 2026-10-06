'''
DataMemory: the main (primary) data registers -- R00 upward to
PRIMARY_DATA_END.

The region's extent is set entirely by the R00 pointer in status register
c: moving R00 down turns program-memory registers into data registers and
moving it up does the reverse, without any register contents changing
(see StatusRegisters.set_R00()). Both boundaries are therefore read live,
so `count`, `number_for()` and friends always describe the partition as it
stands right now.

Registers here are addressed two ways, and it matters which one a caller
means: R00, R01, R02 ... are *register numbers* relative to the partition
boundary (what a user types into an HP-41 `STO`/`RCL`), while the hex
addresses in the state are absolute. `address_for()`/`number_for()` convert
between them.
'''

from typing import TYPE_CHECKING

from .registers import Register
from .regions import MemoryRegion
from .constants import PRIMARY_DATA_END

if TYPE_CHECKING:
    from .memory import Memory


class DataMemory(MemoryRegion):
    '''The primary data registers, R00 up to PRIMARY_DATA_END.'''

    key = "data"
    label = "Data Memory"

    def __init__(self, memory: "Memory"):
        super().__init__(memory)

    # -- Extent ----------------------------------------------------------

    @property
    def start(self) -> int:
        '''
        Absolute address of R00. Reports an empty region when the state has no
        sane R00/`.END.` partition -- a corrupt or never-loaded one -- rather
        than treating a meaningless R00 as a real boundary. See
        `Memory.has_program_partition()`.
        '''
        if not self._memory.has_program_partition():
            return PRIMARY_DATA_END + 1
        return self._memory.status_registers.R00()

    @property
    def end(self) -> int:
        if not self._memory.has_program_partition():
            return PRIMARY_DATA_END
        return PRIMARY_DATA_END

    # -- Numbered access -------------------------------------------------

    def address_for(self, number: int) -> int:
        '''Absolute address of data register `number` (0 = R00). Raises
        ValueError if that register doesn't exist in the current
        partition.'''
        count = self.count
        if number < 0 or number >= count:
            if count == 0:
                raise ValueError(
                    f"Data register {number} doesn't exist -- this state has "
                    "no data register partition"
                )
            raise ValueError(
                f"Data register {number} is outside the current partition "
                f"(R00-R{count - 1})"
            )
        return self.start + number

    def number_for(self, addr: int) -> int:
        '''Data register number (0 = R00) for an absolute address in this
        region. Raises ValueError for an address outside it.'''
        self._check_addr(addr)
        return addr - self.start

    def get(self, number: int) -> Register:
        '''Reads data register `number` (0 = R00).'''
        return self._memory.get_register(self.address_for(number))

    def set(self, number: int, register: Register):
        '''Writes data register `number` (0 = R00).'''
        self._memory.set_register(self.address_for(number), register)

    def numbers(self) -> list:
        '''Every data register's BCD value, R00 first.'''
        return [self._memory.get_register(addr).get_bcd_number() for addr in self]
