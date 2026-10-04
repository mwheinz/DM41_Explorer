r'''
Alarms: the alarms buffer (docs/alarms.md sec 3/4/5 -- also HP41CX Owner's
Manual vol 2, Section 16, "Alarm Functions").

The buffer sits immediately above the Key Assignment Registers, with no
gap, and below whatever free/program-memory space follows: one header
register (0xAA marker + a total register count that includes the header
and the closing delimiter), followed by the alarm entries in ascending,
time-sorted order, then a single 0xF0-marked delimiter register.

Each entry is `[time register] + [repeat register, only if repeating] +
[message register(s), 0-4]`. The time register's low nibble of byte 6 is
the message-register count; the high nibble is a best-effort "past due"
marker (see `Alarm.past_due`'s docstring -- confirmed against exactly one
real sample so far, treat with appropriate skepticism). Whether a repeat
register is present can't be told from the time register alone -- see
`Alarms._looks_like_repeat_register()`.

Alarm TYPE (message / control / conditional) is not a separate field --
it's encoded by however many FOCAL "up arrow" characters (byte 0x5E,
docs/trigraphs.md's `\^|`) lead the message field itself, confirmed
2026-09-01 against real ALMCAT output and the Owner's Manual vol 2 sec 16
alarm-type flowchart:
    no leading 0x5E    -- a plain message alarm; the field is the message
    one leading 0x5E    -- a conditional alarm; the rest of the field is
                           the global label (or catalog-2 function) name
    two leading 0x5E's  -- a control alarm; same, two arrows
A control alarm with nothing after the two arrows means "resume the
current program line" rather than jumping to a label.
'''

import datetime
import math
from typing import List, Optional, Tuple, TYPE_CHECKING

from .registers import Register, DM41MemoryError
from .regions import MemoryRegion
from .constants import PRIMARY_DATA_END
from .trigraphs import encode_trigraphs, decode_trigraphs

if TYPE_CHECKING:
    from .memory import Memory

# HP41/DM41L time fields (both the absolute trigger time and the repeat
# interval) are BCD hundredths-of-a-second since this epoch (docs/
# alarms.md sec 4, cross-validated against the Timer chip's own ALARM
# REGISTER A/B format in "A programmers handbook v.2.07.pdf").
_EPOCH = datetime.datetime(1900, 1, 1)

# How large a decoded "repeat interval" can plausibly be before treating
# the register that produced it as message text instead (docs/alarms.md
# sec 4/9 -- this disambiguation is a documented, irreducible heuristic,
# not something provable from the format alone). Kept deliberately
# generous: a real (if very unusual) repeat register has been confirmed
# 2026-09-01 at ~416.7 days -- the whole point of that sample, per the
# user, appears to have been to stress-test exactly this heuristic -- so
# this bound favors under- rather than over-rejecting a genuine repeat
# register, at the cost of very rarely misreading a digit/early-alphabet-
# heavy message as a bogus one instead (see
# Alarms._looks_like_repeat_register()).
_MAX_PLAUSIBLE_REPEAT = datetime.timedelta(days=3650)

# The "up arrow" FOCAL character (docs/trigraphs.md's `\^|`) that marks a
# control/conditional alarm's label field -- see module docstring.
_UP_ARROW = 0x5E


