"""How LKAOFF shows up in a state file (phase 3 step 8 of
docs/dm41x_explorer_plan.md; S2b and S2g).

LKAOFF clears the KEYFLAGS bits of the top two rows of keys and leaves the
assignments alone, so the OS treats those keys as unassigned and falls back
to the auto-assigned local labels. tests/data/lkaoff3.d41 and lkaon3.d41
differ in just that: each has global label LKATST on key 11 (unshifted) and
BBB on key 12 (shifted); the flags are set in the LKAON file only.
"""

from pathlib import Path

import pytest

from memory import DM41X, Memory
from memory.mnemonics import key_bytes_for, resolve

DATA_DIR = Path(__file__).parent / "data"


def _load(name):
    return Memory.from_file(DATA_DIR / name, profile=DM41X)


def _normalised(memory):
    return " ".join(memory.to_string().split())


def test_lkaoff_state_has_assignments_whose_flags_are_clear():
    memory = _load("lkaoff3.d41")
    keys = memory.key_assignments

    assert keys.flag_clear_assignments() == [(11, False), (12, True)]
    assert keys.is_lkaoff_like()
    # The assignments themselves are all there.
    assert memory.programs.get_program_for_key(11, False).name == "LKATST"
    assert memory.programs.get_program_for_key(12, True).name == "BBB"


def test_lkaon_state_has_nothing_clear():
    memory = _load("lkaon3.d41")

    assert memory.key_assignments.flag_clear_assignments() == []
    assert not memory.key_assignments.is_lkaoff_like()
    assert memory.programs.get_program_for_key(11, False).name == "LKATST"


@pytest.mark.parametrize(
    "name",
    [
        "dm41x_lkan.d41",
        "dm41x_base.d41",
        "dm41x_xrom_keys.d41",
        "keyassigns.dm41",
        "global-key-assignments.dm41",
        "manyfiles.dm41",
        "xrom-keyassignments.dm41",
    ],
)
def test_ordinary_states_have_no_flag_clear_assignments(name):
    memory = _load(name)

    assert memory.key_assignments.flag_clear_assignments() == []
    assert not memory.key_assignments.is_lkaoff_like()


def test_function_assignment_with_a_clear_flag_is_found_too():
    memory = _load("dm41x_base.d41")
    memory.key_assignments.set_assignment(13, False, key_bytes_for(resolve("COS")))
    memory.key_assignments.set_key_flag(13, False, False)

    assert memory.key_assignments.flag_clear_assignments() == [(13, False)]
    assert memory.key_assignments.is_lkaoff_like()


def test_a_clear_flag_outside_the_top_rows_is_listed_but_is_not_lkaoff():
    memory = _load("dm41x_base.d41")
    memory.key_assignments.set_assignment(41, False, key_bytes_for(resolve("COS")))
    memory.key_assignments.set_key_flag(41, False, False)

    assert memory.key_assignments.flag_clear_assignments() == [(41, False)]
    assert not memory.key_assignments.is_lkaoff_like()


def test_an_unassigned_key_with_a_clear_flag_is_not_reported():
    memory = _load("dm41x_base.d41")

    assert memory.key_assignments.flag_clear_assignments() == []


def test_saving_an_lkaoff_state_keeps_it_lkaoff():
    text = (DATA_DIR / "lkaoff3.d41").read_text()
    memory = Memory.from_string(text, profile=DM41X)

    assert _normalised(memory) == " ".join(text.split())
    assert Memory.from_string(memory.to_string(), profile=DM41X).key_assignments \
        .flag_clear_assignments() == [(11, False), (12, True)]


def test_editing_another_key_does_not_touch_the_clear_flags():
    memory = _load("lkaoff3.d41")
    keys = memory.key_assignments

    keys.set_assignment(21, False, key_bytes_for(resolve("COS")))
    assert keys.flag_clear_assignments() == [(11, False), (12, True)]
    assert keys.get_key_flag(21, False)

    keys.delete_assignment(21, False)
    assert keys.flag_clear_assignments() == [(11, False), (12, True)]


def test_assigning_a_clear_key_sets_its_flag_as_asn_does():
    """S2f: ASN under LKAOFF sets the key's flag."""
    memory = _load("lkaoff3.d41")
    keys = memory.key_assignments

    keys.set_assignment(11, False, key_bytes_for(resolve("COS")))

    assert keys.get_key_flag(11, False)
    assert keys.flag_clear_assignments() == [(12, True)]


def test_deleting_a_clear_key_leaves_the_others_alone():
    memory = _load("lkaoff3.d41")

    memory.programs.clear_program_key_assignment("LKATST")

    assert memory.key_assignments.flag_clear_assignments() == [(12, True)]
