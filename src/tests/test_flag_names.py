"""The Flags tab's hardcoded names (gui/flag_names.py) must match the table
in docs/flags.md, which documents them. The app itself never reads the doc
(issue #45); this test is what keeps the two from drifting apart."""

import re
from pathlib import Path

from gui.flag_names import FLAG_NAMES
from memory import StatusRegisters

DOCS_FLAGS = Path(__file__).resolve().parents[2] / "docs" / "flags.md"

# | flag | description | flag | description |
_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*$")


def _documented_names():
    names = {}
    for line in DOCS_FLAGS.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line.strip())
        if match:
            a, desc_a, b, desc_b = match.groups()
            names[int(a)] = desc_a
            names[int(b)] = desc_b
    return names


def test_there_is_a_name_for_every_flag_and_no_others():
    assert sorted(FLAG_NAMES) == list(range(StatusRegisters.FLAG_COUNT))


def test_names_match_docs_flags_md():
    assert FLAG_NAMES == _documented_names()


def test_no_name_is_blank():
    assert all(name.strip() for name in FLAG_NAMES.values())
