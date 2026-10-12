"""
The FOCAL instruction-name registry (docs/mnemonic_dialects_plan.md).

Every instruction has three kinds of name:

- **canonical**: hp41uc's spelling, pure 7-bit ASCII. Used for text
  export only (`X^2`, `P-R`, `SREG`, `ENTER`).
- **display**: what the HP-41/DM41L itself shows, with the FOCAL glyphs
  it can actually display rendered as Unicode. Used by the GUI (`X↑2`,
  `P-R`, `ΣREG`, `ENTER↑`).
- **input aliases**: every spelling text import accepts -- canonical,
  display, their character-substitution variants ("Layer 1": `SIGMAREG`,
  `X**2`, `X!=Y?`, ...), and the hand-maintained dialect spellings in
  memory/mnemonic_dialects.py ("Layer 2": `P->R`, `STO+`, `GOTO`, ...).

The rest of the codebase shouldn't handle instruction spellings itself;
it asks this module:

- `resolve(token)` -> Op: any accepted spelling to the instruction,
  including ones written with trigraph escapes (`\\EREG` for `ΣREG`,
  docs/trigraphs.md). An unknown token's error suggests the closest
  known instructions ("did you mean ...?").
- `canonical(op)` / `display(op)`: an instruction's names.
- Key Assignment helpers: `op_for_key_bytes()`, `key_bytes_for()`,
  `display_for_key_bytes()`, `assignable_display_names()`,
  `assignable_choices()`.

XROM names come in two layers (GitHub issue #52). The core layer -- the
mainframe functions, every model's built-in XROMs and the dialect
spellings -- is checked strictly: a spelling that would mean two
instructions is a build error. The library layer holds the active plug-in
libraries (`set_active_libraries()`), highest priority first; there a
clash is normal, and the first library to claim a number or a spelling
keeps it. A name can then mean two things, and which one depends on where
it is used:

- `resolve(token)` is for program listings: the core layer first, so a
  bare `LOG` keeps meaning the mainframe LOG whatever pac is plugged in
  (hp41uc reads listings the same way).
- `resolve(token, prefer_modules=True)` is for a name typed as on the
  calculator (`ASN "LOG"`): the calculator's own search order,
  xrom_library.name_search_order() -- the built-in Time ROM, then the
  plug-in libraries, then everything else -- so the pac's LOG wins, as it
  does on a real DM41X.
- `resolve_xrom_name(name)`, for `XROM "NAME"`, searches only XROM
  functions, in the calculator's order.

Section headers ("-EXT FCN 2X", "MATH 1C") are display-only: a key holding
one shows its name, but no spelling resolves to it.

An `Op` identifies an instruction by its encoding, not its name:

- OpKind.FUNCTION -- a functions.py SINGLE_BYTE_FUNCTIONS byte. From 0x40
  up that is also the instruction's own program byte (or, for RCL/STO/
  LBL/GTO/..., its prefix byte). Below 0x40 are the keyboard-only
  functions (CAT, SST, ASN, ...), which can be key-assigned but can't
  appear in a program; they are registered with programmable=False.
- OpKind.XROM -- an XROM's (byte1, byte2) pair. The registry knows the
  functions of every model's built-in XROM library (memory/xroms/).
- OpKind.KEYWORD -- END and XROM: text-format keywords with no single
  function byte of their own.

The registry is built once, at import time, and the build fails loudly
if any spelling would mean two different instructions (plan sec 3.5).
"""

