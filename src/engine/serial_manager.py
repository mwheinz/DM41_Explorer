"""
Manage communications with a DM41L serial console.
"""

import threading
import queue
import time
import logging
from typing import Optional, Callable
import serial
import serial.tools.list_ports

from memory import DM41L, DeviceProfile

logger = logging.getLogger(__name__)


class SerialManager:
    """
    Manages serial communication and hardware discovery in a dedicated background thread.
    This class is the single source of truth for all hardware-level operations.

    `profile` is the model on the other end of this connection: the app
    checks a memory state against it before sending (memory.check_profile_fit).
    It is declared by the driver, never inferred from the transport. The
    serial console is the DM41L's, so that is what this driver declares; a
    driver for another model's serial interface would declare its own.
    """

    profile: DeviceProfile = DM41L

    def __init__(self, error_callback: Optional[Callable[[str], None]] = None):
        self.serial_inst = None
        self._thread = None
        self._stop_event = threading.Event()

        self.incoming_queue = queue.Queue()  # Data coming from device -> UI
        self.outgoing_queue = queue.Queue()  # Commands from UI -> device

        self._error_callback = error_callback
        self.is_connected = False

    def get_available_ports(self):
        """Scans system for available serial ports and returns a list of device paths."""
        try:
            available_ports = [p.device for p in serial.tools.list_ports.comports()]
            logger.info("Scan complete. Found %d ports.", (len(available_ports)))
            return available_ports
        except Exception as e:
            logger.error("Hardware discovery failed: %s", (e))
            return []

    def connect(self, port, baudrate=38400):
        """Establishes connection and starts the communication thread."""
        try:
            if self.is_connected or (self._thread and self._thread.is_alive()):
                logger.info("Already connected -- disconnecting before reconnecting.")
                self.disconnect()

            self.serial_inst = serial.Serial(port, baudrate, timeout=0.1)
            self._stop_event.clear()
            self.is_connected = True

            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            logger.info("Successfully connected to %s at %d baud.", port, baudrate)
            return True, f"Connected to {port}"
        except Exception as e:
            logger.error("Connection failed for %s: %s", port, str(e))
            self.is_connected = False
            return False, str(e)

    def disconnect(self):
        """Stops the thread and closes the serial port."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

        if self.serial_inst and self.serial_inst.is_open:
            self.serial_inst.close()

        self.is_connected = False
        logger.info("Disconnected from serial port.")

    def send_command(self, command: str):
        """Queue a command for transmission."""
        if self.is_connected:
            if not command.endswith("\n"):
                command += "\n"
            self.outgoing_queue.put(command)
        else:
            logger.warning("Attempted to send command while disconnected.")

    def send_data(self, data: str):
        """Queue data for transmission."""
        if self.is_connected:
            self.outgoing_queue.put(data)
        else:
            logger.warning("Attempted to send command while disconnected.")

    def get_next_message(self):
        """Public method for the UI thread to retrieve data from the queue."""
        try:
            return self.incoming_queue.get_nowait()
        except queue.Empty:
            return None

    def _run_loop(self):
        """The main loop running in the background thread."""
        error_msg = None
        try:
            while not self._stop_event.is_set():
                try:
                    # 1. Handle Outgoing Commands from queue first
                    try:
                        while not self.outgoing_queue.empty():
                            cmd = self.outgoing_queue.get_nowait()
                            self.serial_inst.write(cmd.encode("utf-8"))
                            logger.debug("Sent command: %s", (cmd.strip()))
                    except queue.Empty:
                        pass

                    # 2. Handle Incoming Data from device.
                    if self.serial_inst and self.serial_inst.in_waiting > 0:
                        data = (
                            self.serial_inst.readline()
                            .decode("utf-8", errors="replace")
                            .replace("\r", "")
                        )
                        if data:
                            self.incoming_queue.put(data)

                    # 3. Yield the processor long enough to let the foreground
                    # tasks have a chance to run.
                    time.sleep(0.01)

                except Exception as e:
                    logger.error("Run Loop failure: %s", str(e))
                    raise
        except Exception as e:
            error_msg = f"Serial communications failure: {str(e)}"
            logger.critical(error_msg)
        finally:
            if self.serial_inst and self.serial_inst.is_open:
                try:
                    self.serial_inst.close()
                except Exception:
                    logger.exception("Failed to close serial port during cleanup.")
            self.is_connected = False
            logger.info("Serial connection terminated.")
            if error_msg and self._error_callback:
                self._error_callback(error_msg)
