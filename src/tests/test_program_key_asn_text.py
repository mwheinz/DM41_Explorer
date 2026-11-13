"""Key ASNs column text on the Programs tab (#75): labels without a key
assignment show nothing."""

from types import SimpleNamespace

import pytest

pytest.importorskip("customtkinter")

from gui.program_tab import ProgramTab  # noqa: E402


def _label(name, text):
    return SimpleNamespace(name=name, key_assignment_text=text)


def _program(*labels):
    return SimpleNamespace(labels=list(labels))


def test_unlabelled_and_unassigned_are_blank():
    assert ProgramTab._key_assignment_text(_program()) == ""
    assert ProgramTab._key_assignment_text(_program(_label("A", "N/A"))) == ""


def test_single_assigned_label():
    assert ProgramTab._key_assignment_text(_program(_label("A", "0x1a"))) == "0x1a"


def test_many_labels_list_only_assigned_ones():
    program = _program(*[_label(f"L{i}", "N/A") for i in range(50)])
    assert ProgramTab._key_assignment_text(program) == ""
    program.labels[3] = _label("L3", "0x1a")
    assert ProgramTab._key_assignment_text(program) == "L3 0x1a"
