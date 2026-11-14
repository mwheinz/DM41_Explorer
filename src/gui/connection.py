"""
The DM41L serial connection, as the main window drives it (GitHub issue
#77): the Connect menu's actions, the port dialog, the startup sequence a
new connection runs (battery -> console timeout -> time -> memory state),
and the pump that runs CommandEngine on the Tk main loop.

`Connection` owns the SerialManager and CommandEngine. It talks to the main
window only through a few of its methods -- the status bar, the open
memory buffer and the mode -- so it can be read without the rest of
gui/app.py. Every callback here runs on the Tk main thread, via the
engine pump, so the serial port can be closed directly from any of them.
"""

import logging
from datetime import datetime
from tkinter import messagebox

from memory import ERROR, WARNING, Memory, check_profile_fit, format_findings
from engine.serial_manager import SerialManager
from engine.command_engine import CommandEngine
from engine.commands import (
    BatteryCheckCommand,
    ConsoleTimeoutCommand,
    GetTimeCommand,
    LoadMemoryStringCommand,
    MemoryStringCommand,
    SetTimeCommand,
)
from gui.dialog_common import ask_yes_no
from gui.port_dialog import PortSelectionDialog

logger = logging.getLogger(__name__)

# How often the engine pump drains the serial input.
ENGINE_POLL_MS = 50


