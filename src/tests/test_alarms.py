"""
Tests for memory/alarms.py's per-entry decode/add/delete (docs/alarms.md,
plus the HP41CX Owner's Manual vol 2 sec 16 "Alarm Functions").

What each real alarm capture decodes to is checked by test_golden.py;
these cover the time encoding and editing the buffer.
"""

import datetime
from pathlib import Path

import pytest

from memory import Memory, Alarm, DM41MemoryError, Register

DATA_DIR = Path(__file__).parent / "data"


def _dt(*args):
    return datetime.datetime(*args)


# ---- Time encoding ---------------------------------------------------------


def test_encode_time_preserves_tenths_while_still_setting_the_repeats_flag():
    """A trigger time with tenth-of-a-second precision survives
    encoding, and the repeats flag is still written as its own last
    digit (docs/alarms.md sec 12)."""
    from memory.alarms import Alarms

    when = datetime.datetime(2026, 9, 4, 10, 30, 9, 900000)

    encoded_repeating = Alarms._encode_time(when, repeats=True)
    decoded_time, decoded_repeats = Alarms._decode_time(encoded_repeating)
    assert decoded_time == when
    assert decoded_repeats is True
    assert encoded_repeating[5] == 0x91  # tenths=9 preserved, flag digit=1

    encoded_one_time = Alarms._encode_time(when, repeats=False)
    decoded_time2, decoded_repeats2 = Alarms._decode_time(encoded_one_time)
    assert decoded_time2 == when
    assert decoded_repeats2 is False
    assert encoded_one_time[5] == 0x90  # tenths=9 preserved, flag digit=0


def test_decode_returns_empty_list_when_no_alarm_buffer():
    memory = Memory.from_file(DATA_DIR / "keyassigns.dm41")
    assert memory.alarms.list_alarms() == []


def test_decode_on_fresh_memory():
    assert Memory().alarms.list_alarms() == []


# ---- add_alarm(): sorted insertion, encoding round-trip --------------------


def test_add_alarm_creates_fresh_buffer_and_returns_the_new_alarm():
    memory = Memory()
    alarm = memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5, 12, 0), text="NOON")
    assert alarm is not None
    assert alarm.text == "NOON"
    assert memory.alarms.list_alarms() == [alarm]


def test_add_alarm_keeps_buffer_sorted_by_trigger_time():
    memory = Memory()
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5, 12, 0), text="NOON")
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5, 8, 0), text="MORNING")
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5, 20, 0), text="EVENING")
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5, 10, 0), text="MIDMORNING")
    texts = [a.text for a in memory.alarms.list_alarms()]
    assert texts == ["MORNING", "MIDMORNING", "NOON", "EVENING"]


def test_add_alarm_with_repeat_interval_round_trips():
    memory = Memory()
    alarm = memory.alarms.add_alarm(
        trigger_time=_dt(2026, 9, 5, 8, 0),
        text="MORNING",
        repeat_interval=datetime.timedelta(days=1),
    )
    reread = memory.alarms.get_alarm(alarm.start_addr)
    assert reread.repeat_interval == datetime.timedelta(days=1)
    assert reread.text == "MORNING"
    # The raw byte, not just our own object round-trip -- this digit is
    # the actual signal real hardware relies on (docs/alarms.md sec 12);
    # see test_add_alarm_sets_the_real_repeats_flag_digit below for why
    # checking only the Python-level round-trip isn't enough on its own.
    time_reg = memory.get_register(alarm.start_addr).get_bytes()
    assert time_reg[5] & 0x0F == 1


def test_add_alarm_sets_the_real_repeats_flag_digit():
    """Root cause of the second 2026-09-02 "ALMCAT displayed garbage"
    report (docs/alarms.md sec 12, badalarms.dm41 v2): add_alarm() used
    to encode a repeating alarm's time register byte-for-byte identical
    to a non-repeating one -- decoding it back through this project's OWN
    then-heuristic-based reader still reported the right repeat_interval,
    which is exactly why the bug shipped undetected: a Python-level round
    trip through matching encode/decode logic looks fine even when the
    real on-the-wire marker real hardware actually reads is missing.
    A short, deliberately non-round interval (5 minutes -- the user's own
    reported case, and small enough the old magnitude heuristic would
    have accepted it too) is used so this test only passes if the real
    flag digit is genuinely being set, not because a plausibility check
    happened to agree."""
    memory = Memory()
    repeating = memory.alarms.add_alarm(
        trigger_time=_dt(2026, 9, 5, 18, 15),
        text="REPEATS SOON",
        repeat_interval=datetime.timedelta(minutes=5),
    )
    one_shot = memory.alarms.add_alarm(
        trigger_time=_dt(2026, 9, 5, 19, 0), text="ONCE ONLY"
    )
    repeating_time_reg = memory.get_register(repeating.start_addr).get_bytes()
    one_shot_time_reg = memory.get_register(one_shot.start_addr).get_bytes()
    assert repeating_time_reg[5] & 0x0F == 1
    assert one_shot_time_reg[5] & 0x0F == 0
    # And the object-level round trip is still correct too, now for the
    # right reason.
    assert memory.alarms.get_alarm(repeating.start_addr).repeat_interval == (
        datetime.timedelta(minutes=5)
    )
    assert memory.alarms.get_alarm(one_shot.start_addr).repeat_interval is None


