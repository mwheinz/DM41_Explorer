"""
Shared helper for tests that sweep every fixture in tests/data/.

A DM41L `.dm41` dump and a DM41X state file are the same text format, so
nothing in a file says which model's memory map applies; the caller must say
(Memory.from_string(..., profile=...)). By convention in this repository,
fixtures captured from a DM41X are named `dm41x_*` and are loaded with the
DM41X profile; every other fixture is a DM41L dump.
"""

from memory import Memory, DM41L, DM41X


def profile_for_fixture(path):
    """The DeviceProfile a tests/data fixture was captured with."""
    return DM41X if path.name.startswith("dm41x_") else DM41L


def load_fixture(path):
    """Memory.from_file() with the right profile for this fixture."""
    return Memory.from_file(path, profile=profile_for_fixture(path))
