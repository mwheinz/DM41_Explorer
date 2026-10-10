"""What each real state capture in tests/data decodes to, as plain data.

`summarize()` reduces a state to the facts the memory model derives from it:
the partition pointers, the global chain and the programs it groups, the
extended-memory directory, key assignments, alarms and region boundaries.
tests/data/golden.json holds the expected summary of every capture, and
test_golden.py checks each one against it.

Most of these captures came from a real DM41L or DM41X, and the expected
values were checked against the calculator (CAT 1, ALMCAT, EMDIR, ...) when
the decoding was worked out, so golden.json is the ground truth -- not a
record of whatever the code happens to output. To regenerate it after an
intentional change to what a capture decodes to:

    cd src && python -m tests.golden

then review the diff of golden.json line by line before committing it.
"""

import json
from pathlib import Path

from memory import DM41L, DM41X, Memory, evaluate_mode_switch

DATA_DIR = Path(__file__).parent / "data"
GOLDEN_PATH = DATA_DIR / "golden.json"


def capture_paths():
    """Every state capture in tests/data, in name order."""
    return sorted(
        p for p in DATA_DIR.iterdir() if p.suffix in (".dm41", ".d41")
    )


def profile_for(path):
    """The model a capture is loaded as: the DM41X for its own captures
    (`dm41x*` names and `.d41` files) and for any state too large for a
    DM41L; the DM41L otherwise."""
    if path.name.startswith("dm41x") or path.suffix == ".d41":
        return DM41X
    memory = Memory.from_file(path, profile=DM41L)
    return DM41X if evaluate_mode_switch(memory, DM41L) else DM41L


def _attempt(fn):
    """fn()'s result, or {"error": message} if it raises -- some captures
    are deliberately malformed, and how they fail is part of what's
    checked."""
    try:
        return fn()
    except Exception as e:  # pylint: disable=broad-except
        return {"error": f"{type(e).__name__}: {e}"}


def _status(memory):
    status = memory.status_registers
    c = status.c().get_nibbles()
    return {
        "R00": f"0x{status.R00:03X}",
        ".END.": f"0x{status.DotEnd:03X}",
        "SIGMA-REG": f"0x{status.SigmaReg:03X}",
        "cold_start": "".join(f"{n:X}" for n in c[5:8]),
        "flags_set": [n for n, on in enumerate(status.get_all_flags()) if on],
    }


def _chain(memory):
    return [
        {
            "at": f"0x{e.header_addr:03X}.{e.header_offset}",
            "kind": e.name if e.is_named else ("END" if e.end_type == 0 else ".END."),
            "distance_bytes": e.distance_bytes,
            **({"key": e.key_assignment} if e.is_named else {}),
        }
        for e in memory.programs.list_global_chain()
    ]


def _programs(memory):
    return [
        {
            "at": f"0x{p.start_addr:03X}.{p.start_offset}",
            "length": p.length,
            "terminator": p.terminator,
            "labels": [
                [label.name, label.key_assignment] for label in p.labels
            ],
        }
        for p in memory.programs.list_programs()
    ]


def _xm_file(f):
    entry = {
        "name": f.name,
        "type": f.type_label,
        "header": f"0x{f.header_addr:03X}",
        "registers": f.declared_length,
        "segments": [f"0x{lo:03X}-0x{hi:03X}" for lo, hi in f.segments],
    }
    if f.num_registers != f.declared_length:
        entry["registers_found"] = f.num_registers
    if f.name_bytes != f.name.encode("latin-1"):
        # The displayed name hides unprintable bytes; record the real ones.
        entry["name_bytes"] = f.name_bytes.hex()
    if f.file_type == f.TYPE_DATA:
        entry["data"] = _attempt(f.get_data_lines)
    elif f.file_type == f.TYPE_ASCII:
        entry["records"] = _attempt(f.get_records)
    elif f.file_type == f.TYPE_PROGRAM:
        entry["program_bytes"] = f.byte_length
        entry["checksum_valid"] = f.checksum_valid
        entry["code"] = _attempt(lambda: f.get_instruction_bytes().hex())
    return entry


def _xm_directory_end(memory):
    """Where the directory's all-FF end-of-memory marker sits, or None for
    extended memory with no files."""
    files = memory.extended_memory.list_files()
    if not files:
        return None
    addr = files[-1].data_start - 1
    marker = memory.get_register(addr).get_hex()
    return f"0x{addr:03X}" if marker == "ff" * 7 else f"0x{addr:03X} holds {marker}"


def _key_assignments(memory):
    keys = memory.key_assignments
    numbers = keys._VALID_KEY_NUMBERS
    return {
        # [key, shifted, name, key byte, function byte 1, function byte 2]
        "assignments": [
            [a.key_number, a.shifted, a.name, a.raw_key_byte, a.fn_byte1, a.fn_byte2]
            for a in keys.list_assignments()
        ],
        # The keys whose KEYFLAGS bit is set, unshifted and shifted.
        "flags_set": [
            [k for k in numbers if keys.get_key_flag(k, False)],
            [k for k in numbers if keys.get_key_flag(k, True)],
        ],
        "flag_clear": [list(k) for k in keys.flag_clear_assignments()],
    }


def _alarms(memory):
    return [
        {
            "at": f"0x{a.start_addr:03X}",
            "time": a.trigger_time.isoformat(),
            "type": a.alarm_type,
            "text": a.text,
            "repeat_seconds": (
                a.repeat_interval.total_seconds() if a.repeat_interval else None
            ),
            "past_due": a.past_due,
            "registers": a.num_registers,
        }
        for a in memory.alarms.list_alarms()
    ]


def normalise(text):
    """State text with the writer's own whitespace choices applied: every
    line reduced to single-space-separated tokens, blank lines and all-zero
    register rows dropped -- so text that differs only in those still counts
    as the same state."""
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


def _saves_back(text, memory, profile):
    """Whether saving reproduces the file's state, and the saved text loads
    back to an equal Memory."""
    saved = memory.to_string()
    return normalise(saved) == normalise(text) and (
        Memory.from_string(saved, profile=profile) == memory
    )


def summarize(path):
    """The golden summary of one capture."""
    profile = profile_for(path)
    text = path.read_text(encoding="utf-8")
    memory = Memory.from_string(text, profile=profile)
    return {
        "profile": profile.name,
        "saves_back": _saves_back(text, memory, profile),
        "status": _status(memory),
        "regions": [
            [s.key, f"0x{s.start:03X}", f"0x{s.end:03X}"] for s in memory.regions()
        ],
        "global_chain": _attempt(lambda: _chain(memory)),
        "programs": _attempt(lambda: _programs(memory)),
        "xm_files": _attempt(
            lambda: [_xm_file(f) for f in memory.extended_memory.list_files()]
        ),
        "xm_directory_end": _attempt(lambda: _xm_directory_end(memory)),
        "key_assignments": _attempt(lambda: _key_assignments(memory)),
        "alarms": _attempt(lambda: _alarms(memory)),
    }


def write_golden():
    golden = {p.name: summarize(p) for p in capture_paths()}
    GOLDEN_PATH.write_text(
        json.dumps(golden, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return golden


if __name__ == "__main__":
    write_golden()
    print(f"Wrote {GOLDEN_PATH} -- review its diff before committing it.")
