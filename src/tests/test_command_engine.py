"""CommandEngine (engine/command_engine.py) and the console commands
(engine/commands.py): sending a command, stripping its echo, collecting the
reply up to the DM41 prompt, parsing it, and timing out."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from engine.base_command import BaseCommand
from engine.command_engine import CommandEngine, EngineState
from engine.commands import (
    BatteryCheckCommand,
    ConsoleTimeoutCommand,
    GetTimeCommand,
    LoadMemoryStringCommand,
    MemoryStringCommand,
    SetTimeCommand,
)
from memory import Memory

PROMPT = "DM41 >> "
STATE = Memory().to_string()


@pytest.fixture(autouse=True)
def debug_logging():
    logging.getLogger("engine.command_engine").setLevel(logging.DEBUG)


@pytest.fixture
def serial():
    manager = MagicMock()
    manager.get_next_message.return_value = None
    return manager


@pytest.fixture
def engine(serial):
    return CommandEngine(serial)


def _run(engine, serial, command, messages):
    """Executes `command`, then delivers `messages` from the calculator one
    per poll. Returns the success and error callbacks."""
    on_success, on_error = MagicMock(), MagicMock()
    serial.get_next_message.side_effect = list(messages) + [None]
    assert engine.execute(command, on_success, on_error)
    assert engine.state == EngineState.WAITING_FOR_REPLY
    for _ in messages:
        engine.process_incoming_data()
    return on_success, on_error


@pytest.mark.parametrize(
    "command, sent, reply, result",
    [
        (BatteryCheckCommand(), "b", "BAT: 3600mV\n", 3600),
        (
            ConsoleTimeoutCommand([15]),
            "ct 15",
            "Console timeout set to 15 minutes\n",
            "Console timeout set to 15 minutes",
        ),
        (GetTimeCommand(), "t", "2026-07-19 15:30:45 SUN\n", "2026-07-19 15:30:45 SUN"),
        (SetTimeCommand(["20240719", "153530"]), "ts 20240719 153530", "", ""),
        # A malformed time gets the calculator's help text back, not an error.
        (
            SetTimeCommand(["20240719"]),
            "ts 20240719",
            "Set time\n\n<YYYYMMDD> <HHMMSS>\n",
            "Set time\n\n<YYYYMMDD> <HHMMSS>",
        ),
        (MemoryStringCommand(), "s", STATE + "\n", STATE.strip()),
        (LoadMemoryStringCommand([STATE]), "l", "Read OK\n", "Read OK"),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, BaseCommand) else None,
)
def test_a_command_round_trip(engine, serial, command, sent, reply, result):
    """The command is sent, its echo is stripped, the reply is collected up
    to the prompt, and the parsed result reaches the success callback."""
    on_success, on_error = _run(engine, serial, command, [sent + "\n", reply + PROMPT])
    serial.send_command.assert_called_with(sent)
    assert engine.state == EngineState.IDLE
    on_success.assert_called_once_with(result)
    on_error.assert_not_called()


def test_the_echo_and_reply_can_arrive_together_with_padding(engine, serial):
    on_success, _ = _run(
        engine, serial, BatteryCheckCommand(), ["b\n  BAT: 3600mV  \n" + PROMPT]
    )
    on_success.assert_called_once_with(3600)


@pytest.mark.parametrize(
    "command, reply",
    [
        (BatteryCheckCommand(), "INVALID_DATA_FORMAT\n"),
        (GetTimeCommand(), "2026-07-19 15:30 INVALID SUN\n"),
        (LoadMemoryStringCommand([STATE]), "Read FAILED\n"),
    ],
    ids=["battery", "time", "load"],
)
def test_an_unparseable_reply_goes_to_the_error_callback(
    engine, serial, caplog, command, reply
):
    sent = command.get_request()
    on_success, on_error = _run(engine, serial, command, [sent + "\n", reply + PROMPT])
    assert engine.state == EngineState.IDLE
    on_success.assert_not_called()
    on_error.assert_called_once()
    assert f"Error parsing response for {type(command).__name__}" in caplog.text


def test_a_second_command_is_refused_while_one_is_in_progress(engine, caplog):
    assert engine.execute(BatteryCheckCommand(), MagicMock(), MagicMock())
    assert not engine.execute(BatteryCheckCommand(), MagicMock(), MagicMock())
    assert "Cannot execute command while another is in progress." in caplog.text


# -- Timeouts -----------------------------------------------------------------


def test_a_command_times_out_when_nothing_arrives(engine, serial):
    on_error = MagicMock()
    with patch("time.monotonic", return_value=100.0):
        engine.execute(BatteryCheckCommand(timeout=1.0), MagicMock(), on_error)
    with patch("time.monotonic", return_value=102.0):
        engine.process_incoming_data()
    assert engine.state == EngineState.IDLE
    assert engine._current_command is None
    on_error.assert_called_with("Serial connection has timed out.")


def test_arriving_data_restarts_the_timeout_clock(engine, serial):
    engine.execute(BatteryCheckCommand(timeout=2.0), MagicMock(), MagicMock())
    with patch("time.monotonic", return_value=101.5):
        serial.get_next_message.return_value = "b\n"
        engine.process_incoming_data()
    assert engine.state == EngineState.COLLECTING
    assert engine._state_timeout == 101.5

    serial.get_next_message.return_value = None
    with patch("time.monotonic", return_value=103.0):  # 1.5 s since the data
        engine.process_incoming_data()
    assert engine.state == EngineState.COLLECTING
    with patch("time.monotonic", return_value=104.0):  # 2.5 s since the data
        engine.process_incoming_data()
    assert engine.state == EngineState.IDLE


# -- LoadMemoryStringCommand's data transfer ------------------------------------


def test_load_memory_needs_data():
    with pytest.raises(ValueError, match="No data provided"):
        LoadMemoryStringCommand(args=[])


def test_trigger_transfer_sends_the_state_text_as_is():
    serial = MagicMock()
    LoadMemoryStringCommand([STATE], serial=serial).trigger_transfer()
    serial.send_data.assert_called_once_with(STATE)


def test_trigger_transfer_without_a_serial_manager_logs_instead_of_raising(caplog):
    LoadMemoryStringCommand(["some state text"]).trigger_transfer()
    assert "No serial manager available for LoadMemoryStringCommand" in caplog.text
