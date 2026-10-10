"""Memory.snapshot()/restore()/transaction(), and the multi-step edits that
use them: a failure part-way through must leave the state exactly as it
was, never half changed."""

import datetime
from pathlib import Path

import pytest

from memory import DM41MemoryError, Memory, ProgramMemory, Register
from memory.status_registers import StatusRegisters

DATA_DIR = Path(__file__).parent / "data"


def _load(name):
    return Memory.from_file(DATA_DIR / name)


class _Boom(Exception):
    pass


# -- snapshot / restore / transaction ---------------------------------------


def test_restore_undoes_register_and_special_register_changes():
    memory = _load("6x-xm.dm41")
    before = memory.to_string()
    saved = memory.snapshot()

    memory.set_register(0x100, Register.from_hex("01020304050607"))
    memory.set_register(0x03, Register.from_hex("55000000000000"))
    memory.set_register("A", Register.from_hex("ffffffffffffff"))
    memory.restore(saved)

    assert memory.to_string() == before


def test_a_snapshot_can_be_restored_more_than_once():
    memory = _load("6x-xm.dm41")
    before = memory.to_string()
    saved = memory.snapshot()

    memory.set_register(0x100, Register.from_hex("01020304050607"))
    memory.restore(saved)
    memory.set_register(0x03, Register.from_hex("55000000000000"))
    memory.restore(saved)

    assert memory.to_string() == before


def test_restore_puts_back_the_cached_key_assignment_extent():
    memory = Memory()
    saved = memory.snapshot()
    memory.key_assignments.set_assignment(11, False, 0x40)
    assert memory.key_assignments.count == 1

    memory.restore(saved)

    assert memory.key_assignments.count == 0
    assert memory.key_assignments.list_assignments() == []


def test_restore_puts_back_the_modified_flag():
    memory = Memory()
    saved = memory.snapshot()
    memory.is_modified()  # sets the flag
    memory.restore(saved)
    assert memory.modified is False


def test_transaction_rolls_back_and_reraises_on_failure():
    memory = _load("6x-xm.dm41")
    before = memory.to_string()

    with pytest.raises(_Boom):
        with memory.transaction():
            memory.set_register(0x100, Register.from_hex("01020304050607"))
            raise _Boom()

    assert memory.to_string() == before


def test_transaction_keeps_changes_on_success():
    memory = Memory()
    with memory.transaction():
        memory.set_register(0x100, Register.from_hex("01020304050607"))
    assert memory.get_register(0x100).get_hex() == "01020304050607"


def test_nested_transaction_undoes_only_the_failing_level():
    memory = Memory()
    with memory.transaction():
        memory.set_register(0x100, Register.from_hex("01020304050607"))
        with pytest.raises(_Boom):
            with memory.transaction():
                memory.set_register(0x101, Register.from_hex("0a0b0c0d0e0f00"))
                raise _Boom()
    assert memory.get_register(0x100).get_hex() == "01020304050607"
    assert memory.get_register(0x101).get_hex() == "00000000000000"


# -- Program memory edits are all-or-nothing --------------------------------


def test_failed_import_after_writing_leaves_memory_unchanged(monkeypatch):
    """Moving `.END.` is the last step of an import, after the program's
    bytes are already written -- a failure there must undo the writes."""
    memory = _load("simple.dm41")
    before = memory.to_string()
    program_bytes = memory.programs.get_program_bytes(
        memory.programs.list_programs()[0]
    )
    # A copy with the label renamed so the duplicate-name check passes.
    program_bytes = program_bytes.replace(b"APPTEST", b"APPTESX")

    def fail(self, addr):
        raise _Boom()

    monkeypatch.setattr(StatusRegisters, "set_DotEnd", fail)
    with pytest.raises(_Boom):
        memory.programs.import_program(program_bytes)

    assert memory.to_string() == before


def test_failed_remove_leaves_memory_unchanged(monkeypatch):
    """remove_program() clears key flags and then rebuilds program memory
    program by program; a failure in the middle must undo all of it."""
    memory = _load("6x-xm.dm41")
    programs = memory.programs.list_programs()
    assert len(programs) >= 2
    before = memory.to_string()

    real_import = ProgramMemory.import_program
    calls = {"n": 0}

    def fail_on_second_import(self, instruction_bytes):
        calls["n"] += 1
        if calls["n"] == 2:
            raise DM41MemoryError("simulated failure mid-rebuild")
        return real_import(self, instruction_bytes)

    monkeypatch.setattr(ProgramMemory, "import_program", fail_on_second_import)
    with pytest.raises(DM41MemoryError):
        memory.programs.remove_program(programs[-1])

    assert calls["n"] == 2
    assert memory.to_string() == before


def test_failed_pack_leaves_memory_unchanged(monkeypatch):
    memory = _load("lander.dm41")
    before = memory.to_string()

    real_rebuild = ProgramMemory._rebuild

    def rebuild_then_fail(self, *args, **kwargs):
        real_rebuild(self, *args, **kwargs)
        raise _Boom()

    monkeypatch.setattr(ProgramMemory, "_rebuild", rebuild_then_fail)
    with pytest.raises(_Boom):
        memory.pack()

    assert memory.to_string() == before


# -- The edit pattern the XM and Alarms tabs use ----------------------------


def test_rejected_alarm_edit_in_a_transaction_keeps_the_original():
    memory = Memory()
    when = datetime.datetime(2026, 9, 2)
    memory.alarms.add_alarm(trigger_time=datetime.datetime(2026, 9, 1), text="FIRST")
    target = memory.alarms.add_alarm(trigger_time=when, text="SECOND")
    memory.alarms.add_alarm(trigger_time=datetime.datetime(2026, 9, 3), text="THIRD")
    before = memory.to_string()

    with pytest.raises(ValueError):
        with memory.transaction():
            memory.alarms.delete_alarm(target.start_addr)
            memory.alarms.add_alarm(trigger_time=when, text="BROKEN\\")

    assert memory.to_string() == before


def test_rejected_xm_edit_in_a_transaction_keeps_the_original():
    memory = _load("empty.dm41")
    xm = memory.extended_memory
    xm.add_file("FIRST", xm.TYPE_DATA, data_lines=["1"])
    target = xm.add_file("SECOND", xm.TYPE_DATA, data_lines=["2"])
    xm.add_file("THIRD", xm.TYPE_DATA, data_lines=["3"])
    before = memory.to_string()

    with pytest.raises(ValueError):
        with memory.transaction():
            xm.remove_file(target.header_addr)
            xm.add_file("SECOND", xm.TYPE_DATA, data_lines=["TOOLONGTEXT"])

    assert memory.to_string() == before
