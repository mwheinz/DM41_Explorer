"""Registers are values: Memory.get_register() returns a copy and
set_register() stores one, so set_register() is the only way to change a
Memory -- the same at every address, whether or not anything was stored
there before."""

from pathlib import Path

from memory import Memory, Register

DATA_DIR = Path(__file__).parent / "data"


def test_changing_a_returned_register_does_not_change_memory():
    memory = Memory.from_file(DATA_DIR / "6x-xm.dm41")
    before = memory.to_string()

    reg = memory.get_register(0x0D)  # stored (register c)
    reg[0] = reg[0] ^ 0xFF
    reg.set_bcd_number(1.0)

    assert memory.to_string() == before


def test_a_missing_address_behaves_the_same_as_a_stored_one():
    memory = Memory()
    assert 0x150 not in memory._core_memory

    reg = memory.get_register(0x150)
    assert reg == Register.from_hex("00000000000000")
    reg.set_bcd_number(42.0)

    assert memory.get_register(0x150) == Register.from_hex("00000000000000")
    # Reading does not add entries to the sparse store.
    assert 0x150 not in memory._core_memory


def test_changing_a_returned_special_register_does_not_change_memory():
    memory = Memory()
    before = memory.get_register("A").get_hex()

    memory.get_register("A")[0] = 0x55

    assert memory.get_register("A").get_hex() == before


def test_an_absent_special_register_still_reads_as_none():
    assert Memory().get_register("X") is None


def test_changing_a_register_after_storing_it_does_not_change_memory():
    memory = Memory()
    reg = Register.from_hex("01020304050607")
    memory.set_register(0x150, reg)
    memory.set_register("A", reg)

    reg[0] = 0xFF

    assert memory.get_register(0x150).get_hex() == "01020304050607"
    assert memory.get_register("A").get_hex() == "01020304050607"


def test_read_modify_write_round_trips():
    memory = Memory()
    reg = memory.get_register(0x150)
    reg.set_bcd_number(42.0)
    memory.set_register(0x150, reg)
    assert memory.get_register(0x150).get_bcd_number() == 42.0


def test_register_copy_is_independent():
    original = Register.from_hex("01020304050607")
    duplicate = original.copy()
    duplicate[0] = 0xFF
    assert original.get_hex() == "01020304050607"
    assert duplicate == Register.from_hex("010203040506ff")