class Alarm:
    '''
    One decoded entry from the Alarms buffer -- what `Alarms.list_alarms()`
    returns and `Alarms.add_alarm()` accepts the pieces of.

    `text` is always plain trigraph-escaped ASCII (docs/trigraphs.md,
    `encode_trigraphs()`/`decode_trigraphs()`) -- for a message alarm it's
    the message; for a control/conditional alarm it's the global label (or
    catalog-2 function) name, with the leading up-arrow marker(s) already
    stripped off (see module docstring). It can be empty: a message alarm
    with an empty message shows the current time/date instead (confirmed,
    docs/alarms.md sec 5's zero-length-message case), and a control alarm
    with an empty label resumes the current program line (Owner's Manual
    vol 2 sec 16) rather than naming one. `add_alarm()` rejects a bare
    (non-trigraph-escaped) lowercase letter above 'e' -- not a real FOCAL
    character, see `decode_trigraphs(restrict_literals=True)`.
    '''

    TYPE_MESSAGE = "message"
    TYPE_CONTROL = "control"
    TYPE_CONDITIONAL = "conditional"

    _TYPE_LABELS = {
        TYPE_MESSAGE: "Message",
        TYPE_CONTROL: "Control",
        TYPE_CONDITIONAL: "Conditional",
    }

    def __init__(
        self,
        *,
        start_addr: int,
        num_registers: int,
        trigger_time: datetime.datetime,
        repeat_interval: Optional[datetime.timedelta],
        alarm_type: str,
        text: str,
        past_due: bool,
    ):
        self.start_addr = start_addr
        self.num_registers = num_registers
        self.trigger_time = trigger_time
        self.repeat_interval = repeat_interval
        self.alarm_type = alarm_type
        self.text = text
        self.past_due = past_due

    @property
    def type_label(self) -> str:
        return self._TYPE_LABELS[self.alarm_type]

    @property
    def is_repeating(self) -> bool:
        return self.repeat_interval is not None

    def __repr__(self) -> str:
        return (
            f"Alarm(0x{self.start_addr:03X}, {self.alarm_type}, "
            f"{self.trigger_time!r}, text={self.text!r})"
        )

    def __eq__(self, other):
        if not isinstance(other, Alarm):
            return NotImplemented
        return (
            self.start_addr == other.start_addr
            and self.num_registers == other.num_registers
            and self.trigger_time == other.trigger_time
            and self.repeat_interval == other.repeat_interval
            and self.alarm_type == other.alarm_type
            and self.text == other.text
            and self.past_due == other.past_due
        )


