"""
The names of the 56 HP-41 flags, as shown in the Flags tab.

The names are hardcoded on purpose: the app never reads docs/flags.md at run
time (issue #45), so a packaged install shows exactly what a source checkout
does. docs/flags.md documents the same table for people; tests/test_flag_names.py
fails if the two ever differ.
"""

from typing import Dict

FLAG_NAMES: Dict[int, str] = {
    0: "general use",
    1: "general use",
    2: "general use",
    3: "general use",
    4: "general use",
    5: "general use",
    6: "general use",
    7: "general use",
    8: "general use",
    9: "general use",
    10: "general use",
    11: "auto execute",
    12: "double wide print",
    13: "lower case print",
    14: "overwrite card protection",
    15: "IL-printer MAN / NORM",
    16: "IL-printer TRACE",
    17: "end of record",
    18: "TINTR enable",
    19: "general use",
    20: "general use",
    21: "printer enable",
    22: "number entry",
    23: "ALPHA entry",
    24: "range error ignore",
    25: "error ignore",
    26: "audio enable",
    27: "USER mode",
    28: "decimal point",
    29: "digit grouping",
    30: "CAT mode",
    31: "timer MDY / DMY",
    32: "IL manio",
    33: "IL lock",
    34: "ADRON / ADROFF",
    35: "disable autostart",
    36: "digit number 8,9",
    37: "digit number 4,5,6,7",
    38: "digit number 2,3,6,7",
    39: "digit number 1,3,5,7,9",
    40: "display FIX / SCI",
    41: "display ENG /FIX-ENG",
    42: "trig mode DEG / GRAD",
    43: "trig mode RAD",
    44: "Continuous ON",
    45: "system data entry",
    46: "partial key sequence",
    47: "SHIFT",
    48: "ALPHA",
    49: "low BAT",
    50: "message",
    51: "SST",
    52: "PRGM mode",
    53: "I/O",
    54: "PSE",
    55: "Printer existence",
}
