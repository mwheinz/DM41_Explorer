'''
StatusRegisters: the 16 named CPU/system registers at 0x00-0x0F (T
through e), and the fields packed inside two of them -- register c's
partition pointers (SigmaReg/R00/.END.) and register d's 56 status flags.

This is the one region whose extent genuinely never moves, but it is
where the pointers that define where every *other* region starts and ends
are stored, so `Memory` reads its own partition boundaries back out
through here.
'''

from typing import Optional, TYPE_CHECKING

from .registers import Register, AlphaRegister, DM41MemoryError
from .regions import MemoryRegion
from .constants import STATUS_REGISTERS_RANGE, STATUS_REGISTER_LABELS

if TYPE_CHECKING:
    from .memory import Memory


class StatusRegisters(MemoryRegion):
    '''The 16 named CPU/system registers, T through e.'''

    key = "status"
    label = "Status Registers"

    # Register c (0x0D): SREG / printer-use / cold-start / R00 / .END.
    #
    #   Register c contains multiple important fields. Read memory.md for a
    #   detailed explanation of what each field is for.
    #   nibbles[0:3]   SREG  (SIGMA-REG) absolute address
    #   nibbles[3:5]   printer use (undecoded)
    #   nibbles[5:8]   cold-start signature -- always 0x169 in real states,
    #                  usable as a sanity check
    #   nibbles[8:11]  R00   absolute address of data register 00
    #   nibbles[11:14] .END. absolute address of the end of program memory
    REG_C_ADDR = 0x0D
    REG_D_ADDR = 0x0E  # Flags
    FLAG_COUNT = 56

    def __init__(self, memory: "Memory"):
        super().__init__(memory, STATUS_REGISTERS_RANGE)

    # -- Named registers -------------------------------------------------

    def T(self) -> Register:
        return self.get_register(0)

    def Z(self) -> Register:
        return self.get_register(1)

    def Y(self) -> Register:
        return self.get_register(2)

    def X(self) -> Register:
        return self.get_register(3)

    def LastX(self) -> Register:
        return self.get_register(4)

    def M(self) -> Register:
        return self.get_register(5)

    def N(self) -> Register:
        return self.get_register(6)

    def O(self) -> Register:
        return self.get_register(7)

    def P(self) -> Register:
        return self.get_register(8)

    def Q(self) -> Register:
        return self.get_register(9)

    def R(self) -> Register:
        '''Status register 0x0A. Also called "F" or "Append" by some
        sources; "R" matches hp41uc and program-text postfix 0x7A (see
        docs/memory.md sec 3.1).'''
        return self.get_register(10)

    def a(self) -> Register:
        return self.get_register(11)

    def b(self) -> Register:
        return self.get_register(12)

    def c(self) -> Register:
        return self.get_register(13)

    def d(self) -> Register:
        return self.get_register(14)

    def e(self) -> Register:
        return self.get_register(15)

    def Flags(self) -> Register:
        return self.get_register(14)

    @property
    def alpha(self) -> AlphaRegister:
        '''The ALPHA register, assembled from the tail of P and all of
        O/N/M. Read live on every access rather than captured once at
        construction, so it can't go stale after an edit to any of those
        four registers.'''
        data = (
            self.get_register(8).get_bytes()[4:7]
            + self.get_register(7).get_bytes()
            + self.get_register(6).get_bytes()
            + self.get_register(5).get_bytes()
        )
        return AlphaRegister(data=data)

    def label_for(self, addr: int) -> Optional[str]:
        '''The system-register name (e.g. 'X') for an address in this
        region, with its decoded value.'''
        if 0x00 <= addr <= 0x04:
            return (
                f"{STATUS_REGISTER_LABELS[addr]}: "
                f"{self.get_register(addr).get_bcd_number()}"
            )
        if 0x05 <= addr <= 0x08:
            return (
                f"{STATUS_REGISTER_LABELS[addr]}: "
                f"{self.get_register(addr).get_ascii()}"
            )
        if 0x09 <= addr <= 0x0F:
            return (
                f"{STATUS_REGISTER_LABELS[addr]}: "
                f"{self.get_register(addr).get_hex()}"
            )
        return None

    # -- Register c: the partition pointers ------------------------------

    @staticmethod
    def _nibbles_to_int(nibbles) -> int:
        '''Combine a list of nibbles into an integer.'''
        value = 0
        for n in nibbles:
            value = (value << 4) | n
        return value

    def _reg_c_nibbles(self) -> list:
        return self.get_register(self.REG_C_ADDR).get_nibbles()

    def _write_reg_c_address(self, first_nibble: int, addr: int, what: str):
        '''Rewrites the 3-nibble address field starting at `first_nibble`
        of register c. Shared by set_R00()/set_DotEnd(), which differ only
        in which field they touch and what they call it in the error.'''
        if not (0 <= addr <= 0xFFF):
            raise ValueError(
                f"{what} must fit in a 3-nibble address (0-0xFFF), got 0x{addr:x}"
            )
        nibbles = self._reg_c_nibbles()
        nibbles[first_nibble] = (addr >> 8) & 0xF
        nibbles[first_nibble + 1] = (addr >> 4) & 0xF
        nibbles[first_nibble + 2] = addr & 0xF
        new_bytes = bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, 14, 2))
        self.set_register(self.REG_C_ADDR, Register(data=new_bytes))

    def SigmaReg(self) -> int:
        '''Absolute address of SIGMA-REG, decoded from register c.'''
        return self._nibbles_to_int(self._reg_c_nibbles()[0:3])

    def DotEnd(self) -> int:
        '''Absolute address of the end of loaded program memory (".END.").'''
        return self._nibbles_to_int(self._reg_c_nibbles()[11:14])

    def R00(self) -> int:
        '''
        Absolute address of data register 00 -- the boundary between
        program memory (below R00) and main data memory (R00 up to
        PRIMARY_DATA_END, inclusive).
        '''
        return self._nibbles_to_int(self._reg_c_nibbles()[8:11])

    def set_R00(self, addr: int):
        '''
        Directly rewrites the R00 pointer in register c.

        This only moves the partition marker -- it does NOT move, clear, or
        resize any actual register contents on either side of the new
        boundary, so moving it can expose stale program bytes as "data" (or
        hide real data registers behind the program-memory boundary).

        This is useful for experimenting with synthetic programming -
        careful use of R00 movement can be used to create special "byte
        jumper" and "byte loader" instructions that are the foundation of
        synthetic programming.
        '''
        self._write_reg_c_address(8, addr, "R00")

    def set_DotEnd(self, addr: int):
        '''
        Directly rewrites the `.END.` pointer in register c, used by
        `ProgramMemory` to move the permanent `.END.` sentinel to the top of
        whatever program memory it just wrote. Like `set_R00()`, this only
        moves the pointer itself; it doesn't touch, clear, or validate any
        register contents on either side of it.
        '''
        self._write_reg_c_address(11, addr, ".END.")

    # -- Register d: the 56 user/system flags ----------------------------
    # See docs/flags.md for each flag's name.

    def get_flag(self, n: int) -> bool:
        if not (0 <= n < self.FLAG_COUNT):
            raise ValueError(f"Flag number must be 0-{self.FLAG_COUNT - 1}, got {n}")
        d = self.get_register(self.REG_D_ADDR)
        if d.size != 7:
            raise DM41MemoryError("Flags register is an invalid size.")

        byte_index, bit_in_byte = divmod(n, 8)
        return bool((d.get_bytes()[byte_index] >> (7 - bit_in_byte)) & 1)

    def set_flag(self, n: int, value: bool):
        if not (0 <= n < self.FLAG_COUNT):
            raise ValueError(f"Flag number must be 0-{self.FLAG_COUNT - 1}, got {n}")
        d = self.get_register(self.REG_D_ADDR)
        if d.size != 7:
           raise DM41MemoryError("Flags register is an invalid size.")

        data = bytearray(d.get_bytes())
        byte_index, bit_in_byte = divmod(n, 8)
        mask = 1 << (7 - bit_in_byte)
        if value:
            data[byte_index] |= mask
        else:
            data[byte_index] &= ~mask & 0xFF
        self.set_register(self.REG_D_ADDR, Register(data=bytes(data)))

    def get_all_flags(self) -> list:
        '''Returns a list of FLAG_COUNT bools, flag 0 first.'''
        d = self.get_register(self.REG_D_ADDR)
        if d.size != 7:
           raise DM41MemoryError("Flags register is an invalid size.")

        bits = int.from_bytes(d.get_bytes(), "big")
        binary = format(bits, f"0{self.FLAG_COUNT}b")
        return [c == "1" for c in binary]

    # -- KEYFLAGS bitmaps (registers R and e) ----------------------------
    #
    # docs/key_assignments.md sec 4.5: register R holds the unshifted-key
    # existence bits, register e the shifted-key ones. The meaning of the
    # individual bits is defined in key_assignments.py; these two primitives
    # just read and write a numbered bit of whichever of the two registers is
    # named.

    KEYFLAGS_UNSHIFTED_ADDR = 0x0A  # R
    KEYFLAGS_SHIFTED_ADDR = 0x0F  # e

    def get_keyflag_bit(self, bit: int, shifted: bool) -> bool:
        addr = self.KEYFLAGS_SHIFTED_ADDR if shifted else self.KEYFLAGS_UNSHIFTED_ADDR
        reg = self.get_register(addr)
        byte_index, bit_in_byte = divmod(bit, 8)
        return bool((reg.get_bytes()[byte_index] >> (7 - bit_in_byte)) & 1)

    def set_keyflag_bit(self, bit: int, shifted: bool, value: bool):
        addr = self.KEYFLAGS_SHIFTED_ADDR if shifted else self.KEYFLAGS_UNSHIFTED_ADDR
        reg = self.get_register(addr)
        data = bytearray(reg.get_bytes())
        byte_index, bit_in_byte = divmod(bit, 8)
        mask = 1 << (7 - bit_in_byte)
        if value:
            data[byte_index] |= mask
        else:
            data[byte_index] &= ~mask & 0xFF
        self.set_register(addr, Register(data=bytes(data)))
