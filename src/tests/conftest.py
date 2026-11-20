"""Opt-in tests against a real DM41L on a serial port.

They never run unless asked for, so a plain `pytest` (and CI) skips them:

    pytest -m dm41l --dm41l-port=/dev/cu.usbserial-0001
    pytest -m dm41l --dm41l-port=/dev/cu.usbserial-0001 --dm41l-allow-writes

`--dm41l-port` runs the tests marked `dm41l`, which only read from the
calculator. Those also marked `dm41l_writes` change it -- its clock, its
console timeout, or its memory (with the state it already holds) -- and
need `--dm41l-allow-writes` as well. See test_dm41l_hardware.py.
"""

import pytest


def pytest_addoption(parser):
    group = parser.getgroup("dm41l", "tests against a real DM41L")
    group.addoption(
        "--dm41l-port",
        metavar="PORT",
        default=None,
        help="serial port of a connected DM41L; runs the tests marked dm41l",
    )
    group.addoption(
        "--dm41l-allow-writes",
        action="store_true",
        default=False,
        help="also run the dm41l tests that change the calculator "
        "(clock, console timeout, memory round trip)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "dm41l: needs a real DM41L on --dm41l-port (read-only)"
    )
    config.addinivalue_line(
        "markers",
        "dm41l_writes: changes the DM41L on --dm41l-port; "
        "needs --dm41l-allow-writes too",
    )


def pytest_collection_modifyitems(config, items):
    port = config.getoption("--dm41l-port")
    writes = config.getoption("--dm41l-allow-writes")
    no_port = pytest.mark.skip(reason="needs a DM41L: pass --dm41l-port=PORT")
    no_writes = pytest.mark.skip(reason="changes the DM41L: pass --dm41l-allow-writes")
    for item in items:
        # Markers only, never item.keywords: those also hold test names and
        # parameter ids, so a test parametrized with the string "dm41l"
        # (a wrongly cased mode name) was skipped as if it needed hardware.
        if item.get_closest_marker("dm41l") and not port:
            item.add_marker(no_port)
        elif item.get_closest_marker("dm41l_writes") and not writes:
            item.add_marker(no_writes)
