"""
Hand-maintained FOCAL mnemonic dialects (docs/mnemonic_dialects_plan.md
sec 3.3, "Layer 2").

A dialect is a named, sourced list of alternate spellings for
instructions, for spellings that use a different *word* rather than just
a different character. Character-level variants (Σ written as S, SIG or
SIGMA; ↑ as ^ or **; ≠ as #, != or <>) are generated automatically by
memory/mnemonics.py ("Layer 1") and don't belong here.

Each dialect maps an instruction's canonical (hp41uc) name to the extra
spellings it accepts. memory/mnemonics.py resolves the canonical names
when it builds its registry, so a typo here fails at import time, and
any spelling that would mean two different instructions is a build
error there too (sec 3.5) -- dialects never override each other.

To add a dialect: append a Dialect to DIALECTS below, then run the test
suite (tests/test_mnemonics.py builds the registry and checks it).
"""

import logging
from dataclasses import dataclass
from typing import Mapping, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Dialect:
    """One named set of alternate spellings. `aliases` maps a canonical
    (hp41uc) instruction name to the extra spellings this dialect
    accepts for it. `source` records where the spellings were found; it
    feeds the generated mnemonic reference."""

    name: str
    source: str
    aliases: Mapping[str, Tuple[str, ...]]


# hp41uc's compiler alternates that Layer 1 can't generate.
HP41UC_ALTERNATES = Dialect(
    name="hp41uc alternates",
    source="hp41uc compile.h alt_fcn1/alt_fcn2 and compile.c",
    aliases={
        "P-R": ("P->R",),
        "R-P": ("R->P",),
        "D-R": ("D->R",),
        "R-D": ("R->D",),
        "R^": ("RUP",),
        "ST+": ("STO+",),
        "ST-": ("STO-",),
        "ST*": ("STO*",),
        "ST/": ("STO/",),
        "GTO": ("GOTO",),
        "END": (".END.",),
    },
)

# The DM41X renamed ED (the Extended Functions ASCII file editor, XROM
# 25,51) to ED$, "same XROM code of course". The canonical name stays ED,
# so a state file or program decompiles the same on every model.
DM41X_MANUAL = Dialect(
    name="DM41X manual",
    source="DM41X User Manual v1.34, sec 3.8.4 (ED is renamed ED$)",
    aliases={
        "ED": ("ED$",),
    },
)

DIALECTS: Tuple[Dialect, ...] = (HP41UC_ALTERNATES, DM41X_MANUAL)
