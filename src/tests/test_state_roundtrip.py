"""Loading a state that leaves registers out. A state omits all-zero rows
and the optional S line; whether every real capture saves back to the same
state is checked by test_golden.py."""

from pathlib import Path

from memory import DM41X, Memory

DATA = Path(__file__).parent / "data"


def test_a_register_the_file_omits_loads_as_zero():
    """The defaults a new Memory() starts with must not show through where
    a file has no row (lander.dm41 has no row 08)."""
    memory = Memory.from_string(
        (DATA / "lander.dm41").read_text(encoding="utf-8"), profile=DM41X
    )
    assert memory.get_register(0x08).get_hex() == "00000000000000"
    assert "S" not in memory.to_string().split("\n")[-2]


def test_a_file_with_an_s_line_keeps_it():
    memory = Memory.from_string(
        (DATA / "empty.dm41").read_text(encoding="utf-8"), profile=DM41X
    )
    assert "S: " in memory.to_string()
