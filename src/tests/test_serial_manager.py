import pytest
import threading
import time
from unittest.mock import MagicMock, patch
from engine.serial_manager import SerialManager
from memory import DM41L


@pytest.fixture
def manager():
    """Provides a clean SerialManager instance for each test."""
    return SerialManager()


@pytest.fixture
def mock_serial():
    """Mocks the serial.Serial object to avoid actual hardware access."""
    with patch("serial.Serial") as mocked:
        # Create an instance of the mock that acts like a real Serial object
        mock_inst = MagicMock()
        mocked.return_value = mock_inst
        # Make is_open return True by default for successful connection tests
        mock_inst.is_open = True
        yield mock_inst


class TestSerialManager:

    def test_initialization(self, manager):
        """Verifies initial state of SerialManager."""
        assert manager.is_connected is False
        assert manager.serial_inst is None
        assert manager.incoming_queue.empty()
        assert manager.outgoing_queue.empty()

    def test_the_serial_console_declares_the_dm41l(self, manager):
        """The model on the other end is declared by the driver, not
        inferred; the Send check uses it (docs/dm41x_explorer_plan.md)."""
        assert manager.profile is DM41L

    @patch("serial.tools.list_ports.comports")
    def test_get_available_ports(self, mock_comports, manager):
        """Tests discovery of available system ports."""
        # Mock a port object returned by comports()
        mock_port = MagicMock()
        mock_port.device = "/dev/tty.testport"
        mock_comports.return_value = [mock_port]

        ports = manager.get_available_ports()
        assert ports == ["/dev/tty.testport"]
        assert len(ports) == 1

    def test_connect_success(self, manager, mock_serial):
        """Verifies successful connection sequence."""
        # We need to patch threading so we don't actually start a thread that runs forever
        # or we run it briefly. Here we'll just check the setup logic.
        with patch("threading.Thread") as mock_thread:
            success, message = manager.connect("/dev/tty.testport", baudrate=9600)

            assert success is True
            assert manager.is_connected is True
            assert manager.serial_inst is not None
            assert mock_thread.called  # Verifies the worker thread was started

    def test_connect_failure(self, manager):
        """Verifies behavior when serial connection fails."""
        with patch("serial.Serial", side_effect=Exception("Connection refused")):
            success, message = manager.connect("/dev/tty.badport")

            assert success is False
            assert manager.is_connected is False
            assert "Connection refused" in message

    def test_send_command(self, manager, mock_serial):
        """Verifies commands are correctly queued when connected."""
        # Set up connection state manually for testing transmission logic
        manager.is_connected = True
        manager.serial_inst = mock_serial

        manager.send_command("test_cmd")

        assert manager.outgoing_queue.qsize() == 1
        assert manager.outgoing_queue.get() == "test_cmd\n"

    def test_send_command_while_disconnected(self, manager, caplog):
        """Verifies warning when trying to send while disconnected."""
        manager.is_connected = False
        manager.send_command("should_fail")

        assert manager.outgoing_queue.empty()
        # Note: verify logging if needed, but queue check is primary functional test here

    def test_disconnect(self, manager, mock_serial):
        """Verifies cleanup during disconnection."""
        manager.serial_inst = mock_serial
        manager._thread = MagicMock()  # Mock thread to avoid joining actual thread
        manager.is_connected = True

        manager.disconnect()

        assert manager.is_connected is False
        mock_serial.close.assert_called_once()

    def test_get_next_message_empty(self, manager):
        """Verifies queue behavior when no data is present."""
        assert manager.get_next_message() is None

    def test_get_next_message_with_data(self, manager):
        """Verifies retrieval of data from the incoming queue."""
        manager.incoming_queue.put("Hello World")
        assert manager.get_next_message() == "Hello World"

    def test_run_loop_sends_queued_outgoing_commands(self, manager, mock_serial):
        """A single pass of _run_loop should flush the outgoing queue to serial_inst.write."""
        manager.serial_inst = mock_serial
        mock_serial.in_waiting = 0
        manager.outgoing_queue.put("b\n")

        # Run exactly one iteration by setting the stop event immediately
        # after the first pass would have happened.
        real_is_set = manager._stop_event.is_set
        call_count = {"n": 0}

        def is_set_once():
            call_count["n"] += 1
            return call_count["n"] > 1

        manager._stop_event.is_set = is_set_once
        with patch("time.sleep"):
            manager._run_loop()

        mock_serial.write.assert_called_once_with(b"b\n")
        assert manager.outgoing_queue.empty()

    def test_run_loop_reads_incoming_data_and_strips_carriage_returns(
        self, manager, mock_serial
    ):
        """Incoming bytes with \\r\\n should land in incoming_queue as \\n only."""
        manager.serial_inst = mock_serial
        mock_serial.in_waiting = 1
        mock_serial.readline.return_value = b"hello\r\n"

        call_count = {"n": 0}

        def is_set_once():
            call_count["n"] += 1
            return call_count["n"] > 1

        manager._stop_event.is_set = is_set_once
        with patch("time.sleep"):
            manager._run_loop()

        assert manager.get_next_message() == "hello\n"

    def test_run_loop_exits_and_marks_disconnected_on_exception(
        self, manager, mock_serial
    ):
        """If serial_inst.write raises, the loop should log, break, and clear is_connected."""
        manager.serial_inst = mock_serial
        manager.is_connected = True
        manager.outgoing_queue.put("boom\n")
        mock_serial.write.side_effect = OSError("device unplugged")

        with patch("time.sleep"):
            manager._run_loop()

        assert manager.is_connected is False
