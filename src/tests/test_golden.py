"""Every real state capture in tests/data decodes to what golden.json says:
pointers, global chain, programs, XM directory and contents, key
assignments, alarms, region boundaries, and saving back to the same state.
See golden.py for what is summarized and how golden.json was checked."""

import json

import pytest

from golden import GOLDEN_PATH, capture_paths, summarize

GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
CAPTURES = capture_paths()


def test_every_capture_has_an_expected_summary():
    assert sorted(p.name for p in CAPTURES) == sorted(GOLDEN)


@pytest.mark.parametrize("path", CAPTURES, ids=lambda p: p.name)
def test_capture_decodes_to_its_golden_summary(path):
    actual = summarize(path)
    expected = GOLDEN[path.name]
    # Section by section, so a failure names the part that changed.
    for section in expected:
        assert actual[section] == expected[section], section