def test_decode_raises_when_repeats_flag_set_but_next_register_is_not_a_duration():
    """A time register whose repeats-flag digit is set, but immediately
    followed by something that isn't a plausible repeat interval (here:
    the delimiter itself -- no register in between at all) is a corrupt
    buffer, not a message to guess at (docs/alarms.md sec 12) -- surfaced
    as a clear error rather than silently misread, matching how real
    hardware's own confusion looked to the user (badalarms.dm41)."""
    memory = Memory()
    alarm = memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5, 8, 0), text="X")
    time_reg = memory.get_register(alarm.start_addr).get_bytes()
    corrupted = bytearray(time_reg)
    corrupted[5] |= 0x01  # claim it repeats, without actually adding a repeat register
    memory.set_register(alarm.start_addr, Register(data=bytes(corrupted)))
    with pytest.raises(DM41MemoryError):
        memory.alarms.list_alarms()


@pytest.mark.parametrize(
    "alarm_type,expected_field",
    [
        (Alarm.TYPE_CONTROL, b"\x5e\x5eRUNPROG"),
        (Alarm.TYPE_CONDITIONAL, b"\x5eRUNPROG"),
        (Alarm.TYPE_MESSAGE, b"RUNPROG"),
    ],
)
def test_add_alarm_encodes_type_marker_correctly(alarm_type, expected_field):
    memory = Memory()
    alarm = memory.alarms.add_alarm(
        trigger_time=_dt(2026, 9, 5, 8, 0), alarm_type=alarm_type, text="RUNPROG"
    )
    # Re-decode and confirm the up-arrow marker round-trips through both
    # encode and decode, and that the label itself excludes the marker.
    reread = memory.alarms.get_alarm(alarm.start_addr)
    assert reread.alarm_type == alarm_type
    assert reread.text == "RUNPROG"
    msg_reg_addr = alarm.start_addr + alarm.num_registers - 1
    last_register = memory.get_register(msg_reg_addr).get_bytes()
    assert last_register.endswith(expected_field[-len(last_register) :])


def test_add_control_alarm_with_empty_label_means_current_program_line():
    """Owner's Manual vol 2 sec 16: a control alarm with nothing after the
    two up-arrows resumes the current program line rather than naming a
    label."""
    memory = Memory()
    alarm = memory.alarms.add_alarm(
        trigger_time=_dt(2026, 9, 5, 8, 0), alarm_type=Alarm.TYPE_CONTROL, text=""
    )
    reread = memory.alarms.get_alarm(alarm.start_addr)
    assert reread.alarm_type == Alarm.TYPE_CONTROL
    assert reread.text == ""


def test_add_alarm_defaults_past_due_from_trigger_time():
    memory = Memory()
    past = memory.alarms.add_alarm(trigger_time=_dt(2000, 1, 1))
    future = memory.alarms.add_alarm(trigger_time=_dt(2099, 1, 1))
    assert past.past_due is True
    assert future.past_due is False


def test_add_alarm_past_due_can_be_overridden():
    memory = Memory()
    alarm = memory.alarms.add_alarm(trigger_time=_dt(2099, 1, 1), past_due=True)
    assert alarm.past_due is True


def test_add_alarm_rejects_message_over_24_characters():
    memory = Memory()
    with pytest.raises(ValueError):
        memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5), text="X" * 25)


def test_add_alarm_rejects_control_label_over_22_characters():
    """A control alarm's field is the same <=24-byte Alpha-register field
    a message alarm uses, minus the 2 bytes the "^^" marker itself takes
    (Owner's Manual vol 2 sec 16 -- the label comes from the Alpha
    register, same as a plain message)."""
    memory = Memory()
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5), alarm_type=Alarm.TYPE_CONTROL, text="X" * 22)
    with pytest.raises(ValueError):
        memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5), alarm_type=Alarm.TYPE_CONTROL, text="X" * 23)


def test_add_alarm_rejects_lowercase_focal_characters():
    """Root cause of the user's 2026-09-02 "ALMCAT displayed garbage"
    report (docs/alarms.md sec 11, badalarms.dm41): FOCAL has no real
    lowercase letters above 'e' -- 0x66-0x7A are reassigned to unrelated
    symbols, not lowercase f-z. Typing ordinary mixed-case text like "Test
    Message" used to be silently accepted and written byte-for-byte,
    which displayed as garbage on real hardware. Matches xm_file.py's
    NAME_MAX_CHAR boundary for XM file names (issue #11)."""
    memory = Memory()
    with pytest.raises(ValueError, match="lowercase"):
        memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5), text="Test Message")
    # Buffer is untouched -- the whole call is rejected before any write.
    assert memory.alarms.list_alarms() == []