class Alarms(MemoryRegion):
    '''
    A variable memory region that begins the register after the last key
    assignment and extends for the number of registers defined in the first
    alarms register. If no alarms exist, this region will be 0-length.
    '''

    key = "alarms"
    label = "Alarms"

    HEADER_MARKER = 0xAA
    DELIMITER_MARKER = 0xF0

    def __init__(self, memory: "Memory"):
        super().__init__(memory)

    @property
    def start(self) -> int:
        '''Exactly where the Key Assignment Registers stop -- the two
        regions are always back-to-back with no gap (docs/alarms.md sec 3).
        Read live, so a key-assignment edit that grows or shrinks that
        region moves this one with it.'''
        return self._memory.key_assignments.end_exclusive

    @property
    def end(self) -> int:
        '''Highest address in the buffer (the closing 0xF0 delimiter), or
        `start - 1` when there is no buffer here at all.'''
        return self.span_end_at(self.start) - 1

    def span_end_at(self, start: int) -> int:
        '''Calculates the end of the alarm buffer+1.
        returns `start` itself if there is no real buffer there.

        This takes the start address as an argument and then walks the alarms
        and calculates a new end value to allow the caller to relocate the
        alarms buffer when new key assignments are inserted.
        '''
        header = self._memory.get_register(start).get_bytes()
        if header[0] != self.HEADER_MARKER:
            return start
        count = header[1]
        end = start + count
        if count <= 0 or end - 1 > PRIMARY_DATA_END:
            return start
        return end

    def relocate(self, old_start: int, new_start: int):

        '''
        Moves the buffer (if any) from old_start to new_start.
        `new_start`, with no gap -- called from
        KeyAssignments._encode_entries() with that region's boundary before
        and after an edit, since this buffer always starts exactly at that
        boundary. Called BEFORE any new Key Assignment register is written, so
        a growing region can't clobber the header/entries currently sitting
        right where it's about to write.

        No-op if the two boundaries are equal (the edit didn't change how
        many registers Key Assignments occupies) or if there's no real
        buffer at `old_start` (same check span_end_at() makes).

        Copies the whole buffer -- header, entries, and delimiter -- as
        one block, in whichever direction avoids overwriting a source
        register before it's been read.

        Registers freed by the buffer moving up (Key Assignments growing) are
        left alone -- the caller is about to overwrite that entire span with
        real Key Assignment register data anyway. Registers freed by the
        buffer moving down (Key Assignments shrinking) are explicitly zeroed,
        since nothing else is about to write there. (Strictly speaking, this
        isn't necessary, but it helps avoid confusion if the user looks at the
        raw memory dump.)
        '''

        delta = new_start - old_start
        if delta == 0:
            # Nothing to do.
            return

        memory = self._memory
        header = memory.get_register(old_start).get_bytes()
        if header[0] != self.HEADER_MARKER:
            # no Alarms buffer here -- nothing to move
            return

        count = header[1]
        old_end = old_start + count
        if count <= 0 or old_end - 1 > PRIMARY_DATA_END:
            raise DM41MemoryError("Alarm memory is corrupt.")

        if new_start + count - 1 > PRIMARY_DATA_END:
            # Nowhere to put it -- on real hardware this would be an
            # out-of-memory condition this tool doesn't model; the
            # safest thing to do here is decline to move the buffer
            # rather than truncate or wrap it into an invalid address.
            raise DM41MemoryError("Insufficient memory.")

        indices = range(count - 1, -1, -1) if delta > 0 else range(count)
        for i in indices:
            memory.set_register(new_start + i, memory.get_register(old_start + i))

        if delta < 0:
            new_end = new_start + count
            for addr in range(new_end, old_end):
                memory.set_register(addr, Register(size=7))

    # -- BCD time/duration codec (docs/alarms.md sec 4) -------------------

    @staticmethod
    def _bcd_nibbles(data: bytes) -> List[int]:
        nibbles = []
        for b in data:
            nibbles.append(b >> 4)
            nibbles.append(b & 0xF)
        return nibbles

    @classmethod
    def _decode_bcd_centiseconds(cls, reg_bytes: bytes) -> int:
        nibbles = cls._bcd_nibbles(reg_bytes[:6])
        if not all(0 <= n <= 9 for n in nibbles):
            raise DM41MemoryError(
                "Not a valid BCD time/duration register: "
                + reg_bytes.hex()
            )
        return int("".join(str(n) for n in nibbles))

    @classmethod
    def _encode_bcd_centiseconds(cls, centiseconds: int) -> bytes:
        digits = f"{centiseconds:012d}"
        if len(digits) > 12 or centiseconds < 0:
            raise ValueError(
                f"{centiseconds} centiseconds doesn't fit a 12-digit BCD field."
            )
        nibbles = [int(c) for c in digits]
        data = bytearray(6)
        for i in range(6):
            data[i] = (nibbles[2 * i] << 4) | nibbles[2 * i + 1]
        return bytes(data)

    @classmethod
    def _decode_time(cls, reg_bytes: bytes) -> Tuple[datetime.datetime, bool]:
        '''Decodes a time register into `(trigger_time, repeats)`.

        CORRECTED 2026-09-04 (docs/alarms.md sec 12 update pending): only
        the very last BCD digit (the hundredths-of-a-second-units place)
        is the deterministic repeats marker -- `0` for one-time, `1` for
        repeating, confirmed 2026-09-02 against 16/16 real alarm entries,
        plus 4 more real past-due samples since. The digit *before* it
        (tenths of a second) is genuine time precision, not part of the
        flag -- every sample above happened to have a `0` there (a
        keypad-set alarm only ever has whole-second precision to begin
        with), which is why the original 2026-09-02 pass treated the
        whole last two digits as the flag (`cs % 100 != 0`). That was
        falsified 2026-09-04 by `src/tests/data/tenthsofasecond.dm41`: a
        real one-time alarm deliberately set to tenth-of-a-second
        precision (confirmed against the real calculator's own `ALMCAT`,
        which displays the `.9` and reads the alarm as non-repeating) --
        the old check misread its nonzero tenths digit as the repeats
        flag and raised `DM41MemoryError` looking for a repeat register
        that doesn't exist. `trigger_time` now keeps the tenths digit;
        only the flag digit itself is zeroed out of it, since that digit
        is a marker `_encode_time()` writes, not real elapsed time.'''
        cs = cls._decode_bcd_centiseconds(reg_bytes)
        repeats = (cs % 10) == 1
        cs_time = cs - (cs % 10)  # drop only the flag digit, keep tenths
        trigger_time = _EPOCH + datetime.timedelta(seconds=cs_time / 100.0)
        return trigger_time, repeats

    @classmethod
    def _encode_time(cls, when: datetime.datetime, *, repeats: bool) -> bytes:
        '''Encodes `when` (tenths-of-a-second precision preserved -- see
        `_decode_time()`'s 2026-09-04 correction) plus the repeats-flag
        digit in the last BCD position -- `repeats` must match whether a
        repeat register will actually follow this time register, or real
        hardware will misread the buffer exactly the way it did
        2026-09-02 (docs/alarms.md sec 12): a repeat register present but
        the flag says no, so the repeat register's raw bytes get read as
        the start of the message instead, and every subsequent alarm in
        the buffer is misaligned by one register from there on.'''
        delta = when - _EPOCH
        if delta.total_seconds() < 0:
            raise ValueError("Alarm trigger time can't be before 1900-01-01.")
        cs_time = round(delta.total_seconds() * 100)
        cs = cs_time - (cs_time % 10) + (1 if repeats else 0)
        return cls._encode_bcd_centiseconds(cs)

    @classmethod
    def _decode_duration(cls, reg_bytes: bytes) -> datetime.timedelta:
        cs = cls._decode_bcd_centiseconds(reg_bytes)
        return datetime.timedelta(seconds=cs / 100.0)

    @classmethod
    def _encode_duration(cls, interval: datetime.timedelta) -> bytes:
        if interval.total_seconds() < 0:
            raise ValueError("Repeat interval can't be negative.")
        cs = round(interval.total_seconds() * 100)
        return cls._encode_bcd_centiseconds(cs)

    @classmethod
    def _looks_like_repeat_register(cls, reg_bytes: bytes) -> bool:
        '''SUPERSEDED 2026-09-02 as the primary "does a repeat register
        follow" signal -- `_decode_time()`'s repeats-flag digit is the
        real, deterministic marker (docs/alarms.md sec 12); this heuristic
        was the best available guess before that was found, and is kept
        only as a sanity check (see `_decode_one()`): if the flag says a
        repeat register follows but the candidate register doesn't even
        look plausible by this check, that's a real sign of a genuinely
        corrupt buffer, worth surfacing rather than silently trusting.
        Original docstring, for that narrower use: byte 6 == 0x00,
        all-BCD digits in bytes 0-5, and a plausible magnitude (see
        _MAX_PLAUSIBLE_REPEAT).'''
        if len(reg_bytes) < 7 or reg_bytes[6] != 0x00:
            return False
        nibbles = cls._bcd_nibbles(reg_bytes[:6])
        if not all(0 <= n <= 9 for n in nibbles):
            return False
        cs = int("".join(str(n) for n in nibbles))
        return datetime.timedelta(seconds=cs / 100.0) <= _MAX_PLAUSIBLE_REPEAT

    # -- Alarm type / text codec (module docstring) -----------------------

    @classmethod
    def _classify(cls, raw: bytes):
        '''Splits a decoded message field's raw bytes into
        `(alarm_type, label_or_message_bytes)` per the up-arrow convention
        in the module docstring.'''
        if raw[:2] == bytes([_UP_ARROW, _UP_ARROW]):
            return Alarm.TYPE_CONTROL, raw[2:]
        if raw[:1] == bytes([_UP_ARROW]):
            return Alarm.TYPE_CONDITIONAL, raw[1:]
        return Alarm.TYPE_MESSAGE, raw

    @staticmethod
    def _prefix_for_type(alarm_type: str) -> bytes:
        if alarm_type == Alarm.TYPE_CONTROL:
            return bytes([_UP_ARROW, _UP_ARROW])
        if alarm_type == Alarm.TYPE_CONDITIONAL:
            return bytes([_UP_ARROW])
        if alarm_type == Alarm.TYPE_MESSAGE:
            return b""
        raise ValueError(f"Unknown alarm type: {alarm_type!r}")

    # -- Decoding (docs/alarms.md sec 3/4/5) ------------------------------

    def _decode_one(self, addr: int, limit_addr: int):
        '''Decodes one entry starting at `addr` (a time register).
        `limit_addr` is the delimiter's address -- decoding never reads at
        or past it. Returns `(Alarm, next_addr)`.'''
        entry_start = addr
        time_bytes = self._memory.get_register(addr).get_bytes()
        trigger_time, repeats = self._decode_time(time_bytes)
        msg_count = time_bytes[6] & 0x0F
        # High nibble: best-effort "past due" marker -- see Alarm.past_due.
        past_due = (time_bytes[6] & 0xF0) != 0
        addr += 1

        repeat_interval = None
        if repeats:
            if addr >= limit_addr:
                raise DM41MemoryError(
                    f"Alarm at 0x{entry_start:03X} claims to repeat "
                    "(its time register's repeats-flag digit is set) but "
                    "there's no register left before the delimiter for "
                    "the repeat interval -- the buffer looks corrupt."
                )
            candidate = self._memory.get_register(addr).get_bytes()
            if not self._looks_like_repeat_register(candidate):
                raise DM41MemoryError(
                    f"Alarm at 0x{entry_start:03X} claims to repeat, but "
                    f"the register right after its time register (0x"
                    f"{addr:03X}) doesn't look like a valid repeat "
                    "interval -- the buffer looks corrupt."
                )
            repeat_interval = self._decode_duration(candidate)
            addr += 1

        raw = bytearray()
        for i in range(msg_count):
            raw += self._memory.get_register(addr + i).get_bytes()
        addr += msg_count

        # Padding (if any) is leading NULs in the lowest-address message
        # register only (docs/alarms.md sec 4) -- stripping leading NULs
        # off the whole concatenation recovers exactly the real text.
        raw = bytes(raw).lstrip(b"\x00")
        alarm_type, label_bytes = self._classify(raw)

        alarm = Alarm(
            start_addr=entry_start,
            num_registers=addr - entry_start,
            trigger_time=trigger_time,
            repeat_interval=repeat_interval,
            alarm_type=alarm_type,
            text=encode_trigraphs(label_bytes),
            past_due=past_due,
        )
        return alarm, addr

    def list_alarms(self) -> List[Alarm]:
        '''Every alarm currently in the buffer, in stored (trigger-time-
        sorted, docs/alarms.md sec 5) order. Empty list if there's no
        buffer at all.'''
        alarms = []
        addr = self.start + 1
        limit = self.end  # the delimiter's address
        while addr < limit:
            alarm, addr = self._decode_one(addr, limit)
            alarms.append(alarm)
        return alarms

    def get_alarm(self, start_addr: int) -> Optional[Alarm]:
        '''The single alarm whose time register is at `start_addr`, or
        None -- `start_addr` is an Alarm's own stable identity (its
        position can move as other alarms are added/removed), suited to a
        GUI re-fetching "the currently selected alarm" fresh rather than
        trusting a cached object across an edit.'''
        return next(
            (a for a in self.list_alarms() if a.start_addr == start_addr), None
        )

    # -- Encoding one entry (docs/alarms.md sec 3/4, module docstring) ----

    @classmethod
    def _build_entry_registers(
        cls,
        *,
        trigger_time: datetime.datetime,
        repeat_interval: Optional[datetime.timedelta],
        alarm_type: str,
        text: str,
        past_due: bool,
    ) -> List[Register]:
        prefix = cls._prefix_for_type(alarm_type)
        # restrict_literals=True: a bare lowercase letter above 'e' isn't
        # a real FOCAL character (it's reassigned to an unrelated symbol,
        # not lowercase f-z) -- typing one produced a confirmed garbage
        # display on real hardware 2026-09-02 (docs/alarms.md sec 11).
        # Trigraphs are still fully supported for a genuinely-wanted byte
        # value, including building a control/conditional alarm's label
        # out of special FOCAL characters.
        body = decode_trigraphs(text, restrict_literals=True) if text else b""
        field = prefix + body
        noun = "Label" if alarm_type != Alarm.TYPE_MESSAGE else "Message"
        if len(field) > 24:
            raise ValueError(
                f"{noun} is too long: {len(body)} character(s), "
                f"{24 - len(prefix)} max for a {alarm_type} alarm."
            )
        msg_regs = math.ceil(len(field) / 7) if field else 0

        time_bytes = bytearray(
            cls._encode_time(trigger_time, repeats=repeat_interval is not None) + b"\x00"
        )
        time_bytes[6] = (0xF0 if past_due else 0x00) | (msg_regs & 0x0F)
        registers = [Register(data=bytes(time_bytes))]

        if repeat_interval is not None:
            repeat_bytes = cls._encode_duration(repeat_interval) + b"\x00"
            registers.append(Register(data=repeat_bytes))

        if msg_regs:
            padded = field.rjust(msg_regs * 7, b"\x00")
            for i in range(msg_regs):
                registers.append(Register(data=padded[i * 7 : (i + 1) * 7]))

        return registers

    def _room_ceiling(self) -> int:
        '''Highest address the buffer may grow into -- bounded by
        `.END.` when the dump has a real program partition (matching
        FreeSpace's own reasoning), falling back to PRIMARY_DATA_END
        otherwise. A tighter, more correct check than relocate()'s own
        PRIMARY_DATA_END-only backstop above, usable here because this is
        new code rather than a pre-existing check being preserved as-is.'''
        return self._memory.free_space.end

    def _insert_registers(self, insert_addr: int, entry_registers: List[Register]):
        '''Splices `entry_registers` into the buffer starting at
        `insert_addr`, creating the buffer fresh if none exists yet.
        Shared by `add_alarm()`.'''
        n = len(entry_registers)
        ceiling = self._room_ceiling()

        if self.is_empty:
            total = n + 2  # header + entries + delimiter
            if self.start + total - 1 > ceiling:
                raise DM41MemoryError(
                    "Insufficient memory: no room for a new alarm."
                )
            self._memory.set_register(
                self.start, Register(data=bytes([self.HEADER_MARKER, total, 0, 0, 0, 0, 0]))
            )
            addr = self.start + 1
            for reg in entry_registers:
                self._memory.set_register(addr, reg)
                addr += 1
            self._memory.set_register(
                addr, Register(data=bytes([self.DELIMITER_MARKER, 0, 0, 0, 0, 0, 0]))
            )
            return

        old_header = self._memory.get_register(self.start).get_bytes()
        old_count = old_header[1]
        old_delim_addr = self.end
        if old_delim_addr + n > ceiling:
            raise DM41MemoryError(
                "Insufficient memory: no room for a new alarm."
            )

        tail_len = old_delim_addr - insert_addr + 1
        for i in range(tail_len - 1, -1, -1):
            src = insert_addr + i
            self._memory.set_register(src + n, self._memory.get_register(src))

        addr = insert_addr
        for reg in entry_registers:
            self._memory.set_register(addr, reg)
            addr += 1

        new_count = old_count + n
        if new_count > 0xFF:
            raise DM41MemoryError("Alarm buffer is too large to represent.")
        new_header = Register(
            data=bytes([self.HEADER_MARKER, new_count]) + bytes(old_header[2:7])
        )
        self._memory.set_register(self.start, new_header)

    def add_alarm(
        self,
        *,
        trigger_time: datetime.datetime,
        alarm_type: str = Alarm.TYPE_MESSAGE,
        text: str = "",
        repeat_interval: Optional[datetime.timedelta] = None,
        past_due: Optional[bool] = None,
    ) -> Alarm:
        '''Adds a new alarm, inserted wherever `trigger_time` belongs to
        keep the buffer in sorted order (docs/alarms.md sec 5) -- after any
        existing alarm(s) at the same trigger time. `text` is trigraph-
        escaped display text (see Alarm's docstring); for a control/
        conditional alarm it's the global label name (may be empty, see
        Alarm's docstring), for a message alarm the message itself (also
        may be empty).

        `past_due` defaults to comparing `trigger_time` against the host
        clock -- matching the Owner's Manual's own documented behavior
        ("any alarm initially set to a past time" becomes past due, vol 2
        sec 16) -- but the byte-level marker this sets is itself only a
        heuristic (one confirmed sample so far); pass an explicit True/
        False to override.

        Raises `ValueError` for an out-of-range text length, and
        `DM41MemoryError` if there isn't room to grow the buffer.'''
        if past_due is None:
            past_due = trigger_time < datetime.datetime.now()

        entry_registers = self._build_entry_registers(
            trigger_time=trigger_time,
            repeat_interval=repeat_interval,
            alarm_type=alarm_type,
            text=text,
            past_due=past_due,
        )

        existing = self.list_alarms()
        insert_pos = len(existing)
        for i, alarm in enumerate(existing):
            if alarm.trigger_time > trigger_time:
                insert_pos = i
                break
        creating_fresh = self.is_empty
        if creating_fresh:
            insert_addr = self.start
        elif insert_pos < len(existing):
            insert_addr = existing[insert_pos].start_addr
        else:
            insert_addr = self.end

        # A fresh buffer's header goes at insert_addr itself (self.start);
        # the entry we just asked for ends up one register above that, not
        # at insert_addr -- see _insert_registers()'s "is_empty" branch.
        entry_addr = self.start + 1 if creating_fresh else insert_addr

        self._insert_registers(insert_addr, entry_registers)
        return self.get_alarm(entry_addr)

    def delete_alarm(self, start_addr: int):
        '''Removes the alarm whose time register is at `start_addr`,
        closing the gap (docs/alarms.md sec 7's confirmed one-time-alarm-
        fires behavior, applied here to an explicit user delete instead).
        Clears the whole buffer back to empty (class docstring) if this
        was the last alarm. Raises `ValueError` if no alarm is there.'''
        target = self.get_alarm(start_addr)
        if target is None:
            raise ValueError(f"No alarm at 0x{start_addr:03X}.")

        old_header = self._memory.get_register(self.start).get_bytes()
        old_count = old_header[1]
        old_delim_addr = self.end
        n = target.num_registers
        entry_end = start_addr + n

        tail_len = old_delim_addr - entry_end + 1
        for i in range(tail_len):
            src = entry_end + i
            self._memory.set_register(start_addr + i, self._memory.get_register(src))

        new_count = old_count - n
        if new_count <= 2:
            for addr in range(self.start, old_delim_addr + 1):
                self._memory.set_register(addr, Register(size=7))
        else:
            new_header = Register(
                data=bytes([self.HEADER_MARKER, new_count]) + bytes(old_header[2:7])
            )
            self._memory.set_register(self.start, new_header)
            for addr in range(self.start + new_count, old_delim_addr + 1):
                self._memory.set_register(addr, Register(size=7))
