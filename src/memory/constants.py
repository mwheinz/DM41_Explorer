'''
Shared address-range and sentinel-register constants used across the
memory package.
'''

from .registers import Register
from .device_profile import DM41L

STATUS_REGISTERS_RANGE = (0x00, 0x0F)
VOID_RANGE = (0x10, 0x3F)
KEY_ASSIGNMENTS_RANGE = (0xC0, 0xC0)  # Key assignments are variable length.
PRIMARY_DATA_END = 0x1FF

# The lowest R00 could sensibly be on real hardware: right where the Key
# Assignments region starts (0xc0) -- R00 itself must be at least one
# register above that.
MIN_SANE_R00 = 0xC1

# Every hardware register (HP41/DM41L) is exactly 7 bytes long -- see
# docs/memory.md Sec.2 ("Word Size"). Memory.from_string() uses this to
# reject a state whose register field is the wrong length instead of
# silently loading a corrupt/truncated Register.
REGISTER_SIZE_BYTES = 7

# The "G" special register is a documented exception: it's a single status
# byte tacked onto the state format, not a full 7-byte hardware register
# (see Memory.__init__'s special-register defaults).
SPECIAL_REGISTER_SIZE_OVERRIDES = {"G": 1}

ZERO_REGISTER_HEX = "00000000000000"
EOM_REGISTER_HEX = "ffffffffffffff"


# A Register wraps a mutable bytearray, so these are functions that build a
# fresh one each call, not shared module-level objects: a shared instance
# stored into a Memory (set_register(addr, EOM_REGISTER)) could be changed
# in place later, silently altering the "constant" for every other user.
def zero_register() -> Register:
    '''A new all-zero 7-byte register (what a missing address reads as).'''
    return Register(size=REGISTER_SIZE_BYTES)


def eom_register() -> Register:
    '''A new end-of-XM-directory sentinel register (all 0xFF).'''
    return Register.from_hex(EOM_REGISTER_HEX)


# Labels for the 16 status registers, in address order.
STATUS_REGISTER_LABELS = [
    "T", "Z", "Y", "X",
    "LastX", "M", "N", "O",
    "P", "Q", "R", "a",
    "b", "c", "d / Flags", "e",
]

# The DM41L's extended-memory regions, kept as a plain list for existing
# callers and tests. The authoritative, per-model definitions (including the
# DM41X's third region) live in device_profile.py; code that needs "the
# regions of THIS memory" must use `memory.profile.xm_regions`, not this.
XM_REGIONS = list(DM41L.xm_regions)
