'''
Manages sending commands to the serial console and retrieving the responses.
'''

import logging
import re
import time
from enum import Enum, auto
from typing import Any, Optional, Callable
from .base_command import BaseCommand

logger = logging.getLogger(__name__)

class EngineState(Enum):
    '''
    Really simple state machine. Only 3 states, no decisions. Any
    deviation from the pattern is an error. Most commands return an immediate
    response, the only one that's more complex is sending a memory state to the
    calculator. That one requires a first reply, then sending data, then
    another reply.
    '''
    # Waiting for commands, just logging background data
    IDLE = auto()
    # Command sent, waiting for echo to start arriving
    WAITING_FOR_REPLY = auto()
    # Echo received, collecting actual response data until prompt appears
    COLLECTING = auto()


class CommandEngine:
    '''
    Implements a simple state machine required to handle echoes and terminal
    prompts.
    '''

    # This is hardcoded to the DM41L. Other Voyager models will use different
    # prompts.
    PROMPT_PATTERN = re.compile(r"\nDM41 >> $")

    def __init__(self, serial_manager):
        self.serial = serial_manager
        self.state = EngineState.IDLE
        self._buffer = ""
        self._state_timeout = 0.0
        self._current_command: Optional[BaseCommand] = None
        self._response_callback: Optional[Callable[[Any], None]] = None
        self._error_callback: Optional[Callable[[Any], None]] = None

    def process_incoming_data(self) -> bool:
        '''
        Drains data from the SerialManager queue and advances the state machine.
        Returns True if there may be more serial data to read.
        '''
        new_data = self.serial.get_next_message()
        if not new_data:
            # Check for a timeout.
            if self.state != EngineState.IDLE:
                if (
                    time.monotonic() - self._state_timeout
                ) > self._current_command.timeout:
                    logger.error("Command timed out.")
                    self._handle_timeout()
            return False

        if self.state == EngineState.IDLE:
            # Calculator has sent an unexpected message of some kind.
            # Not necessarily an error. The most common one is the calculator
            # will send "Bye." if the connection times out and closes.
            logger.debug("Calculator sent: '%s'", new_data)
            return False

        if self.state == EngineState.WAITING_FOR_REPLY:
            logger.debug("Transitioning from WAITING to COLLECTING")
            self._buffer = ""
            self.state = EngineState.COLLECTING

        # reset the timeout clock.
        self._state_timeout = time.monotonic()

        self._buffer += new_data

        # If _buffer ends in a prompt, then the current command is
        # complete.
        prompt_match = self.PROMPT_PATTERN.search(self._buffer)
        if prompt_match:
            logger.debug("Transitioning from COLLECTING to IDLE")
            self._handle_completion(self._buffer, prompt_match.start())
            return False

        return True

    def execute(
        self,
        command: BaseCommand,
        callback: Callable[[Any], None],
        error: Callable[[Any], None],
    ) -> bool:
        '''
        The main entry point to issue commands from the CLI.

        Returns True if the command was actually queued for transmission,
        False if it was rejected because another command is already in
        progress. Callers that need to follow up an execute() call with
        something command-specific (e.g. LoadMemoryCommand.trigger_transfer())
        must check this return value first.
        '''
        logger.info("execute (%s)", type(command).__name__)

        if self.state != EngineState.IDLE:
            logger.error("Cannot execute command while another is in progress.")
            return False

        self._current_command = command
        self._response_callback = callback
        self._error_callback = error
        self.state = EngineState.WAITING_FOR_REPLY
        self._state_timeout = time.monotonic()

        # Send the request through the serial manager's queue
        self.serial.send_command(command.get_request())
        logger.info("Command queued: %s", command.get_request().strip())
        return True

    def _handle_completion(self, full_text: str, prompt_index: int):
        '''
        Handles detection of the prompt sequence, indicating the current
        command has completed.
        '''
        logger.info("_handle_completion(%s, %d)", full_text, prompt_index)

        # Extract data before the prompt match index
        response_data = full_text[:prompt_index]

        # Simple approach: the command sent was self._current_command.get_request()
        # + \n. The response starts with that exact echo.
        expected_echo = self._current_command.get_request()

        if response_data.startswith(expected_echo):
            # Remove echo from the start of data string
            actual_response = response_data[len(expected_echo) :]
            if actual_response.startswith("\n"):
                actual_response = actual_response[1:]
        else:
            # If echo pattern doesn't match exactly, we fall back to raw data
            # This might happen if there is additional noise before the echo.
            actual_response = response_data

        try:
            # Pass processed data to the command's specific parser
            parsed_result = self._current_command.parse_response(actual_response)
            if self._response_callback:
                self._response_callback(parsed_result)
        except Exception as e:
            logger.error(
                "Error parsing response for %s: %s",
                type(self._current_command).__name__,
                e,
            )
            self._error_callback(
                f"Error parsing response for "
                f"{type(self._current_command).__name__}, "
                f"{e}"
            )
        finally:
            # Reset state machine
            self._buffer = ""
            self.state = EngineState.IDLE
            self._state_timeout = 0.0
            self._current_command = None
            self._response_callback = None

    def _handle_timeout(self):
        self._buffer = ""
        self.state = EngineState.IDLE
        self._state_timeout = 0.0
        self._current_command = None
        self._response_callback = None

        self._error_callback("Serial connection has timed out.")
