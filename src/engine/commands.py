'''
The individual commands for controlling the emulator console.
'''

import re
import logging
from typing import Any
from .base_command import BaseCommand

logger = logging.getLogger(__name__)


class PingCommand(BaseCommand):
    '''
    Dummy command. Used to verify that the calculator's serial console
    is running.
    '''

    def __init__(self, timeout: float):
        super().__init__(timeout=timeout)

    @property
    def command_string(self) -> str:
        return ""

    def parse_response(self, raw_data: str) -> Any:
        return raw_data.strip()


class MemoryStringCommand(BaseCommand):
    '''
    "s" command - Causes the calculator to send its memory state to the console
    as ASCII encoded text data. This data is returned as a string.
    '''

    def __init__(self, timeout: float = 1.0, serial=None):
        super().__init__(timeout=timeout, serial=serial)

    @property
    def command_string(self) -> str:
        return "s"

    def parse_response(self, raw_data: str) -> Any:
        state = raw_data.strip()
        return state


class LoadMemoryStringCommand(BaseCommand):
    ''''l' command - Streams a string to hardware as a memory state.'''

    def __init__(self, args: list, timeout: float = 5.0, serial=None):
        super().__init__(args=args, timeout=timeout, serial=serial)
        if not args or len(args) < 1:
            raise ValueError("No data provided for LoadMemoryStringCommand.")
        self.source = args[0]

    @property
    def command_string(self) -> str:
        return "l"  # send_command will handle adding the newline

    def trigger_transfer(self):
        '''Queues the string to the serial buffer immediately after 'l'.'''
        logger.info("Beginning file transfer.")
        if not self.serial:
            logger.error("No serial manager available for LoadMemoryStringCommand.")
            return
        try:
            # Send raw data without an extra newline added by send_command
            self.serial.send_data(self.source)
            logger.info("File contents queued for transmission.")
        except Exception as e:
            logger.error("Failed to transfer data: %s", e)
            raise

    def parse_response(self, raw_data: str) -> Any:
        # Search for success/failure indicators within the accumulated buffer
        if "Read OK" in raw_data:
            return raw_data.strip()
        if "Read FAILED" in raw_data:
            raise ValueError("Transfer Failed: Read FAILED")

        return raw_data.strip()


class GetTimeCommand(BaseCommand):
    ''''t' command - Requests system time.'''

    @property
    def command_string(self) -> str:
        return "t"

    def parse_response(self, raw_data: str) -> Any:
        # Expected Response:
        # YYYY-MM-DD HH:MM:SS DOW
        response = raw_data.strip()
        pattern = r"^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\s[A-Z]{3}$"
        if re.match(pattern, response):
            return response

        raise ValueError(f"Invalid time data received: '{response}'")


class SetTimeCommand(BaseCommand):
    ''''ts' command - Sets device clock.'''

    def __init__(self, args: list):
        '''
        Sets the calculator's clock. arguments are in the form
        [ "YYYYMMDD", "HHMMSS"]
        '''
        super().__init__(args=args)

        try:
            self._formatted_arg = " ".join(args)
        except Exception as e:
            logger.error("Invalid Time Stamp '%s'", args)
            raise e

    @property
    def command_string(self) -> str:
        return f"ts {self._formatted_arg}"

    def parse_response(self, raw_data: str) -> Any:
        return raw_data.strip()


class BatteryCheckCommand(BaseCommand):
    ''''b' command - Returns battery voltage in mV.'''

    @property
    def command_string(self) -> str:
        return "b"

    def parse_response(self, raw_data: str) -> Any:
        # Expected Response:
        # "BAT: <nnnn>mV"
        pattern = r"BAT:\s*(\d+)mV"
        match = re.search(pattern, raw_data)

        if match:
            return int(match.group(1))

        raise ValueError(
            f"Could not parse battery voltage from " f"response: '{raw_data}'"
        )


class ConsoleTimeoutCommand(BaseCommand):
    ''''ct' command - Configures console timeout in minutes.'''

    def __init__(self, args: list):
        super().__init__(args=args)
        self.minutes = int(args[0])

    @property
    def command_string(self) -> str:
        return f"ct {self.minutes}"

    def parse_response(self, raw_data: str) -> Any:
        # Expected Response:
        # "Console timeout set to <nn> minutes"
        return raw_data.strip()