class Connection:
    """The serial connection to a DM41L, for the main window `app`."""

    # How long show_connect_dialog() waits after the menu command returns.
    CONNECT_DIALOG_DELAY_MS = 20

    def __init__(self, app):
        self.app = app
        self.serial = SerialManager()
        self.engine = CommandEngine(self.serial)

    # -- Engine pump ------------------------------------------------------

    def start(self):
        """Starts the engine pump. Nothing touches the serial port until
        the user chooses Connect / Reconnect... (the app starts offline)."""
        self._pump_engine()

    def _pump_engine(self):
        try:
            while self.engine.process_incoming_data():
                pass
        except Exception as e:
            logger.error("Engine pump error: %s", e)
        error = self.serial.get_next_error()
        if error is not None:
            self._handle_serial_error(error)
        self.app.after(ENGINE_POLL_MS, self._pump_engine)

    def _handle_serial_error(self, msg: str):
        logger.error("Serial error: %s", msg)
        self.app.set_status(self.app.idle_status())
        # SerialManager's background thread fills its error queue after it
        # has already closed its own port handle and cleared is_connected,
        # so there's nothing left to disconnect here. The dialog is deferred
        # for the same reentrancy reason as _on_command_error's.
        self.app.after(0, lambda: messagebox.showerror("Error", "Disconnected."))

    # -- The mode guard ---------------------------------------------------

    def require_serial_mode(self) -> bool:
        """True if the serial actions apply in the app's mode. Otherwise
        says why and returns False -- the guard behind every serial action,
        for the keyboard shortcuts that never consult the menu's state."""
        mode = self.app.mode
        if mode.supports_serial:
            return True
        messagebox.showinfo(
            f"Not Available in {mode.value} Mode",
            f"A {mode.value} has no serial console, so there is "
            "nothing to connect to.\n\n"
            "Switch to DM41L mode in Preferences to use the serial "
            "connection. Note that switching mode starts a new, empty "
            "memory state.",
        )
        return False

    def _require_connection(self) -> bool:
        if not self.require_serial_mode():
            return False
        if not self.serial.is_connected:
            messagebox.showwarning("Not Connected", "Connect to the DM41L first.")
            return False
        return True

    def _connected_status(self) -> str:
        return f"Connected to {self.serial.serial_inst.port}"

    # -- Connecting -------------------------------------------------------

    def show_connect_dialog(self):
        if not self.require_serial_mode():
            return
        # _prompt_for_port() blocks in wait_window() until the dialog closes.
        # Called straight from a menu item, that nested wait runs inside
        # macOS's menu-tracking event loop mode, which never delivers events
        # to the new window: the dialog shows, but can't be clicked or
        # closed, and the app gets the spinning beachball. Returning from the
        # menu command first lets the dialog run in the normal event loop.
        self.app.after(
            self.CONNECT_DIALOG_DELAY_MS, lambda: self._prompt_for_port(None)
        )

    def _prompt_for_port(self, message):
        config = self.app.config_store
        dialog = PortSelectionDialog(
            self.app, self.serial, default_port=config.serial_port, message=message
        )
        self.app.wait_window(dialog)
        if dialog.result:
            self._connect_and_verify(dialog.result)
        elif self.serial.is_connected:
            # Cancelling out of Reconnect leaves the existing connection
            # untouched -- don't overwrite the status bar with "Not
            # connected".
            self.app.set_status(self._connected_status())
        else:
            self.app.set_status(self.app.idle_status())

    def disconnect(self):
        if not self.serial.is_connected:
            return
        self.serial.disconnect()
        self.app.set_status(self.app.idle_status())
        self.app.set_battery_text("")
        self.app.set_calc_time_text("")

    def close(self):
        """Closes the port, if open, without touching the window: for
        shutdown, when the status bar is about to go."""
        if self.serial.is_connected:
            self.serial.disconnect()

    def _connect_and_verify(self, port: str):
        config = self.app.config_store
        self.app.set_status(f"Connecting to {port}...")
        success, message = self.serial.connect(port, baudrate=config.baudrate)
        if not success:
            self.app.set_status(self.app.idle_status())
            self._prompt_for_port(f"Connection to '{port}' failed: {message}")
            return

        config.serial_port = port
        try:
            config.save()
        except Exception as e:
            logger.error("Could not save preferences to %s: %s", config.PREFS_FILE, e)
            messagebox.showerror(
                "Could not save preferences", f"{config.PREFS_FILE}, {e}"
            )
        self.app.set_status(f"Connected to {port} -- verifying...")

        self._execute_command(
            BatteryCheckCommand(timeout=2.0),
            self._on_startup_battery,
            self._on_verify_failed,
        )

    def _on_verify_failed(self, message: str):
        self.close()
        self.app.set_status(self.app.idle_status())
        self.app.after(
            0,
            lambda: self._prompt_for_port(
                f"Connected to the port, but the DM41L did not respond ({message}). "
                "Please select a different serial port."
            ),
        )

    # -- Startup sequence: battery -> timeout -> time -> memory state ------

    def _on_startup_battery(self, voltage_mv):
        logger.info("Battery: %s", voltage_mv)
        self.app.set_battery_text(f"Battery: {voltage_mv} mV")
        self.app.set_status(self._connected_status())
        self.app.after(
            10,
            lambda: self._execute_command(
                ConsoleTimeoutCommand([self.app.config_store.console_timeout_minutes]),
                self._on_timeout_command,
                self._on_command_error,
            ),
        )

    def _on_timeout_command(self, result):
        logger.info("Timeout: %s", result)
        self.app.after(
            10,
            lambda: self._execute_command(
                GetTimeCommand(), self._on_startup_time, self._on_command_error
            ),
        )

    def _on_startup_time(self, time_str):
        logger.info("Time: %s", time_str)
        self.app.set_calc_time_text(f"Calc time: {time_str}")
        self.app.after(10, self._fetch_memory_state)

    def _buffer_is_spoken_for(self) -> bool:
        """True if the open buffer came from a file or has been edited, so
        a connection must not replace it with the calculator's state."""
        return self.app.memory_source is not None or self.app.memory.modified

    def _fetch_memory_state(self):
        if self._buffer_is_spoken_for():
            # A file or edited buffer is already loaded: keep it, don't fetch.
            self.app.set_status(self._connected_status())
            return
        self.app.set_status("Reading memory state...")
        self.app.after(
            10,
            lambda: self._execute_command(
                MemoryStringCommand(),
                self._on_auto_state_received,
                self._on_command_error,
            ),
        )

    def _on_auto_state_received(self, state):
        # Re-check right at the moment the fetched state is about to be
        # applied. It's a small race condition, but it does exist.
        if self._buffer_is_spoken_for():
            logger.info(
                "Discarding auto-connect's fetched state: a file was opened "
                "in the meantime."
            )
            self.app.set_status(self._connected_status())
            return
        self._on_state_received(state)

    def _on_state_received(self, state):
        logger.info("State received.")
        try:
            # The profile is the connection driver's declared model (the
            # serial driver declares the DM41L), not the app's mode: this
            # state came off that calculator. The two agree today, since
            # only DM41L mode can connect at all.
            state_memory = Memory.from_string(state, profile=self.serial.profile)
            self.app.replace_memory(state_memory)
            self.app.set_status("Connected -- memory state loaded.")
        except Exception as e:
            self._on_command_error(f"Failed to parse memory state: {e}")

    # -- Running commands -------------------------------------------------

    def _execute_command(self, command, on_success, on_error) -> bool:
        """Runs `command` on the engine, which refuses it while another
        command is still in progress. A refused command is reported to the
        user and the status bar is put back, rather than the request being
        dropped silently while the status still claims it is under way.
        Returns whether the command was accepted -- a caller with a
        follow-up step (Send's trigger_transfer()) must check it."""
        if self.engine.execute(command, on_success, on_error):
            return True
        logger.warning(
            "%s not sent: another command is still in progress.",
            type(command).__name__,
        )
        if self.serial.is_connected:
            self.app.set_status(self._connected_status())
        else:
            self.app.set_status(self.app.idle_status())
        messagebox.showwarning(
            "Busy",
            "Another command is still in progress. Please wait a moment and try again.",
        )
        return False

    def _on_command_error(self, message: str):
        logger.error("%s", message)
        self.app.set_status(self.app.idle_status())
        self.close()
        # Deferred: showing a modal dialog immediately would open a nested
        # Tk event loop while the engine is mid-reset, risking reentrant
        # calls into process_incoming_data().
        self.app.after(0, lambda: messagebox.showerror("Error", message))

    # -- Connect menu actions ---------------------------------------------

    def set_calculator_time(self):
        if not self._require_connection():
            return
        now = datetime.now()
        args = [now.strftime("%Y%m%d"), now.strftime("%H%M%S")]
        self._execute_command(
            SetTimeCommand(args), self._on_time_set, self._on_command_error
        )

    def _on_time_set(self, result):
        logger.info("Time set: %s", result)
        self.app.set_calc_time_text("Calc time set to system time")
        self.app.after(
            0,
            lambda: messagebox.showinfo(
                "Success", "Calculator clock set to system time."
            ),
        )

    def get_state_from_calculator(self):
        if not self._require_connection():
            return
        if not self.app.confirm_discard("read the calculator's memory"):
            return
        self.app.set_status("Reading memory state...")
        self.app.after(
            10,
            lambda: self._execute_command(
                MemoryStringCommand(), self._on_state_received, self._on_command_error
            ),
        )

    def send_state_to_calculator(self):
        if not self._require_connection():
            return

        # A state can hold things the calculator on the other end does not
        # have. The connection declares which model that is -- not the
        # app's mode; see memory/profile_fit.py.
        #
        # Since the explicit modes (#43) the XM *error* branch below is
        # defence in depth rather than a path a user can reach: connecting
        # needs DM41L mode, and opening a state too large for a DM41L in
        # DM41L mode forces a switch to DM41X mode, which disconnects. The
        # check is kept because it is shared with evaluate_mode_switch()
        # and costs nothing, and because a later model (a DM41XN over
        # serial) could make the combination reachable again. The XROM
        # *warning* branch is still reached normally: a state full of
        # DM41X-only functions opens in DM41L mode quite happily.
        memory = self.app.memory
        target = self.serial.profile
        findings = check_profile_fit(memory, target)
        errors = [f for f in findings if f.level == ERROR]
        warnings = [f for f in findings if f.level == WARNING]
        for finding in findings:
            logger.warning("Send check (%s): %s", finding.level, finding)
        if errors:
            messagebox.showerror(
                f"Cannot Send to {target.name}",
                f"This memory state cannot be sent to a {target.name}:\n\n"
                f"{format_findings(errors)}\n\nNothing was sent.",
            )
            return

        message = (
            "This will overwrite the calculator's current memory with the "
            "currently loaded state."
        )
        if warnings:
            message += (
                f"\n\nThese will not work on a {target.name}:\n\n"
                f"{format_findings(warnings)}\n\nSend it anyway?"
            )
        else:
            message += " Continue?"
        # Not messagebox.askyesno(): on macOS that box is too narrow for
        # the list, which then wraps on every line (GitHub issue #55).
        if not ask_yes_no(self.app, "Send State to Calculator", message):
            return

        command = LoadMemoryStringCommand([memory.to_string()], serial=self.serial)
        if self._execute_command(command, self._on_state_sent, self._on_command_error):
            command.trigger_transfer()

    def _on_state_sent(self, result):
        logger.info("State sent: %s", result)
        self.app.after(
            0,
            lambda: messagebox.showinfo("Sent", "Memory state sent to the calculator."),
        )
