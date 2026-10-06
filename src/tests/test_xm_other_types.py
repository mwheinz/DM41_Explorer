"""XM files whose header type is 4-15 (phase 3 step 6 of
docs/dm41x_explorer_plan.md).

RETPFL on a DM41X rewrites a file's type nibble to any value (S4). The
calculator labels every type above 3 "@" (S4c). tests/data/
dm41x_retpfl_before.d41 and dm41x_retpfl_after.d41 are 22-file states that
differ in that XM0, XM1 and XM2 become types 4, 5 and 6; before this step
list_files() raised on the after file and hid the whole directory.
"""

from pathlib import Path

import pytest

from memory import DM41X, DM41MemoryError, Memory, Register
from memory.xm_file import XMFile

DATA_DIR = Path(__file__).parent / "data"
RETYPED = {"XM0    ": 4, "XM1    ": 5, "XM2    ": 6}


def _files(name):
    memory = Memory.from_file(DATA_DIR / name, profile=DM41X)
    return memory, {f.name: f for f in memory.extended_memory.list_files()}


def _with_type_nibble(nibble):
    """The before state with XM0's header type changed to `nibble`."""
    memory, files = _files("dm41x_retpfl_before.d41")
    header_addr = files["XM0    "].header_addr
    raw = memory.get_register(header_addr).get_hex()
    memory.set_register(header_addr, Register.from_hex(f"{nibble:x}{raw[1:]}"))
    return memory


def test_the_retyped_state_lists_every_file():
    _, before = _files("dm41x_retpfl_before.d41")
    _, after = _files("dm41x_retpfl_after.d41")

    assert len(after) == len(before) == 22
    assert list(after) == list(before)


def test_retyped_files_are_other_types_with_the_calculators_label():
    _, after = _files("dm41x_retpfl_after.d41")

    for name, file_type in RETYPED.items():
        file = after[name]
        assert file.file_type == file_type
        assert file.is_other_type
        assert file.type_label == f"@ (type {file_type})"
        assert file.num_registers == 8


def test_the_other_files_keep_their_types():
    _, before = _files("dm41x_retpfl_before.d41")
    _, after = _files("dm41x_retpfl_after.d41")

    for name, file in after.items():
        if name not in RETYPED:
            assert not file.is_other_type
            assert file.file_type == before[name].file_type
            assert file.segments == before[name].segments


def test_retyping_leaves_the_data_where_it_was():
    _, before = _files("dm41x_retpfl_before.d41")
    _, after = _files("dm41x_retpfl_after.d41")

    for name in RETYPED:
        assert after[name].segments == before[name].segments
        assert [r.get_hex() for r in after[name].data_registers()] == [
            r.get_hex() for r in before[name].data_registers()
        ]


@pytest.mark.parametrize("nibble", range(4, 16))
def test_every_type_from_4_to_15_lists_and_is_labelled(nibble):
    memory = _with_type_nibble(nibble)

    files = {f.name: f for f in memory.extended_memory.list_files()}

    assert len(files) == 22
    assert files["XM0    "].file_type == nibble
    assert files["XM0    "].type_label == f"@ (type {nibble})"


def test_type_zero_is_still_a_corrupt_header():
    memory = _with_type_nibble(0)

    with pytest.raises(DM41MemoryError):
        memory.extended_memory.list_files()


def test_the_known_types_keep_their_labels():
    _, files = _files("dm41x_retpfl_before.d41")

    assert files["XM0    "].type_label == "Data"
    assert files["XMA0   "].type_label == "ASCII"
    assert files["XMBCD  "].type_label == "Program"
    assert not any(f.is_other_type for f in files.values())


def test_a_retyped_file_cannot_be_decoded_as_data():
    _, after = _files("dm41x_retpfl_after.d41")

    with pytest.raises(ValueError):
        after["XM0    "].get_data_lines()


def test_removing_another_file_keeps_the_retyped_ones():
    memory, before = _files("dm41x_retpfl_after.d41")
    data = {n: [r.get_hex() for r in before[n].data_registers()] for n in RETYPED}

    memory.extended_memory.remove_file(before["XM9    "].header_addr)

    after = {f.name: f for f in memory.extended_memory.list_files()}
    assert len(after) == 21
    assert "XM9    " not in after
    for name, file_type in RETYPED.items():
        assert after[name].file_type == file_type
        assert [r.get_hex() for r in after[name].data_registers()] == data[name]


def test_retyped_state_round_trips_byte_for_byte():
    text = (DATA_DIR / "dm41x_retpfl_after.d41").read_text()
    memory = Memory.from_string(text, profile=DM41X)

    assert " ".join(memory.to_string().split()) == " ".join(text.split())


def test_other_type_constants_cover_exactly_4_to_15():
    assert XMFile.TYPE_OTHER_FIRST == 4
    assert XMFile.TYPE_OTHER_LAST == 15
