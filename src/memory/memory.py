'''
Memory: a representation of a DM41-series (DM41L, DM41X, ...) memory state
and tools for manipulating it. See the memory package's __init__.py docstring
for the on-disk state format overview.

Memory itself owns only what is genuinely state-wide: parsing and
serialization, raw register storage, the partition sanity check every region
agrees on, and the set of regions. Everything that is specific to one kind of
memory lives on that kind's own region class (regions.py's module docstring
has the map) and is reached through `Memory.region()` or one of the named
region properties below.
'''

import re
from typing import Dict, Optional, Union
from pathlib import Path

from .registers import Register
from .device_profile import DeviceProfile, DM41L
from .constants import (
    MIN_SANE_R00,
    zero_register,
    REGISTER_SIZE_BYTES,
    SPECIAL_REGISTER_SIZE_OVERRIDES,
)
from .regions import RegionSpan, VoidRegion, FreeSpace
from .status_registers import StatusRegisters
from .key_assignments import KeyAssignments
from .alarms import Alarms
from .program_memory import ProgramMemory
from .data_memory import DataMemory
from .xm_file import ExtendedMemory


class Memory:
    '''A complete DM41-series memory state (a DM41L `.dm41` state or a DM41X
    `.d41` state file -- the text format is identical).'''

    # Pattern to capture a capital letter followed by any hex string of 1
    # or more chars
    SPECIAL_PATTERN = re.compile(r"([A-Z]:\s*)([0-9a-fA-F]+)")

    def __init__(self, header: str = "DM41", profile: DeviceProfile = DM41L):
        self._header = header
        # Which DM41 model's memory map this state uses. The state text
        # doesn't say, so the caller chooses -- see device_profile.py.
        self._profile = profile
        self._core_memory: Dict[int, Register] = {}  # Keyed by register index
        self._special_registers: Dict[str, Register] = (
            {}
        )  # Keyed by label order preservation

        # Default values for some status registers. Taken from a memory
        # state in the "Memory Lost" state. Registers not initialized
        # are all zeroes.
        self._core_memory[8] = Register.from_hex("4b000000000000")
        self._core_memory[12] = Register.from_hex("1000000000019c")
        self._core_memory[13] = Register.from_hex("1a70016919c19b")
        self._core_memory[14] = Register.from_hex("0000002c048000")

        # Default values for special registers. Taken from a
        # memory state in the "Memory Lost" state.
        self._special_registers["A"] = Register.from_hex("00000000c00020")
        self._special_registers["B"] = Register.from_hex("f000002c0480fd")
        self._special_registers["C"] = Register.from_hex("f000002c0480fd")
        self._special_registers["S"] = Register.from_hex("00001100000000")
        self._special_registers["M"] = Register.from_hex("00011cd5ff73cb")
        self._special_registers["N"] = Register.from_hex("000000000000c0")
        self._special_registers["G"] = Register.from_hex("00")

        # The regions. Each is a live view -- its boundaries are computed
        # from this Memory every time they're read (see regions.py), so
        # these instances are built once here and stay correct for the
        # life of the state no matter how much moves around inside it.
        self._regions = {}
        for region in (
            StatusRegisters(self),
            VoidRegion(self),
            KeyAssignments(self),
            Alarms(self),
            FreeSpace(self),
            ProgramMemory(self),
            DataMemory(self),
            ExtendedMemory(self),
        ):
            self._regions[region.key] = region

        self._modified = False

    def __eq__(self, other):
        if not isinstance(other, Memory):
            return False

        if self._header != other._header or self._profile != other._profile:
            return False
        if self._special_registers != other._special_registers:
            return False

        # Compare *effective* register values via get_register() rather than
        # the raw _core_memory dicts: memory states are sparse, but
        # get_register() already treats an address with no matching entry as
        # an implicit zero register, so two Memory objects that agree on every
        # address's effective value are equal even if one of them happens to
        # have an explicit zero-valued entry where the other has none at all.

        addrs = set(self._core_memory) | set(other._core_memory)
        return all(self.get_register(a) == other.get_register(a) for a in addrs)

    # -- Loading and saving ----------------------------------------------

    @classmethod
    def from_string(cls, buffer: str, profile: DeviceProfile = DM41L) -> "Memory":
        '''
        Creates a new Memory object from a string that contains a DM41
        memory state. `profile` says which model's memory map to use (the
        text format is identical for every model).
        '''

        memory = cls(profile=profile)

        lines = buffer.strip().splitlines()
        if not lines:
            return memory

        # A state omits all-zero rows, so a register it does not mention is
        # zero. Drop the "Memory Lost" defaults a new Memory starts with, or
        # they would show through where the file has a zero (lander.dm41 and
        # targ.dm41 have no row 08).
        memory._core_memory.clear()
        # Likewise S is optional in a state (targ.dm41 and lander.dm41 have
        # none), and to_string() leaves it out when there is none.
        memory._special_registers.pop("S", None)

        header = lines[0]
        if header != "DM41":
            raise ValueError(f"Invalid header: {header}")

        phase = 1
        next_base = 0
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue

            token = line.split()
            if ":" not in token[0]:
                if phase != 1:
                    raise ValueError(
                        "Memory registers cannot follow special registers: "
                        f"{line}"
                    )
                # We're expecting a well-formed memory state.
                # First token should be a hex base-addr.
                try:
                    base = int(token[0], 16)
                except ValueError as e:
                    raise ValueError(
                        f"{token[0]} is not a hexadecimal"
                        f" number: {line}"
                    ) from e
                if base < next_base:
                    raise ValueError(
                        "Memory state is not well-formed:"
                        f"{base} < {next_base}. {line}"
                    )

                i = 0
                for hex_str in token[1:]:
                    register = Register.from_hex(hex_str)
                    if register.size != REGISTER_SIZE_BYTES:
                        raise ValueError(
                            f"Register at 0x{base + i:x} is "
                            f"{register.size} bytes, expected "
                            f"{REGISTER_SIZE_BYTES}: {line}"
                        )
                    memory._core_memory[base + i] = register
                    i += 1
                if i > 4:
                    raise ValueError(f"Line too long: {line}")

                next_base = base + i
            else:
                phase = 2

                # This section can have a varying number of registers per
                # line, but they should be in pairs.
                for i in range(0, len(token), 2):
                    if ":" != token[i][1]:
                        raise ValueError(f"Malformed line {line}")
                    label = token[i][0]
                    register = Register.from_hex(token[i + 1])
                    expected_size = SPECIAL_REGISTER_SIZE_OVERRIDES.get(
                        label, REGISTER_SIZE_BYTES
                    )
                    if register.size != expected_size:
                        raise ValueError(
                            f"Special register {label} is "
                            f"{register.size} bytes, expected "
                            f"{expected_size}: {line}"
                        )
                    memory._special_registers[label] = register

        # The only region whose extent isn't derivable on demand -- see
        # KeyAssignments.end.
        memory.key_assignments.rescan()
        return memory

    @classmethod
    def from_file(
        cls, path: Union[str, Path], profile: DeviceProfile = DM41L
    ) -> "Memory":
        '''Load a memory state from disk. A convenience wrapper: the pure,
        filesystem-free API is from_string(), which is what portable code
        (and the web decoder's port) uses.'''
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_string(f.read(), profile=profile)

    def to_string(self) -> str:
        ''' Create a string representation of the memory state. '''

        # Section I: "DM41"
        lines = [self._header]

        # Section II: Core Memory. For compactness, a row that consists of all
        # zeroes is omitted from the final string. This is purely a
        # save-format compaction: get_register() already treats a missing
        # address as an implicit zero register, so a page skipped here reads
        # back identically to one that was never touched at all.

        # Remember, _core_memory is a sparse structure. Addresses that are omitted are
        # assumed to be zero. sorted_indices is strictly a list of addresses
        # that were either present in the original representation this state
        # was created from or else were set during operation.
        sorted_indices = sorted(self._core_memory.keys())
        if sorted_indices:
            # State files group core memory in rows of 4 registers.
            # Generate a list of rows for the final output.
            rows = sorted({idx - (idx % 4) for idx in sorted_indices})
            for base_idx in rows:
                registers = [
                    self.get_register(base_idx + offset) for offset in range(4)
                ]
                # If all 4 registers are zero, skip this row.
                if all(register == zero_register() for register in registers):
                    continue
                row = [f"{base_idx:02x}"]
                row.extend(register.get_hex() for register in registers)
                lines.append("  ".join(row))

        # Section III: Special Registers
        # These appear to be representations of the HP41's CPU registers. Note
        # that right now the DM41_Explorer never alters their values.
        # TODO: Consider just making these constants based on their values in
        # a state taken in memory lost state.
        if self._special_registers:
            # These need to be emitted in the same order they first appeared.
            A = self._special_registers["A"].get_hex()
            B = self._special_registers["B"].get_hex()
            C = self._special_registers["C"].get_hex()
            lines.append(f"A: {A} B: {B} C: {C}")
            # S may not be present.
            S = self._special_registers.get("S", None)
            if S is not None:
                lines.append(f"S: {S.get_hex()}")
            M = self._special_registers["M"].get_hex()
            N = self._special_registers["N"].get_hex()
            G = self._special_registers["G"].get_hex()
            lines.append(f"M: {M} N: {N} G: {G}")

        return "\n".join(lines) + "\n"

    def to_file(self, path: Union[str, Path]):
        '''Write the state to disk (convenience wrapper over to_string())
        and clear the modified flag.'''
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_string())
        self._modified=False

    # -- Raw register access ---------------------------------------------

    def get_register(self, key: Union[int, str]) -> Optional[Register]:
        if isinstance(key, int):
            # Check if the address exists in our sparse core memory mapping
            reg = self._core_memory.get(key)
            if reg is None:
                # Return a default 56-bit (7 byte) register of zeroes if
                # the address is missing
                return Register(7)
            return reg
        return self._special_registers.get(key)

    def set_register(self, key: Union[int, str], register: Register):
        if isinstance(key, int):
            self._core_memory[key] = register
        else:
            self._special_registers[key] = register

    def region(self, key: str):
        '''Return the MemoryRegion registered under `key` Raises KeyError for
        anything else if the region name does not exist.. '''
        return self._regions[key]

    @property
    def profile(self) -> DeviceProfile:
        '''This state's model profile (memory map).'''
        return self._profile

    @property
    def modified(self) -> bool:
        return self._modified

    def is_modified(self):
        ''' Memory modifications can happen many different ways, so we can't
        just detect such changes automatically - checking every register
        change would be a bit expensive - instead we have whatever code
        changed the memory to set the modified flag; this permits the code to
        set the flag just once when the change is complete. '''
        self._modified = True

    @property
    def status_registers(self) -> StatusRegisters:
        '''The 16 named CPU/system registers (0x00-0x0F), plus the flags
        and the R00/.END./SIGMA-REG pointers packed inside them.'''
        return self._regions["status"]

    @property
    def key_assignments(self) -> KeyAssignments:
        '''The Key Assignment Registers (docs/key_assignments.md sec 4).'''
        return self._regions["key"]

    @property
    def alarms(self) -> Alarms:
        '''The alarms buffer (docs/alarms.md sec 3/4).'''
        return self._regions["alarms"]

    @property
    def free_space(self) -> FreeSpace:
        '''Unallocated registers between the alarms buffer and `.END.`.'''
        return self._regions["unused"]

    @property
    def programs(self) -> ProgramMemory:
        '''User program storage and the global chain (docs/program.md).'''
        return self._regions["program"]

    @property
    def data_memory(self) -> DataMemory:
        '''The primary data registers, R00 up to PRIMARY_DATA_END.'''
        return self._regions["data"]

    @property
    def extended_memory(self) -> ExtendedMemory:
        '''Extended memory -- file storage (see xm_file.py).'''
        return self._regions["xm"]

    def has_program_partition(self) -> bool:
        '''True when R00/`.END.` describe a program/data partition worth
        trusting. False for a corrupt or never-loaded state, where the
        pointers decode to values that mean nothing -- ProgramMemory and
        DataMemory both report themselves empty in that case, and
        FreeSpace runs the whole way up to PRIMARY_DATA_END, rather than
        any of them guessing at a split.

        Note that even a default, empty Memory object
        starts with these set; but it is always possible that we read a
        corrupt state file that omitted the status registers. About the only
        other way to create this situation is for the user to set the value
        of R00 to an illegal value.
        '''
        try:
            r00 = self.status_registers.R00()
            dot_end = self.status_registers.DotEnd()
        except Exception:  # pylint: disable=broad-except
            return False
        return r00 >= MIN_SANE_R00 and dot_end <= r00

    def regions(self) -> list:
        '''
        Every named region of the full addressable display range (0x000
        through `profile.display_end` -- 0x2EF for a DM41L, 0x3EF for a
        DM41X), as a flat, address-ordered list of RegionSpan(key, label,
        start, end) -- both inclusive.

        The "xm" key appears once per XM region (two on a DM41L, three on a
        DM41X), since those spans aren't contiguous with each other. On a
        DM41X the 16 addresses 0x2F0-0x2FF between the second and third
        regions belong to no span.
        '''
        xm = self.extended_memory
        xm0_lo, xm0_hi = self._profile.xm_regions[0]

        spans = [
            self.status_registers.span(),
            self.region("nonexistent").span(),
            RegionSpan(xm.key, xm.label, xm0_lo, xm0_hi),
            self.key_assignments.span(),
            self.alarms.span(),
            self.free_space.span(),
        ]

        if self.has_program_partition():
            spans.append(self.programs.span())
            spans.append(self.data_memory.span())

        # Each later XM region's span starts one below its pointer register
        # (e.g. 0x200 for the DM41L's second region) -- the register that
        # would hold a pointer to a region the model doesn't have.
        for lo, hi in self._profile.xm_regions[1:]:
            spans.append(RegionSpan(xm.key, xm.label, lo - 1, hi))
        return spans

    def region_for(self, addr: int) -> Optional[RegionSpan]:
        '''The RegionSpan containing `addr` (from regions()), or None if
        `addr` falls outside every span this method returns (shouldn't
        happen for any address in [0x000, profile.display_end] other than
        the DM41X's 0x2F0-0x2FF gap, but this is a lookup, not a guarantee).'''
        for span in self.regions():
            if addr in span:
                return span
        return None

    # -- Whole-state operations -------------------------------------------

    def pack(self) -> int:
        '''
        Explicitly repacks user memory -- This is one of the few operations
        that genuinely spans regions, which is why it lives here rather than
        on any one of them.

        Key Assignments (sec 4) and Alarms (sec 3/4, docs/alarms.md)
        already usually perfectly packed as a side effect of every
        KeyAssignments edit -- but in the actual calculator packing is a
        manual operation. This means deleting key assignments can create gaps
        in a state file on disk if the user did not manually pack memory before
        saving the state file.

        Program memory (docs/program.md sec 5) gets more than a repack --
        see `ProgramMemory.repack()`, which rebuilds the global chain
        from a forward opcode scan rather than trusting the existing
        backlinks, deletes NULLs and clears cached jump distances the way
        a real PACK does, and closes the newest program with a real END
        plus an empty `.END.` (which can cost one register -- the return
        value is then -1).

        Meant to be run explicitly after loading a state file or before an
        Import to guarantee the maximum possible free space is available for
        the program to be imported, and to make sure every label actually
        present is visible for assignment -- this project deliberately doesn't
        run it automatically on every edit, so what's in memory always matches
        exactly what the user last loaded or changed until they ask for this.

        Returns the number of additional registers now free as a result
        (the change in the free-space region's size) -- 0 if nothing
        needed packing, -1 if the newest program was closed by `.END.`
        directly and now needs a real END as well.

        Raises `DM41MemoryError` if the program-memory scan cannot safely
        determine program memory's real content -- see
        `ProgramMemory._forward_scan_programs()` for exactly when that
        happens. Nothing is changed at all if that happens: the scan runs,
        and can raise, before anything about program memory is touched.
        '''
        before_free = self.status_registers.DotEnd() - self.alarms.end_exclusive

        self.key_assignments.repack()
        self.programs.repack()

        after_free = self.status_registers.DotEnd() - self.alarms.end_exclusive
        return after_free - before_free