def test_add_alarm_allows_lowercase_a_through_e_and_uppercase():
    memory = Memory()
    alarm = memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5), text="abcde AND CAPS")
    assert alarm.text == "abcde AND CAPS"


def test_add_control_alarm_label_supports_trigraphs_for_special_characters():
    """The FOCAL-literal restriction doesn't take away the ability to
    reach a genuinely-wanted special character -- an explicit \nnn
    trigraph is still honored, matching how XM file *content* (as opposed
    to file names) already works."""
    memory = Memory()
    alarm = memory.alarms.add_alarm(
        trigger_time=_dt(2026, 9, 5),
        alarm_type=Alarm.TYPE_CONTROL,
        text="A\127B",  # \127 is Append's byte value (0x7F)
    )
    assert alarm.text == "A\127B"
    # Round-trips through the actual register bytes, not just the Python
    # object -- confirms the raw byte, not just the escaped string, made
    # it into the buffer correctly.
    reloaded = memory.alarms.get_alarm(alarm.start_addr)
    assert reloaded.text == "A\127B"


def test_add_alarm_relocates_correctly_when_key_assignments_grow():
    """The Alarms buffer sits directly above Key Assignments -- adding a
    key assignment that forces that region to grow must move an already-
    added alarm up with it, exactly like Alarms.relocate() does for the
    whole buffer (see test_memory.py's own relocation tests)."""
    memory = Memory()
    memory.key_assignments.set_assignment(11, False, 0x40)
    memory.key_assignments.set_assignment(12, True, 0x41)
    alarm = memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5), text="TEST")
    old_addr = alarm.start_addr

    memory.key_assignments.set_assignment(13, False, 0x42)  # forces growth

    assert memory.alarms.get_alarm(old_addr) is None  # moved away
    moved = memory.alarms.list_alarms()
    assert len(moved) == 1
    assert moved[0].text == "TEST"
    assert moved[0].start_addr == old_addr + 1  # KA grew by exactly 1 register


# ---- delete_alarm(): gap closing, collapse to empty -------------------------


def test_delete_alarm_closes_the_gap_and_keeps_others_sorted():
    memory = Memory()
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5, 8, 0), text="MORNING")
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5, 12, 0), text="NOON")
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5, 20, 0), text="EVENING")

    noon = next(a for a in memory.alarms.list_alarms() if a.text == "NOON")
    memory.alarms.delete_alarm(noon.start_addr)

    remaining = [a.text for a in memory.alarms.list_alarms()]
    assert remaining == ["MORNING", "EVENING"]


def test_delete_last_alarm_collapses_buffer_to_empty():
    memory = Memory()
    alarm = memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5), text="ONLY")
    memory.alarms.delete_alarm(alarm.start_addr)

    assert memory.alarms.is_empty
    assert memory.alarms.list_alarms() == []
    # The freed registers are genuinely cleared, not left with stale
    # header/delimiter marker bytes (matches KeyAssignments' own
    # delete-to-empty behavior).
    assert memory.get_register(memory.alarms.start).get_hex() == "00000000000000"


def test_delete_alarm_raises_for_unknown_address():
    memory = Memory()
    with pytest.raises(ValueError):
        memory.alarms.delete_alarm(0xC5)


def test_delete_all_alarms_one_at_a_time_from_real_fixture():
    """Regression guard for a real bug caught during development: deleting
    from a *stale* pre-fetched list corrupts later deletes once an earlier
    one has shifted addresses -- callers (and this test) must re-fetch
    list_alarms() before each delete."""
    memory = Memory.from_file(DATA_DIR / "4alarmtest.dm41")
    while memory.alarms.list_alarms():
        memory.alarms.delete_alarm(memory.alarms.list_alarms()[0].start_addr)
    assert memory.alarms.is_empty


# ---- Round-trip through to_string()/from_string() --------------------------


def test_alarms_survive_a_to_string_from_string_round_trip():
    memory = Memory()
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5, 8, 0), text="MORNING", repeat_interval=datetime.timedelta(days=1))
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5, 20, 0), alarm_type=Alarm.TYPE_CONTROL, text="RUNPROG")

    reloaded = Memory.from_string(memory.to_string())
    alarms = reloaded.alarms.list_alarms()
    assert [a.text for a in alarms] == ["MORNING", "RUNPROG"]
    assert alarms[0].repeat_interval == datetime.timedelta(days=1)
    assert alarms[1].alarm_type == Alarm.TYPE_CONTROL


# ---- Insufficient-memory guard ----------------------------------------------


def test_add_alarm_raises_when_no_room_left():
    """Fills program memory right up to the Alarms buffer (via a minimal
    .END. placement) so there's no free space left, then confirms
    add_alarm() raises DM41MemoryError rather than writing past
    free_space.end -- see Alarms._room_ceiling()."""
    memory = Memory()
    # Push .END. down to leave (almost) no free space above the Alarms
    # buffer's current (empty) start.
    memory.status_registers.set_DotEnd(memory.alarms.start)
    with pytest.raises(DM41MemoryError):
        memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 5), text="TOO BIG")
