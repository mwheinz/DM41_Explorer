"""Round-trip gate: every state file in tests/data loads and saves back
to the same text.

docs/state_format.md (section 6) lists what the writer changes in the text
of a file: trailing spaces on register rows go, two-space gaps between
special-register pairs become one space, and all-zero rows are omitted.
`normalise` applies exactly those, so anything else that differs is a real
loss. Each file is read with the DM41X profile, as the app does.
"""

from pathlib import Path

import pytest

from memory import DM41X, Memory

DATA = Path(__file__).parent / "data"
STATE_FILES = sorted(list(DATA.glob("*.dm41")) + list(DATA.glob("*.d41")))

# Fixtures that are deliberately not valid states (none at present).
NOT_VALID_STATES = set()
STATE_FILES = [p for p in STATE_FILES if p.name not in NOT_VALID_STATES]


def normalise(text: str) -> str:
    """`text` with the writer's own whitespace choices applied: every line
    reduced to single-space-separated tokens, blank lines and all-zero
    register rows dropped."""
    lines = []
    for line in text.strip().splitlines():
        tokens = line.split()
        if not tokens:
            continue
        # A register row is an address followed by registers (no "X:" label).
        is_row = not tokens[0].endswith(":") and tokens[0] != "DM41"
        if is_row and all(set(t) == {"0"} for t in tokens[1:]):
            continue
        lines.append(" ".join(tokens))
    return "\n".join(lines)


def test_there_are_fixtures_to_check():
    assert len(STATE_FILES) >= 40


@pytest.mark.parametrize("path", STATE_FILES, ids=lambda p: p.name)
def test_state_file_saves_back_to_the_same_text(path):
    original = path.read_text(encoding="utf-8")
    memory = Memory.from_string(original, profile=DM41X)
    saved = memory.to_string()
    assert normalise(saved) == normalise(original)
    # And the saved text loads back to an equal memory.
    assert Memory.from_string(saved, profile=DM41X) == memory


def test_a_register_the_file_omits_loads_as_zero():
    """A state leaves out all-zero rows; the defaults a new Memory() starts
    with must not show through where a file has none (lander.dm41)."""
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
