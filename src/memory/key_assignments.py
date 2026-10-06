'''
KeyAssignments: the Key Assignment Registers region (docs/key_assignments.md
sec 4), starting at KEY_ASSIGNMENTS_RANGE[0] (0xC0) and growing upward two
assignments at a time.

Each register holding user function key assignments starts with a 0xF0
marker byte, followed by up to two 3-byte assignment entries:
    [fn byte 1] [fn byte 2] [key byte]
A built-in single-byte HP-41 function stores its filler byte FIRST (fn
byte 1 == 0x04) and the real function code second; a two-byte
XROM/peripheral function uses both bytes for real data, no filler.
Reverse-engineered from William C. Wickes' "Synthetic Programming on the
HP-41C" (Section 2E, "The Key Assignment Registers") and confirmed
byte-for-byte against real dumps -- see docs/key_assignments.md sec 4.2/4.8
for the full derivation.

The Alarms buffer (alarms.py) starts exactly where this region ends, with
no gap, so every edit here that changes the register count moves it as
well -- see `_encode_entries()`.
'''

from typing import Optional, TYPE_CHECKING

from .registers import Register
from .regions import MemoryRegion
from .constants import KEY_ASSIGNMENTS_RANGE, PRIMARY_DATA_END
from .mnemonics import display_for_key_bytes

if TYPE_CHECKING:
    from .memory import Memory


