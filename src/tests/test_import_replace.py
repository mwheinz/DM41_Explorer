"""
Tests for importing programs that replace existing ones (#72):
ProgramMemory.plan_import() works out which existing programs share a global
label name with the import, and import_programs_replacing() removes them,
imports, repacks and carries each key assignment over to the new label of
the same name.

manyfiles.dm41 holds XMBCD (key 11), XMALPHA (key 12) and PURXM (key 13);
twolabels.dm41 one program with labels FIRST and SECOND and no keys.
"""

from pathlib import Path

import pytest

from memory import DM41MemoryError, Memory, decode_program_txt, split_programs

DATA_DIR = Path(__file__).parent / "data"


def _txt(text: str) -> list:
    return split_programs(decode_program_txt(text.encode()))


def _names(memory) -> list:
    return [p.names_label for p in memory.programs.list_programs()]


def _key_of(memory, name):
    label = memory.programs.find_program_by_name(name)
    return memory.key_assignments.key_number_for_byte(label.key_assignment)


# -- plan_import() -------------------------------------------------------------


def test_plan_is_empty_without_a_shared_label():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    plan = memory.programs.plan_import(_txt('LBL "NEW"\nSIN\nEND\n'))
    assert plan.replaced == ()
    assert plan.carried_keys == ()


def test_plan_finds_the_program_with_the_same_label_and_its_key():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    plan = memory.programs.plan_import(_txt('LBL "XMALPHA"\nSIN\nEND\n'))
    assert [p.names_label for p in plan.replaced] == ["XMALPHA"]
    assert [label.name for label in plan.carried_keys] == ["XMALPHA"]
    assert plan.lost_labels == ()
    assert plan.lost_keys == ()


def test_plan_names_labels_and_keys_the_import_does_not_bring_back():
    memory = Memory.from_file(DATA_DIR / "twolabels.dm41")
    memory.programs.set_program_key_assignment("FIRST", 11, False)
    memory.programs.set_program_key_assignment("SECOND", 12, True)

    plan = memory.programs.plan_import(_txt('LBL "FIRST"\nSIN\nEND\n'))

    assert [p.names_label for p in plan.replaced] == ["FIRST, SECOND"]
    assert plan.lost_labels == ("SECOND",)
    assert [label.name for label in plan.carried_keys] == ["FIRST"]
    assert [label.name for label in plan.lost_keys] == ["SECOND"]


def test_plan_changes_nothing():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    before = memory.snapshot()
    memory.programs.plan_import(_txt('LBL "XMALPHA"\nSIN\nEND\n'))
    assert memory.snapshot() == before


# -- import_programs_replacing() -------------------------------------------------


def test_replacing_moves_the_program_to_newest_and_keeps_its_key():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    programs = _txt('LBL "XMALPHA"\nSIN\nEND\n')
    memory.programs.import_programs_replacing(programs)

    assert _names(memory) == ["XMBCD", "PURXM", "XMALPHA"]
    assert memory.programs.list_programs()[-1].length == len(programs[0])
    assert _key_of(memory, "XMALPHA") == (12, False)
    assert _key_of(memory, "XMBCD") == (11, False)
    assert _key_of(memory, "PURXM") == (13, False)
    assert memory.key_assignments.get_key_flag(12, False)


def test_one_file_can_replace_several_programs():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    memory.programs.import_programs_replacing(
        _txt('LBL "XMBCD"\nSIN\nEND\nLBL "PURXM"\nCOS\nEND\nLBL "NEW"\nTAN\nEND\n')
    )
    assert _names(memory) == ["XMALPHA", "XMBCD", "PURXM", "NEW"]
    assert _key_of(memory, "XMBCD") == (11, False)
    assert _key_of(memory, "PURXM") == (13, False)
    assert not memory.programs.find_program_by_name("NEW").key_assignment


def test_a_lost_label_loses_its_key_and_its_flag():
    memory = Memory.from_file(DATA_DIR / "twolabels.dm41")
    memory.programs.set_program_key_assignment("FIRST", 11, False)
    memory.programs.set_program_key_assignment("SECOND", 12, True)

    memory.programs.import_programs_replacing(_txt('LBL "FIRST"\nSIN\nEND\n'))

    assert _names(memory) == ["FIRST"]
    assert _key_of(memory, "FIRST") == (11, False)
    assert memory.programs.get_program_for_key(12, True) is None
    assert not memory.key_assignments.get_key_flag(12, True)


def test_replacing_without_a_conflict_is_a_plain_import():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    imported = memory.programs.import_programs_replacing(_txt('LBL "NEW"\nSIN\nEND\n'))
    assert [p.names_label for p in imported] == ["NEW"]
    assert _names(memory) == ["XMBCD", "XMALPHA", "PURXM", "NEW"]


def test_a_failed_replacement_puts_the_old_programs_back():
    # Far bigger than program memory, so the import fails after the old
    # XMALPHA has already been removed.
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    before = memory.snapshot()
    with pytest.raises(DM41MemoryError):
        memory.programs.import_programs_replacing(
            _txt('LBL "XMALPHA"\n' + "SIN\n" * 3000 + "END\n")
        )
    assert memory.snapshot() == before


def test_a_label_used_twice_in_the_file_is_still_rejected():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    with pytest.raises(ValueError, match="Two programs in this file"):
        memory.programs.plan_import(_txt('LBL "A"\nEND\nLBL "A"\nEND\n'))


def test_plain_import_sees_a_txt_label_already_in_memory():
    # A compiled TXT program's labels aren't chain-linked, so the duplicate
    # check has to scan its bytes forward to find them.
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    with pytest.raises(ValueError, match="already exists"):
        memory.programs.import_programs(_txt('LBL "APPTEST"\nSIN\nEND\n'))