import difflib
import enum
import itertools
import logging
import re
from dataclasses import dataclass
from typing import (
    Dict,
    Hashable,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from .functions import SINGLE_BYTE_FUNCTIONS
from .xrom_library import (
    BUILTIN_MODULES_BEFORE_PLUGINS,
    XromLibrary,
    builtin_xrom_names,
)
from .mnemonic_dialects import DIALECTS, Dialect
from .trigraphs import decode_trigraphs, focal_to_unicode, unicode_to_trigraphs

logger = logging.getLogger(__name__)


class OpKind(enum.Enum):
    FUNCTION = enum.auto()
    XROM = enum.auto()
    KEYWORD = enum.auto()


@dataclass(frozen=True)
class Op:
    """One instruction, identified by its encoding (see module docstring).
    `code` is an int for FUNCTION, a (byte1, byte2) tuple for XROM, and
    the keyword's canonical name for KEYWORD."""

    kind: OpKind
    code: Hashable


def function_op(byte: int) -> Op:
    return Op(OpKind.FUNCTION, byte)


def xrom_op(byte1: int, byte2: int) -> Op:
    return Op(OpKind.XROM, (byte1, byte2))


# -- XROM numbers -----------------------------------------------------------
#
# An XROM is written "XROM mm,ff": module mm (00-31) and function ff
# (00-63). Its two bytes are byte1 = 0xA0 + mm // 4 and
# byte2 = (mm % 4) << 6 | ff (docs/key_assignments.md sec 4.2), so the
# first byte is always 0xA0-0xA7.

XROM_FIRST_BYTES = range(0xA0, 0xA8)

# "XROM 26,38", "xrom 26, 38" or "XROM 26 38".
_XROM_TEXT_RE = re.compile(r"^XROM\s+(\d{1,2})\s*[,\s]\s*(\d{1,2})$", re.IGNORECASE)


def xrom_bytes(module: int, function: int) -> Tuple[int, int]:
    """The (byte1, byte2) of XROM module,function. Raises ValueError if
    either number is out of range (module 0-31, function 0-63)."""
    if not (0 <= module <= 31 and 0 <= function <= 63):
        raise ValueError(
            f"XROM {module},{function:02d} is out of range: the module must be "
            "00-31 and the function 00-63"
        )
    return 0xA0 | (module >> 2), ((module & 0x03) << 6) | function


def xrom_numbers(byte1: int, byte2: int) -> Tuple[int, int]:
    """The (module, function) numbers an XROM's two bytes encode."""
    return ((byte1 & 0x07) << 2) | (byte2 >> 6), byte2 & 0x3F


def xrom_text(byte1: int, byte2: int) -> str:
    """ "XROM mm,ff" for an XROM's two bytes, hp41uc's spelling."""
    module, function = xrom_numbers(byte1, byte2)
    return f"XROM {module:02d},{function:02d}"


# 'XROM "LOG"': a module function by name (GitHub issue #52).
_XROM_NAME_RE = re.compile(r'^XROM\s+"(.+)"$', re.IGNORECASE)


def parse_xrom_name_text(text: str) -> Optional[str]:
    """The name in 'XROM "NAME"', or None if `text` isn't that form.
    resolve_xrom_name() looks the name up."""
    match = _XROM_NAME_RE.match(text.strip())
    return match.group(1) if match else None


def parse_xrom_text(text: str) -> Optional[Tuple[int, int]]:
    """(module, function) if `text` is "XROM mm,ff" (or "XROM mm ff", in
    any case), else None. The numbers are not range-checked here:
    xrom_bytes() does that, with a message that names them."""
    match = _XROM_TEXT_RE.match(text.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


# Instructions program_text.py dispatches on by identity.
LBL = function_op(0xCF)
GTO = function_op(0xD0)
XEQ = function_op(0xE0)
END = Op(OpKind.KEYWORD, "END")
XROM = Op(OpKind.KEYWORD, "XROM")


class UnknownMnemonicError(ValueError):
    """resolve() couldn't turn a token into a usable instruction."""


class MnemonicRegistryError(Exception):
    """The registry's own tables are inconsistent (e.g. one spelling
    would mean two instructions). A programming error, raised at import
    time, never by user input."""


# -- Character substitutions ("Layer 1") -----------------------------------
#
# ASCII stand-ins for the non-ASCII FOCAL glyphs in display names. The
# first stand-in is hp41uc's canonical choice; the rest are other
# spellings hp41uc's compiler accepts (compile.h alt_fcn1/alt_fcn2:
# SIGMA+, SIGREG, CLSIGMA, X!=Y?, X<>Y?, ENTER^, ...). Every display name
# gets every combination of these as input aliases.
_SUBSTITUTIONS = {
    "Σ": ("S", "SIG", "SIGMA"),
    "≠": ("#", "!=", "<>"),
    "↑": ("^",),
}

# "**" also stands for ↑, but only where ↑ means "to the power of" --
# hp41uc accepts X**2, Y**X, E**X, 10**X and E**X-1, not R** or ENTER**.
_POWER_STANDINS = {"↑": ("**",)}
_POWER_OPS = frozenset(function_op(b) for b in (0x51, 0x53, 0x55, 0x57, 0x58))

# The one canonical name that isn't just the display name with each
# glyph replaced by its first stand-in: hp41uc drops ENTER↑'s arrow.
_CANONICAL_EXCEPTIONS = {function_op(0x83): "ENTER"}

# Look-alike characters folded before lookup. Option-W on a Mac types
# N-ARY SUMMATION (U+2211), not GREEK CAPITAL SIGMA (U+03A3).
_UNICODE_FOLDS = str.maketrans({"∑": "Σ"})


def _ascii_form(display_name: str) -> str:
    return "".join(_SUBSTITUTIONS.get(ch, (ch,))[0] for ch in display_name)


def _library_canonical(display_name: str, op: Op) -> str:
    """A library function's ASCII name: the usual stand-ins (Σ -> S, ...),
    then trigraphs for any glyph that has none -- P∡V becomes P\\<)V, which
    resolve() decodes back (GitHub issue #52). The XROM's numbers if even
    that is not ASCII."""
    name = unicode_to_trigraphs(_ascii_form(display_name))
    return name if name.isascii() else xrom_text(*op.code)


def _found_before_plugins(op: Op) -> bool:
    """True for a built-in XROM the calculator searches before the plug-in
    modules (xrom_library.BUILTIN_MODULES_BEFORE_PLUGINS: the Time ROM)."""
    return (
        op.kind is OpKind.XROM
        and xrom_numbers(*op.code)[0] in BUILTIN_MODULES_BEFORE_PLUGINS
    )


def _substitution_variants(text: str, op: Op) -> Iterable[str]:
    """`text` (a spelling of `op`) with each substitutable glyph replaced
    by each of its stand-ins, in every combination (including `text`
    itself)."""
    power = op in _POWER_OPS
    choices = [
        (ch,)
        + _SUBSTITUTIONS.get(ch, ())
        + (_POWER_STANDINS.get(ch, ()) if power else ())
        for ch in text
    ]
    for combo in itertools.product(*choices):
        yield "".join(combo)


@dataclass(frozen=True)
class Entry:
    op: Op
    canonical: str
    display: str
    programmable: bool
    # Every accepted spelling, with where it came from ("canonical",
    # "display", "substitution", or a dialect name). Case-insensitive
    # matches of these are accepted too.
    aliases: Tuple[Tuple[str, str], ...]
    # True for an XROM a DM41L doesn't have built in: one the DM41X added
    # to the HP-41CX set, or any plug-in module's. The registry knows these
    # names whatever the model, so a program that uses one still compiles
    # and decompiles.
    dm41x_only: bool = False
    # The title of the plug-in library the function comes from; None for
    # the mainframe functions and the built-in XROMs.
    library: Optional[str] = None


class Registry:
    """The built lookup tables. Module-level functions below use one
    shared instance built from DIALECTS; tests build their own to check
    the safety rules."""

    def __init__(
        self,
        dialects: Iterable[Dialect] = DIALECTS,
        libraries: Sequence[XromLibrary] = (),
    ):
        base = self._base_entries()
        by_canonical: Dict[str, Op] = {}
        for op, (canonical_name, _display, _prog) in base.items():
            if not canonical_name.isascii():
                raise MnemonicRegistryError(
                    f"canonical name {canonical_name!r} for {op} isn't ASCII"
                )
            if by_canonical.setdefault(canonical_name, op) != op:
                raise MnemonicRegistryError(
                    f"canonical name {canonical_name!r} used by both "
                    f"{by_canonical[canonical_name]} and {op}"
                )

        self._exact: Dict[str, Op] = {}
        sources: Dict[Op, Dict[str, str]] = {op: {} for op in base}

        def add(spelling: str, op: Op, source: str) -> None:
            previous = self._exact.setdefault(spelling, op)
            if previous != op:
                raise MnemonicRegistryError(
                    f"spelling {spelling!r} ({source}) would mean both "
                    f"{base[previous][0]} and {base[op][0]}"
                )
            sources[op].setdefault(spelling, source)

        for op, (canonical_name, display_name, _prog) in base.items():
            add(canonical_name, op, "canonical")
            add(display_name, op, "display")
            for variant in _substitution_variants(display_name, op):
                add(variant, op, "substitution")

        for dialect in dialects:
            for canonical_name, spellings in dialect.aliases.items():
                op = by_canonical.get(canonical_name)
                if op is None:
                    raise MnemonicRegistryError(
                        f"dialect {dialect.name!r} lists unknown canonical "
                        f"name {canonical_name!r}"
                    )
                for spelling in spellings:
                    for variant in _substitution_variants(spelling, op):
                        add(variant, op, dialect.name)

        # Case-insensitive fallback. Two ops sharing a folded spelling
        # isn't a build error: an exact-case match can still tell them
        # apart, so resolve() only complains if it gets that far.
        self._folded: Dict[str, Set[Op]] = {}
        for spelling, op in self._exact.items():
            self._folded.setdefault(spelling.upper(), set()).add(op)

        # "DM41X only" means "not built into a DM41L" (issue #51).
        dm41l_xroms = builtin_xrom_names("DM41L")
        self._entries: Dict[Op, Entry] = {
            op: Entry(
                op=op,
                canonical=canonical_name,
                display=display_name,
                programmable=programmable,
                aliases=tuple(sources[op].items()),
                dm41x_only=op.kind is OpKind.XROM and op.code not in dm41l_xroms,
            )
            for op, (canonical_name, display_name, programmable) in base.items()
        }

        # Display-only names: the built-in libraries' section headers.
        self._headers: Dict[Op, str] = {}
        for code, name in builtin_xrom_names(headers=True).items():
            if xrom_op(*code) not in self._entries:
                self._headers[xrom_op(*code)] = name

        self._library_exact: Dict[str, Op] = {}
        self._library_folded: Dict[str, Set[Op]] = {}
        self._add_libraries(libraries)
        # Every folded spelling of either layer, for "did you mean".
        self._all_folded: Dict[str, Set[Op]] = {
            key: set(ops) for key, ops in self._folded.items()
        }
        for key, ops in self._library_folded.items():
            self._all_folded.setdefault(key, set()).update(ops)

    def _add_libraries(self, libraries: Sequence[XromLibrary]) -> None:
        """The plug-in libraries' functions, highest priority first. A
        number already taken -- by a built-in XROM or a higher-priority
        library -- decodes as that one, so the later function is left out
        entirely: its name would otherwise compile to bytes that decode as
        something else. A spelling already taken stays with the first
        library. Built-in libraries are in the core layer already."""
        for library in libraries:
            if library.builtin_on:
                continue
            for function in library.functions():
                op = xrom_op(*function.code)
                if op in self._entries or op in self._headers:
                    continue
                if function.header:
                    self._headers[op] = function.name
                    continue
                canonical_name = _library_canonical(function.name, op)
                spellings: Dict[str, str] = {}
                candidates = [(canonical_name, "canonical"), (function.name, "display")]
                candidates += [
                    (variant, "substitution")
                    for variant in _substitution_variants(function.name, op)
                ]
                for spelling, source in candidates:
                    if self._library_exact.setdefault(spelling, op) == op:
                        spellings.setdefault(spelling, source)
                self._entries[op] = Entry(
                    op=op,
                    canonical=canonical_name,
                    display=function.name,
                    programmable=True,
                    aliases=tuple(spellings.items()),
                    dm41x_only=True,
                    library=library.title,
                )
        for spelling, op in self._library_exact.items():
            self._library_folded.setdefault(spelling.upper(), set()).add(op)

    @staticmethod
    def _base_entries() -> Dict[Op, Tuple[str, str, bool]]:
        """op -> (canonical, display, programmable), before aliases."""
        base: Dict[Op, Tuple[str, str, bool]] = {}
        for byte, name in SINGLE_BYTE_FUNCTIONS.items():
            op = function_op(byte)
            display_name = name  # functions.py names are HP-41 display names
            canonical_name = _CANONICAL_EXCEPTIONS.get(op, _ascii_form(display_name))
            base[op] = (canonical_name, display_name, byte >= 0x40)
        for (byte1, byte2), name in builtin_xrom_names().items():
            base[xrom_op(byte1, byte2)] = (_ascii_form(name), name, True)
        for keyword in (END, XROM):
            base[keyword] = (keyword.code, keyword.code, True)
        return base

    def _lookup(
        self, exact: Dict[str, Op], folded: Dict[str, Set[Op]], text: str, token: str
    ) -> Optional[Op]:
        """`text` in one layer: an exact-case match, else the one op whose
        spelling matches ignoring case. None if neither; raises
        UnknownMnemonicError if the case-insensitive match is ambiguous."""
        op = exact.get(text)
        if op is not None:
            return op
        candidates = folded.get(text.upper(), set())
        if len(candidates) > 1:
            names = ", ".join(sorted(self._entries[c].canonical for c in candidates))
            raise UnknownMnemonicError(
                f"ambiguous instruction {token!r}: could be {names} "
                "(check upper/lower case)"
            )
        return next(iter(candidates), None)

    def resolve(
        self,
        token: str,
        *,
        programmable_only: bool = True,
        prefer_modules: bool = False,
    ) -> Op:
        text = self._normalize(token)
        core = self._lookup(self._exact, self._folded, text, token)
        if core is not None and (not prefer_modules or _found_before_plugins(core)):
            op = core
        else:
            library = self._lookup(
                self._library_exact, self._library_folded, text, token
            )
            op = library or core
        if op is None:
            message = f"unrecognized instruction {token!r}"
            suggestions = self.suggest(text, programmable_only=programmable_only)
            if suggestions:
                message += f" (did you mean: {', '.join(suggestions)})"
            raise UnknownMnemonicError(message)
        if programmable_only and not self._entries[op].programmable:
            raise UnknownMnemonicError(
                f"{token!r} is a keyboard-only function and can't appear in a program"
            )
        return op

    def resolve_xrom_name(self, token: str) -> Op:
        """The XROM function named `token`, in the calculator's search
        order, never a mainframe function. Raises UnknownMnemonicError if
        no built-in or active library has it."""
        text = self._normalize(token)
        core = self._lookup(self._exact, self._folded, text, token)
        if core is not None and core.kind is not OpKind.XROM:
            core = None
        if core is not None and _found_before_plugins(core):
            return core
        op = self._lookup(self._library_exact, self._library_folded, text, token)
        op = op or core
        if op is None:
            raise UnknownMnemonicError(
                f"no XROM function named {token!r} in the built-in or active "
                "libraries (write XROM mm,ff for a module that isn't active)"
            )
        return op

    @staticmethod
    def _normalize(token: str) -> str:
        """Plan sec 3.3 steps 1-2. Trigraphs are decoded first, before
        any case folding, because their shorthands are case-sensitive
        (\\E is Sigma, \\e isn't a trigraph at all). The decoded FOCAL
        bytes are then rendered as the display glyphs (\\E -> Σ,
        \\/= -> ≠, \\^| -> ↑), which the alias table already knows."""
        text = token
        if "\\" in text:
            try:
                text = focal_to_unicode(decode_trigraphs(text))
            except ValueError as exc:
                raise UnknownMnemonicError(
                    f"unrecognized instruction {token!r}: {exc}"
                ) from exc
        return text.translate(_UNICODE_FOLDS)

    def suggest(
        self, text: str, *, programmable_only: bool = True, limit: int = 3
    ) -> List[str]:
        """Up to `limit` instructions whose spellings are closest to
        `text`, each as "DISPLAY" or "DISPLAY (CANONICAL)" when the two
        differ. Empty if nothing is reasonably close."""
        wanted = text.upper()
        suggestions: List[str] = []
        seen: Set[Op] = set()
        for match in difflib.get_close_matches(
            wanted, self._all_folded, n=limit * 4, cutoff=0.7
        ):
            for op in sorted(
                self._all_folded[match], key=lambda o: self._entries[o].canonical
            ):
                entry = self._entries[op]
                if op in seen or (programmable_only and not entry.programmable):
                    continue
                seen.add(op)
                if entry.display == entry.canonical:
                    suggestions.append(entry.display)
                else:
                    suggestions.append(f"{entry.display} ({entry.canonical})")
            if len(suggestions) >= limit:
                break
        return suggestions[:limit]

    def entry(self, op: Op) -> Entry:
        return self._entries[op]

    def is_known(self, op: Op) -> bool:
        return op in self._entries

    def header_name(self, op: Op) -> Optional[str]:
        """The name of the section header `op` is, or None."""
        return self._headers.get(op)

    def entries(self) -> List[Entry]:
        return list(self._entries.values())


_REGISTRY = Registry()


def set_active_libraries(libraries: Iterable[XromLibrary]) -> None:
    """Rebuilds the registry with `libraries` as the active plug-in
    libraries, highest priority first -- typically
    xrom_library.active_set(...).libraries; the built-in ones in it are
    skipped, being known already. An empty list goes back to the built-in
    XROMs alone."""
    global _REGISTRY
    _REGISTRY = Registry(libraries=list(libraries))


def resolve(
    token: str, *, programmable_only: bool = True, prefer_modules: bool = False
) -> Op:
    """The instruction any accepted spelling `token` means. Tries an
    exact-case match first, then a case-insensitive one. Raises
    UnknownMnemonicError (a ValueError) if `token` isn't a known
    spelling, is ambiguous without its exact case, or -- with
    programmable_only -- names a keyboard-only function.

    By default a name means what it means in a program listing: a
    mainframe or built-in function before any plug-in library's. With
    `prefer_modules`, it follows the calculator's own search order, as a
    name typed into ASN does (see the module docstring)."""
    return _REGISTRY.resolve(
        token, programmable_only=programmable_only, prefer_modules=prefer_modules
    )


def resolve_xrom_name(name: str) -> Op:
    """The XROM function 'XROM "NAME"' means: the built-in XROMs and the
    active plug-in libraries only, in the calculator's search order.
    Raises UnknownMnemonicError if none has it."""
    return _REGISTRY.resolve_xrom_name(name)


def canonical(op: Op) -> str:
    """hp41uc's ASCII name for `op`, used for text export."""
    return _REGISTRY.entry(op).canonical


def display(op: Op) -> str:
    """The HP-41's own name for `op`, for the GUI."""
    return _REGISTRY.entry(op).display


def is_known(op: Op) -> bool:
    return _REGISTRY.is_known(op)


def is_dm41x_only(op: Op) -> bool:
    """True if `op` is an XROM the DM41X added (X<I>Y, TRNG and the DM41X
    module's functions), so a DM41L doesn't have it. Raises KeyError for an
    op that isn't registered -- check is_known() first."""
    return _REGISTRY.entry(op).dm41x_only


def library_of(op: Op) -> Optional[str]:
    """The title of the active plug-in library `op` comes from, or None
    for a mainframe function, a built-in XROM or an unknown op."""
    return _REGISTRY.entry(op).library if _REGISTRY.is_known(op) else None


def entries() -> List[Entry]:
    """Every registered instruction, for reference listings."""
    return _REGISTRY.entries()


# -- Key Assignment Register helpers ----------------------------------------
#
# A Key Assignment Register entry stores a function as one byte (a
# functions.py SINGLE_BYTE_FUNCTIONS key) or an XROM (byte1, byte2) pair
# (docs/key_assignments.md sec 4.2) -- exactly an OpKind.FUNCTION or
# OpKind.XROM Op's code.


def character_substitutions() -> List[Tuple[str, Tuple[str, ...], Tuple[str, ...]]]:
    """The Layer 1 table, for reference listings: (glyph, its ASCII
    stand-ins, extra stand-ins accepted only in the power functions
    X↑2/Y↑X/E↑X/10↑X/E↑X-1). The first stand-in is hp41uc's canonical
    choice."""
    return [
        (glyph, standins, _POWER_STANDINS.get(glyph, ()))
        for glyph, standins in _SUBSTITUTIONS.items()
    ]


def op_for_key_bytes(fn_byte1: int, fn_byte2: Optional[int]) -> Op:
    """The Op a Key Assignment Register entry's function byte(s) encode
    (`fn_byte2` is None for a single-byte function). Not necessarily a
    known instruction -- check is_known()."""
    if fn_byte2 is None:
        return function_op(fn_byte1)
    return xrom_op(fn_byte1, fn_byte2)


def key_bytes_for(op: Op) -> Union[int, Tuple[int, int]]:
    """The inverse of op_for_key_bytes(): an int for a single-byte
    function, a (byte1, byte2) tuple for an XROM. Raises ValueError for
    a text-format keyword (END, XROM), which can't be key-assigned."""
    if op.kind is OpKind.KEYWORD:
        raise ValueError(f"{op.code} can't be assigned to a key")
    return op.code


def display_for_key_bytes(fn_byte1: int, fn_byte2: Optional[int]) -> str:
    """The display name for a Key Assignment Register entry's function
    byte(s): a function's name, or a section header's ("-EXT FCN 2X" for
    XROM 25,00), as the calculator shows it. Anything else falls back to
    "XROM mm,ff" for an XROM (first byte 0xA0-0xA7), or to "0xNN" /
    "0xNN 0xNN". Never raises."""
    op = op_for_key_bytes(fn_byte1, fn_byte2)
    if is_known(op):
        return display(op)
    header = _REGISTRY.header_name(op)
    if header is not None:
        return header
    if fn_byte2 is None:
        return f"0x{fn_byte1:02X}"
    if fn_byte1 in XROM_FIRST_BYTES:
        return xrom_text(fn_byte1, fn_byte2)
    return f"0x{fn_byte1:02X} 0x{fn_byte2:02X}"


def is_available_on(op: Op, profile) -> bool:
    """Whether a `profile` calculator actually has `op`.

    Only XROMs can be missing: a single-byte function is part of every
    HP-41's own instruction set, while an XROM comes from a module, and
    `profile.builtin_xroms` is what that model has built in. A model that
    loads modules (the DM41X) can have any XROM, by plugging in the module
    that holds it. A `profile` of None means "any model", which is what
    every caller that does not care about availability passes."""
    if profile is None or op.kind is not OpKind.XROM:
        return True
    return op.code in profile.builtin_xroms or profile.loads_modules


def assignable_display_names(profile=None) -> List[str]:
    """Every key-assignable function's display name, sorted -- built-in
    functions (including keyboard-only ones like CAT) and XROMs.

    With a `profile`, only the functions that model actually has: in
    DM41L mode the key-assignment picker offers the HP-41CX set alone,
    since a DM41L cannot run a DM41X function at all.
    Without one, every registered function, which is what the mnemonic
    reference and the import path want -- the registry always knows every
    name whatever model is in force."""
    return sorted(
        e.display
        for e in _REGISTRY.entries()
        if e.op.kind is not OpKind.KEYWORD and is_available_on(e.op, profile)
    )


def assignable_choices(profile=None) -> List[Tuple[str, Op]]:
    """The key-assignment picker's entries, sorted: (label, op) for each
    function assignable_display_names() would list. The label is the
    display name, unless two functions share it -- a pac's SOLVE and
    another's, or a pac's LOG and the mainframe LOG -- when each is told
    apart by where it comes from: "SOLVE  (Math Pac, XROM 01,09)",
    "SIZE?  (built in, XROM 25,44)", "LOG  (HP-41 function)"."""
    found = [
        e
        for e in _REGISTRY.entries()
        if e.op.kind is not OpKind.KEYWORD and is_available_on(e.op, profile)
    ]
    counts: Dict[str, int] = {}
    for entry in found:
        counts[entry.display] = counts.get(entry.display, 0) + 1
    choices = []
    for entry in found:
        label = entry.display
        if counts[label] > 1:
            if entry.op.kind is not OpKind.XROM:
                label += "  (HP-41 function)"
            else:
                source = entry.library or "built in"
                label += f"  ({source}, {xrom_text(*entry.op.code)})"
        choices.append((label, entry.op))
    return sorted(choices, key=lambda choice: choice[0])
