"""
Shared address-range and sentinel-register constants used across the
memory package.
"""

from .registers import Register, REGISTER_SIZE_BYTES  # re-exported

STATUS_REGISTERS_RANGE = (0x00, 0x0F)
VOID_RANGE = (0x10, 0x3F)
# The HP 82242A IR printer module ("Blinky") keeps its working state in
# 0x020-0x02F, inside the void (#66). The rest of the void stays empty.
IR_PRINTER_RANGE = (0x20, 0x2F)
KEY_ASSIGNMENTS_RANGE = (0xC0, 0xC0)  # Key assignments are variable length.
PRIMARY_DATA_END = 0x1FF

# The lowest R00 could sensibly be on real hardware: right where the Key
# Assignments region starts (0xc0) -- R00 itself must be at least one
# register above that.
MIN_SANE_R00 = 0xC1


# The "G" special register is a documented exception: it's a single status
# byte tacked onto the state format, not a full 7-byte hardware register
# (see Memory.__init__'s special-register defaults).
SPECIAL_REGISTER_SIZE_OVERRIDES = {"G": 1}

# Every special register a state can contain, in the order to_string()
# writes them. A state naming any other letter is rejected rather than
# loaded, since to_string() would silently drop it on save.
SPECIAL_REGISTER_LABELS = ("A", "B", "C", "S", "M", "N", "G")

# The two variants of the state text format, by their header line (#82):
# the DM41 series' (`.dm41`/`.d41`) and the PX-41CX's (`.px41`). The
# registers are the same in both; only the layout differs, so a state is
# written back in the layout its header calls for.
#   row_sep      between the address and registers of a register row
#   row_end      after a register row's last register
#   pair_sep     between "<letter>: <hex>" pairs on a special-register line
#   end_marker   a last line of its own, or None
# The DM41 layout is DM41_Explorer's own, as it always has been: one space
# between special-register pairs and no trailing spaces, where the
# calculator writes two and two (docs/state_format.md sec 4). The PX-41CX
# layout is the one its dumps use (tests/data/px41cx_*.px41).
STATE_STYLES = {
    "DM41": {"row_sep": "  ", "row_end": "", "pair_sep": " ", "end_marker": None},
    "PX41": {"row_sep": " ", "row_end": " ", "pair_sep": "  ", "end_marker": "EOF"},
}

# A line that ends a state's text, whatever its header. The PX-41CX writes
# one; anything after it (such as noise from the serial transfer) is not
# part of the state.
STATE_END_MARKER = "EOF"

ZERO_REGISTER_HEX = "00000000000000"
EOM_REGISTER_HEX = "ffffffffffffff"


# A Register wraps a mutable bytearray, so these are functions that build a
# fresh one each call, not shared module-level objects: a shared instance
# stored into a Memory (set_register(addr, EOM_REGISTER)) could be changed
# in place later, silently altering the "constant" for every other user.
def zero_register() -> Register:
    """A new all-zero 7-byte register (what a missing address reads as)."""
    return Register(size=REGISTER_SIZE_BYTES)


def eom_register() -> Register:
    """A new end-of-XM-directory sentinel register (all 0xFF)."""
    return Register.from_hex(EOM_REGISTER_HEX)


# Labels for the 16 status registers, in address order.
STATUS_REGISTER_LABELS = [
    "T",
    "Z",
    "Y",
    "X",
    "LastX",
    "M",
    "N",
    "O",
    "P",
    "Q",
    "R",
    "a",
    "b",
    "c",
    "d / Flags",
    "e",
]
