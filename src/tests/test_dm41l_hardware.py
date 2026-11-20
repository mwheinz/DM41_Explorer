"""Tests against a real DM41L, through the app's own SerialManager and
CommandEngine. They are skipped unless asked for (see conftest.py):

    pytest -m dm41l --dm41l-port=/dev/cu.usbserial-0001
    pytest -m dm41l --dm41l-port=/dev/cu.usbserial-0001 --dm41l-allow-writes

The calculator must be on, with its console awake. The read-only tests
leave it untouched. The `dm41l_writes` ones set its clock to the computer's
time and its console timeout to the preference, as connecting in the app
does, and send its own memory state back to it."""

import time
from datetime import datetime

import pytest

from config import ProjectConfig
from engine.command_engine import CommandEngine
from engine.commands import (
    BatteryCheckCommand,
    ConsoleTimeoutCommand,
    GetTimeCommand,
    LoadMemoryStringCommand,
    MemoryStringCommand,
    PingCommand,
    SetTimeCommand,
)
from engine.serial_manager import SerialManager
from memory import DM41L, ERROR, Memory, check_profile_fit, format_findings

pytestmark = pytest.mark.dm41l

# Generous: a reply only has to start within a command's own timeout, but a
# whole state takes a moment longer to arrive.
COMMAND_WAIT_S = 15.0

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class Console:
    """Runs one command at a time on the DM41L and waits for its result,
    pumping the engine the way the app's main loop does."""

    def __init__(self, port: str):
        self.port = port
        self.serial = SerialManager()
        self.engine = CommandEngine(self.serial)

    def run(self, command, after_execute=None):
        result = {}
        accepted = self.engine.execute(
            command,
            lambda value: result.setdefault("ok", value),
            lambda message: result.setdefault("error", message),
        )
        assert accepted, "the engine refused the command as busy"
        if after_execute:
            after_execute()
        deadline = time.monotonic() + COMMAND_WAIT_S
        while not result and time.monotonic() < deadline:
            while self.engine.process_incoming_data():
                pass
            error = self.serial.get_next_error()
            if error:
                result["error"] = error
            time.sleep(0.01)
        if "error" in result:
            pytest.fail(f"{type(command).__name__} failed: {result['error']}")
        if not result:
            pytest.fail(f"{type(command).__name__}: no result from {self.port}")
        return result["ok"]

    def read_state(self) -> str:
        return self.run(MemoryStringCommand())


@pytest.fixture(scope="module")
def console(request):
    port = request.config.getoption("--dm41l-port")
    console = Console(port)
    connected, message = console.serial.connect(port)
    if not connected:
        pytest.fail(f"Could not open {port}: {message}")
    try:
        # Opening the port can take a moment to settle; an empty line then
        # wakes the console, as the app's ping does.
        time.sleep(0.5)
        result = {}
        console.engine.execute(
            PingCommand(timeout=3.0),
            lambda value: result.setdefault("ok", value),
            lambda message: result.setdefault("error", message),
        )
        deadline = time.monotonic() + COMMAND_WAIT_S
        while not result and time.monotonic() < deadline:
            while console.engine.process_incoming_data():
                pass
            time.sleep(0.01)
        if "ok" not in result:
            pytest.fail(
                f"No reply from a DM41L on {port}. Is it on, with its "
                "console awake (it says 'Bye...' when the console times out)?"
            )
        yield console
    finally:
        console.serial.disconnect()


# -- Read-only ----------------------------------------------------------------


def test_battery_voltage_is_plausible(console):
    millivolts = console.run(BatteryCheckCommand(timeout=2.0))
    # A CR2032: about 3.2 V new, and the calculator gives up well above 2 V.
    assert 2000 < millivolts < 3600, f"{millivolts} mV"


def test_the_clock_reads_as_a_date_and_time(console):
    reply = console.run(GetTimeCommand())
    # "YYYY-MM-DD HH:MM:SS DOW"; GetTimeCommand has already checked the shape.
    stamp, weekday = reply.rsplit(" ", 1)
    when = datetime.strptime(stamp, TIME_FORMAT)
    assert weekday == when.strftime("%a").upper()


def test_the_state_reads_parses_and_fits_a_dm41l(console):
    text = console.read_state()
    memory = Memory.from_string(text, profile=DM41L)
    errors = [f for f in check_profile_fit(memory, DM41L) if f.level == ERROR]
    assert not errors, format_findings(errors)
    # What would be sent back describes the same memory.
    again = Memory.from_string(memory.to_string(), profile=DM41L)
    assert again.to_string() == memory.to_string()


def test_two_reads_in_a_row_agree(console):
    first = Memory.from_string(console.read_state(), profile=DM41L)
    second = Memory.from_string(console.read_state(), profile=DM41L)
    assert first.to_string() == second.to_string()


# -- Changes the calculator ---------------------------------------------------


@pytest.mark.dm41l_writes
def test_setting_the_console_timeout_to_the_preference(console):
    config = ProjectConfig()
    config.load()
    minutes = config.console_timeout_minutes
    reply = console.run(ConsoleTimeoutCommand([minutes]))
    assert reply == f"Console timeout set to {minutes} minutes"


@pytest.mark.dm41l_writes
def test_setting_the_clock_to_the_computers_time(console):
    now = datetime.now()
    console.run(SetTimeCommand([now.strftime("%Y%m%d"), now.strftime("%H%M%S")]))
    stamp = console.run(GetTimeCommand()).rsplit(" ", 1)[0]
    drift = abs(
        (datetime.strptime(stamp, TIME_FORMAT) - datetime.now()).total_seconds()
    )
    assert drift < 5, f"the calculator's clock is {drift:.0f} s off after setting it"


@pytest.mark.dm41l_writes
def test_sending_the_state_back_leaves_it_unchanged(console, tmp_path):
    """The round trip that hung in the app before #86's fix: read the
    state, send that same state back, and read it again."""
    before = console.read_state()
    backup = tmp_path / "dm41l_before.dm41"
    backup.write_text(before + "\n")
    memory = Memory.from_string(before, profile=DM41L)

    command = LoadMemoryStringCommand([memory.to_string()], serial=console.serial)
    reply = console.run(command, after_execute=command.trigger_transfer)
    assert "Read OK" in reply, f"state backed up to {backup}"

    after = Memory.from_string(console.read_state(), profile=DM41L)
    assert after.to_string() == memory.to_string(), f"state backed up to {backup}"