class KeyAssignments(MemoryRegion):
    '''The Key Assignment Registers (docs/key_assignments.md sec 4.2).'''

    key = "key"
    label = "Key Assignments"

    MARKER = 0xF0

    # The 34 real assignable key positions on the physical keyboard (docs
    # sec 6 item 4's "HP41" grid) as (M, N) pairs. Row 3 has no N=1 -- that
    # position is the physical SHIFT key, which can never be assigned;
    # rows 4-8 only go up to N=4. Validating against this (rather than
    # just "1<=M<=8, 1<=N<=5") matters: an out-of-range pair like (8, 5)
    # produces a *negative* raw bit number in _keyflags_bit()'s formula,
    # which Python's divmod()/bytes indexing would silently wrap around to
    # a real (but wrong) byte instead of raising -- corrupting an
    # unrelated KEYFLAGS bit rather than failing loudly.
    _VALID_KEY_POSITIONS = (
        [(1, n) for n in range(1, 6)]
        + [(2, n) for n in range(1, 6)]
        + [(3, n) for n in range(2, 6)]
        + [(m, n) for m in range(4, 9) for n in range(1, 5)]
    )
    _VALID_KEY_NUMBERS = [10 * m + n for m, n in _VALID_KEY_POSITIONS]

    # For the HP41 series, row 4's ENTER^ key is physically double-width -- on
    # the real keyboard it occupies both the column-1 AND column-2 slots in
    # the key-byte (sec 4.3) / KEYFLAGS (sec 4.5) column numbering, so there
    # is no real key at physical column 2 for row 4 at all. Confirmed against
    # Wickes' Figure 4-2 ("Key Assignment Flag Bits"): that diagram draws a
    # single wide box spanning columns 1-2 in row 4, with no bit assigned to
    # column 2 -- this is the "imaginary 42nd key under ENTER" mentioned in
    # sec 4.5's flag-count note. Wickes' key- NUMBER notation (sec 2) still
    # numbers row 4's three other keys sequentially -- 42, 43, 44, the same as
    # every other row -- but they sit at physical column positions 3, 4, 5 in
    # the formulas below, not 2, 3, 4. Every other row's key-number column
    # digit equals its physical column directly; row 4 is the sole exception.
    # Found 2026-08-18 from the user's real-hardware testing: assignments to
    # key 42 showed up on the calculator as key 41 and didn't work, and
    # real-calculator row-4 assignments came back missing/misplaced when read
    # by this app -- both are exactly what using the wrong (N-1) column offset
    # for row 4 would cause.
    #
    # For the DM41L, the keys are physically laid out very differently but in
    # order to emulate the HP41 series, a key press generates the same key
    # code it would generate on an HP41 rather than its physical position.
    _ROW4_PHYSICAL_COLUMN = {1: 1, 2: 3, 3: 4, 4: 5}

    def __init__(self, memory: "Memory"):
        super().__init__(memory)
        # Exclusive upper bound, cached rather than rescanned on every
        # read -- see `end` and `rescan()` below.
        self._end = KEY_ASSIGNMENTS_RANGE[0]

    # -- Extent ----------------------------------------------------------

    @property
    def start(self) -> int:
        return KEY_ASSIGNMENTS_RANGE[0]

    @property
    def end(self) -> int:
        '''Highest address currently holding a Key Assignment Register, or
        `start - 1` when there are no assignments at all.

        The underlying exclusive bound is cached rather than rescanned on
        every read (unlike R00/.END., which are cheap single-register
        nibble reads, this is a linear scan) and is kept up to date by
        every edit made through this class. A raw `Memory.set_register()`
        into this span -- e.g. from the hex editor -- will NOT update it
        until `rescan()` is called or the dump is reloaded.
        '''
        return self._end - 1

    def rescan(self):
        '''Re-derives this region's extent from the registers themselves.
        Called after a dump is loaded, and available to any caller that
        has written into this span behind the region's back.'''
        self._end = self._scan_end()

    def _scan_end(self) -> int:
        '''
        Scans upward from `start` (0xC0) for as long as each register's
        leading byte is the 0xF0 key-assignment marker, and returns the
        address one past the last such register -- an exclusive upper
        bound, suitable for e.g. `range(0xC0, end)`. Returns `start`
        itself if register 0xC0 doesn't start a key-assignment register at
        all (no assignments made, or no real dump loaded).

        Bounded at PRIMARY_DATA_END as a hard backstop against a corrupt
        dump wandering past this region entirely, rather than trusting
        `.END.`/R00 -- both of those are themselves derived values that
        can be nonsense in a fresh or corrupt Memory, so this scan
        deliberately doesn't depend on either.
        '''
        addr = KEY_ASSIGNMENTS_RANGE[0]
        while addr <= PRIMARY_DATA_END and (
            self._memory.get_register(addr).get_bytes()[0] == self.MARKER
        ):
            addr += 1
        return addr

    # -- Keyboard geometry (docs/key_assignments.md sec 2/4.3/4.5) -------

    @classmethod
    def _key_row_col(cls, key_number: int) -> tuple:
        '''Splits a two-digit key number `MN` (docs sec 2 -- row M, column
        N) into (M, N). Raises ValueError unless `key_number` is one of the
        34 real assignable keyboard positions (see _VALID_KEY_POSITIONS
        above) -- notably rejecting `31` (the physical SHIFT key) and
        anything with M or N outside the real keyboard's layout. `N` here
        is the key-NUMBER column (as printed on the key, e.g. the `2` in
        `42`) -- see _physical_column() for the column actually used by the
        byte/bit formulas, which differs from this for row 4.'''
        m, n = divmod(key_number, 10)
        if (m, n) not in cls._VALID_KEY_POSITIONS:
            raise ValueError(f"Invalid key number: {key_number!r}")
        return m, n

    @classmethod
    def _physical_column(cls, m: int, n: int) -> int:
        '''Maps a key number's (M, N) -- N being the key-NUMBER column,
        e.g. the `2` in key `42` -- to the physical column actually used
        by the key-byte (sec 4.3) and KEYFLAGS bit (sec 4.5) formulas.
        Identical to N for every row except row 4, whose double-width
        ENTER^ key shifts the three keys after it over by one physical
        column -- see _ROW4_PHYSICAL_COLUMN above.'''
        if m == 4:
            return cls._ROW4_PHYSICAL_COLUMN[n]
        return n

    @classmethod
    def key_byte_for(cls, key_number: int, shifted: bool) -> int:
        '''The internal key-byte encoding for `key_number` (docs sec 4.3):
        `16*(N-1) + M` unshifted, `16*(N-1) + (M+8)` shifted, where `N` is
        the *physical* column (see _physical_column()).'''
        m, n = cls._key_row_col(key_number)
        n_phys = cls._physical_column(m, n)
        row = m + 8 if shifted else m
        return 16 * (n_phys - 1) + row

    @classmethod
    def key_number_for_byte(cls, key_byte: int) -> tuple:
        '''Inverts key_byte_for(): given a stored key byte, returns
        (key_number, shifted). Tries every real assignable keyboard
        position (_VALID_KEY_NUMBERS) rather than algebraically inverting
        the formula, since the carry behavior for M=8 rows (sec 4.3) makes
        a closed-form inverse easy to get subtly wrong; this is only ever
        called on the small number of decoded entries in a dump, so the
        brute-force cost is immaterial. Raises ValueError if `key_byte`
        doesn't match any real key (e.g. a corrupt dump, or a hand-crafted
        test fixture targeting a non-assignable position).'''
        for key_number in cls._VALID_KEY_NUMBERS:
            if cls.key_byte_for(key_number, False) == key_byte:
                return key_number, False
            if cls.key_byte_for(key_number, True) == key_byte:
                return key_number, True
        raise ValueError(f"Key byte 0x{key_byte:02x} doesn't decode to a known key")

    @classmethod
    def _keyflags_bit(cls, key_number: int) -> int:
        '''Bit position within the KEYFLAGS bitmap (register R or e) for
        `key_number` (docs sec 4.5): `36 - M - 8*(N-1)`, where `N` is the
        *physical* column (see _physical_column()). The same bit number
        is used in both registers -- which register (R vs. e)
        distinguishes unshifted from shifted, not the bit position.'''
        m, n = cls._key_row_col(key_number)
        n_phys = cls._physical_column(m, n)
        return 36 - m - 8 * (n_phys - 1)

    # -- KEYFLAGS (sec 4.5) ----------------------------------------------
    #
    # The bits themselves live in status registers R and e (see
    # StatusRegisters.get_keyflag_bit/set_keyflag_bit); which bit means
    # which key is this region's business, so the mapping lives here.

    def get_key_flag(self, key_number: int, shifted: bool) -> bool:
        '''Reads the KEYFLAGS existence bit for `key_number` -- True means
        *some* assignment exists for this key/shift-state, in either the
        Key Assignment Registers (sec 4.2) or a global label (sec 4.6);
        it says nothing about which kind. See docs sec 4.5.'''
        return self._memory.status_registers.get_keyflag_bit(
            self._keyflags_bit(key_number), shifted
        )

    def flag_clear_assignments(self) -> list:
        '''`(key_number, shifted)` for every key that has an assignment of
        either kind (a Key Assignment Register entry, or a global label's
        key byte) while its KEYFLAGS bit is clear, in key-number order.

        The OS looks an assignment up only when the flag is set, so such a
        key behaves as unassigned. This is how LKAOFF is stored in a state
        file on a DM41X: it clears the flags of the top two rows of keys and
        leaves the assignments alone (docs/key_assignments.md, "LKAOFF (DM41X)").
        Nothing here changes the flags, and the app never recomputes them
        from the assignments; the one-key set/clear in set_assignment() and
        the global-label paths is all that ever touches them.'''
        assigned = set()
        for entry in self.list_assignments():
            if entry["key_number"] is not None:
                assigned.add((entry["key_number"], entry["shifted"]))
        for label in self._memory.programs.list_global_chain():
            if label.is_named and label.key_assignment:
                try:
                    assigned.add(self.key_number_for_byte(label.key_assignment))
                except ValueError:
                    pass  # not a real key position; nothing to report
        return sorted(
            (key_number, shifted)
            for key_number, shifted in assigned
            if not self.get_key_flag(key_number, shifted)
        )

    def is_lkaoff_like(self) -> bool:
        '''True if any assigned key in the top two rows (keys 11-15 and
        21-25, the ones local-label auto assignment and LKAOFF act on) has
        its KEYFLAGS bit clear -- what a state saved after LKAOFF looks
        like.'''
        return any(
            key_number // 10 in (1, 2)
            for key_number, _shifted in self.flag_clear_assignments()
        )

    def set_key_flag(self, key_number: int, shifted: bool, value: bool):
        '''Sets or clears the KEYFLAGS existence bit for `key_number`
        (docs sec 4.5). Callers writing an actual assignment should use
        set_assignment()/delete_assignment() below instead of calling this
        directly -- those keep the Key Assignment Registers and KEYFLAGS
        in sync; this is the low-level primitive they (and ProgramMemory's
        global-label assignment/deletion) share.'''
        self._memory.status_registers.set_keyflag_bit(
            self._keyflags_bit(key_number), shifted, value
        )

    # -- Entry storage (sec 4.2/4.4) -------------------------------------

    def decode_entries(self) -> list:
        '''Returns every entry currently in the Key Assignment Registers,
        in stored (newest-first, sec 4.4) order, as
        `(fn_byte1, fn_byte2_or_None, key_byte)` tuples -- `fn_byte2` is
        None for a single-byte built-in function entry (the register's
        real filler-first storage, sec 4.2, is normalized away here so
        every other method only deals with "1 byte" vs. "2 bytes").'''
        entries = []
        for addr in range(self.start, self._end):
            raw = self._memory.get_register(addr).get_bytes()
            for offset in (1, 4):
                b0, b1, b2 = raw[offset], raw[offset + 1], raw[offset + 2]
                if b0 == 0 and b1 == 0 and b2 == 0:
                    continue  # register has an odd number of assignments
                if b0 == 0x04:
                    entries.append((b1, None, b2))
                else:
                    entries.append((b0, b1, b2))
        return entries

    def _encode_entries(self, entries: list):
        '''Repacks `entries` (same shape decode_entries() returns)
        canonically into the Key Assignment Registers, starting at `start`
        with no gaps, two entries per register, re-adding the filler byte
        for a single-byte entry (sec 4.2). Clears every register between
        the new end and whatever now comes right after it -- either the old
        end, or the end of a just-relocated Alarms buffer if one is present
        and reaches past the old end -- so a shrinking edit doesn't leave
        stale F0-marked registers behind without also clobbering an Alarms
        buffer that may have just been moved into part of that same span.
        Also updates this region's own extent. See Alarms.relocate() for
        the move itself. Entries are written in list order -- callers
        control LIFO placement (sec 4.4) by ordering `entries` themselves
        before calling this.'''
        alarms = self._memory.alarms
        base = self.start
        old_end = self._end
        new_end = base + (len(entries) + 1) // 2  # ceil(len/2), 2 entries/register

        # Move the Alarms buffer out of the way BEFORE writing a single
        # new Key Assignment register below -- see Alarms.relocate()'s
        # docstring for why the order matters (a growing region would
        # otherwise overwrite it before it could be moved).
        alarms.relocate(old_end, new_end)

        addr = base
        i = 0
        while i < len(entries):
            data = bytearray(7)
            data[0] = self.MARKER
            for slot in range(2):
                if i >= len(entries):
                    break
                fn1, fn2, key_byte = entries[i]
                offset = 1 + slot * 3
                if fn2 is None:
                    data[offset] = 0x04
                    data[offset + 1] = fn1
                else:
                    data[offset] = fn1
                    data[offset + 1] = fn2
                data[offset + 2] = key_byte
                i += 1
            self._memory.set_register(addr, Register(data=bytes(data)))
            addr += 1

        # Anything from new_end up to old_end is stale -- UNLESS the Alarms
        # buffer was just relocated to start at new_end and reaches into (or
        # past) that span, in which case it's real, just-moved Alarms data,
        # not leftover Key Assignments bytes. span_end_at(new_end) reflects
        # the buffer's post-move position; Alarms.end_exclusive can't be used
        # here since it reads back through this region's own extent, which
        # isn't updated to new_end until the line right after this loop.
        clear_from = alarms.span_end_at(new_end)
        for stale in range(clear_from, old_end):
            self._memory.set_register(stale, Register(size=7))
        self._end = new_end

    def repack(self):
        '''Rewrites the region in canonical, gapless form without changing
        which assignments it holds. A no-op for a dump this class has only
        ever edited itself (every edit already leaves it packed); this
        self-heals one loaded from disk with a pre-existing gap. See
        Memory.pack().'''
        self._encode_entries(self.decode_entries())

    # -- Assignments (sec 4.2/4.4/4.7) -----------------------------------

    def set_assignment(self, key_number: int, shifted: bool, function_bytes):
        '''Assigns `key_number` (unshifted or shifted, per `shifted`) to a
        built-in/peripheral function -- `function_bytes` is a single int
        (a one-byte HP-41 function, e.g. 0x40 for `+`; see the sec-5
        caveat in memory/functions.py for the low-code (<0x40) case) or a
        2-item sequence of ints (an XROM/peripheral function's two bytes,
        sec 4.8).

        Any existing entry for the same key/shift-state is replaced (not
        left as a stale duplicate): the old entry is removed and the new
        one inserted at the front, so it lands in register 0xC0 exactly
        as a brand-new assignment would (sec 4.4's LIFO order). Also sets
        the corresponding KEYFLAGS bit (sec 4.5), and clears any global
        label (sec 4.6) currently assigned to the same key -- the real
        lookup order (sec 4.7) would otherwise let this entry silently
        shadow that program's assignment rather than genuinely replacing
        it; see ProgramMemory.set_program_key_assignment() for the same
        precedent in the other direction.
        '''
        key_byte = self.key_byte_for(key_number, shifted)

        if isinstance(function_bytes, int):
            fn1, fn2 = function_bytes, None
        else:
            fn1, fn2 = function_bytes
            if fn2 is None:
                raise ValueError("A 2-byte function's second byte can't be None")

        for b in (fn1,) if fn2 is None else (fn1, fn2):
            if not (0 <= b <= 0xFF):
                raise ValueError(f"Function byte out of range: {b!r}")

        entries = [e for e in self.decode_entries() if e[2] != key_byte]
        entries.insert(0, (fn1, fn2, key_byte))
        self._encode_entries(entries)
        self._memory.programs.clear_assignments_for_key_byte(key_byte)
        self.set_key_flag(key_number, shifted, True)

    def delete_assignment(self, key_number: int, shifted: bool):
        '''Removes any Key Assignment Register entry for `key_number`/
        `shifted` and clears its KEYFLAGS bit. A no-op (still clears the
        flag) if the key currently has no entry there -- e.g. it's a
        global-label assignment (sec 4.6, untouched by this method) or
        simply unassigned.'''
        key_byte = self.key_byte_for(key_number, shifted)
        entries = self.decode_entries()
        filtered = [e for e in entries if e[2] != key_byte]
        if len(filtered) != len(entries):
            self._encode_entries(filtered)
        self.set_key_flag(key_number, shifted, False)

    def get_assignment(self, key_number: int, shifted: bool) -> Optional[dict]:
        '''Looks up the single Key Assignment Register entry (if any) for
        `key_number`/`shifted` -- same dict shape as one entry from
        list_assignments(), or None if that key/shift-state has no entry
        there (unassigned, or assigned via a global label instead, sec
        4.6). Intended for a GUI rendering one keypad cell at a time (docs
        sec 6 item 4), where scanning the full decoded list per cell would
        be wasteful for a whole grid at once -- callers rendering every key
        at once should use list_assignments() instead.'''
        key_byte = self.key_byte_for(key_number, shifted)
        for fn1, fn2, kb in self.decode_entries():
            if kb == key_byte:
                return {
                    "key_number": key_number,
                    "shifted": shifted,
                    "fn_byte1": fn1,
                    "fn_byte2": fn2,
                    "name": display_for_key_bytes(fn1, fn2),
                    "raw_key_byte": kb,
                }
        return None

    def list_assignments(self) -> list:
        '''Returns every built-in/peripheral key assignment currently in
        the Key Assignment Registers as a list of dicts:
        `{"key_number": int, "shifted": bool, "fn_byte1": int,
        "fn_byte2": int|None, "name": str}` -- `name` is the function's
        HP-41 display name (memory/mnemonics.py), or a "0xNN"-style fallback
        string if the byte(s) don't match any known function. Order
        matches the buffer's own newest-first order (sec 4.4); global
        label assignments (sec 4.6) are NOT included here -- see
        ProgramMemory.list_global_chain()'s `key_assignment` field for
        those, per docs/key_assignments.md sec 6 item 4's shared-data-model
        note.'''
        results = []
        for fn1, fn2, key_byte in self.decode_entries():
            try:
                key_number, shifted = self.key_number_for_byte(key_byte)
            except ValueError:
                # Doesn't decode to any of the 34 real assignable keyboard
                # positions -- seen so far only in a hand-crafted test
                # fixture (keyassigntest.dm41), not a real device capture.
                # Surfaced rather than silently dropped or crashing the
                # whole listing, with enough raw detail (the key byte
                # itself) that a caller/GUI can flag it distinctly.
                key_number, shifted = None, None
            results.append({
                "key_number": key_number,
                "shifted": shifted,
                "fn_byte1": fn1,
                "fn_byte2": fn2,
                "name": display_for_key_bytes(fn1, fn2),
                "raw_key_byte": key_byte,
            })
        return results
