"""
XROM libraries: one JSON file per ROM, naming its XROM functions (GitHub
issues #44 and #50).

A library lists a ROM's module numbers (XROM 1-31) and, for each, its
functions by number (0-63) with the HP-41's own display name. The files
shipped with DM41_Explorer are in memory/xroms/; tools/make_xrom_library.py
builds them from .mod files and hp41uc XROM lists. A file looks like this
(see the plan on issue #44 for every field):

    {"format": "dm41-xrom-library", "format_version": 1,
     "id": "math", "title": "Math Pac", "builtin_on": [],
     "modules": [{"xrom": 1, "functions": [
         {"fn": 0, "name": "MATH 1C", "type": "mcode", "header": true},
         {"fn": 1, "name": "MATRIX", "type": "focal"}]}]}

This module only reads and checks them; nothing here knows about the GUI or
the instruction-name registry. It answers three questions:

- load_libraries(): which files are usable. A file that fails validation,
  is from a newer format version, or reuses an id already loaded is
  rejected with an ERROR finding rather than half-read.
- active_set(): the libraries in force for a model, in priority order --
  the model's built-in library (the one whose `builtin_on` names it)
  first, then the user's enabled libraries in their order.
- find_conflicts(): what clashes among the active libraries. Two libraries
  using the same XROM number is a WARNING: a calculator can run only one
  of them. A function name another one hides -- a mainframe function, or
  any earlier library's function -- is a NOTICE: the name means the other
  one, and only "XROM mm,ff" reaches this one. Section headers are never
  matched by name, so they never hide anything.

It deliberately imports neither mnemonics.py nor device_profile.py: both
will be built from these libraries (issue #51), so they will import this
module, not the other way round.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple

from .functions import SINGLE_BYTE_FUNCTIONS

FORMAT = "dm41-xrom-library"
FORMAT_VERSION = 1

# The models a library can be built into. Kept here rather than read from
# device_profile.PROFILES, which will depend on this module (see above).
KNOWN_MODELS = ("DM41L", "DM41X")

# Where the libraries shipped with the application live.
SHIPPED_DIR = Path(__file__).resolve().parent / "xroms"

ERROR = "error"
WARNING = "warning"
NOTICE = "notice"

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FUNCTION_TYPES = ("mcode", "focal")
_OPTIONAL_TEXT = ("version", "part_number", "author", "source")


class XromLibraryError(ValueError):
    """A library file that cannot be used, with a message for the user."""


def xrom_label(module: int, function: int) -> str:
    """ "XROM mm,ff", hp41uc's spelling."""
    return f"XROM {module:02d},{function:02d}"


@dataclass(frozen=True)
class XromFunction:
    """One function of a library: XROM `module`,`function` is `name`.
    `header` marks a CAT 2 section header, which is decoded and listed but
    never matched by name. `type` ("mcode", "focal" or None) is for
    information only."""

    module: int
    function: int
    name: str
    type: Optional[str] = None
    header: bool = False

    @property
    def label(self) -> str:
        return xrom_label(self.module, self.function)


@dataclass(frozen=True)
class XromModule:
    """One XROM number of a library, with its functions in number order."""

    xrom: int
    functions: Tuple[XromFunction, ...]
    page_id: Optional[str] = None


@dataclass(frozen=True)
class XromLibrary:
    """One validated library file. `path` is where it was read from (None
    for one parsed from data); `shipped` is whether it came with the
    application."""

    id: str
    title: str
    builtin_on: FrozenSet[str]
    modules: Tuple[XromModule, ...]
    version: Optional[str] = None
    part_number: Optional[str] = None
    author: Optional[str] = None
    source: Optional[str] = None
    path: Optional[Path] = None
    shipped: bool = False

    @property
    def module_numbers(self) -> Tuple[int, ...]:
        return tuple(module.xrom for module in self.modules)

    def functions(self) -> Iterator[XromFunction]:
        """Every function, module by module, in number order."""
        for module in self.modules:
            yield from module.functions


@dataclass(frozen=True)
class XromFinding:
    """One problem with a library file, or one conflict between active
    libraries. `level` is ERROR, WARNING or NOTICE; `library_ids` are the
    libraries involved, so a manager can show it next to each of them."""

    level: str
    message: str
    library_ids: Tuple[str, ...] = ()

    def __str__(self) -> str:
        return self.message


# -- Reading and validating -------------------------------------------------


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_library(
    data, where: str = "library", *, path: Optional[Path] = None, shipped=False
) -> XromLibrary:
    """Validates `data` (a decoded JSON object) as a library. Raises
    XromLibraryError naming `where` and the first problem found. Unknown
    fields are ignored, so a later version can add optional ones."""

    def fail(problem: str):
        raise XromLibraryError(f"{where}: {problem}")

    if not isinstance(data, dict) or data.get("format") != FORMAT:
        fail(f'not an XROM library (its "format" is not "{FORMAT}")')
    version = data.get("format_version")
    if not _is_int(version) or version < 1:
        fail('"format_version" must be a whole number, 1 or more')
    if version > FORMAT_VERSION:
        fail(
            f"written for a newer version of DM41_Explorer (format version "
            f"{version}; this version reads {FORMAT_VERSION})"
        )

    library_id = data.get("id")
    if not isinstance(library_id, str) or not _ID_RE.match(library_id):
        fail('"id" must be lower-case letters and digits, joined by "-"')
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        fail('"title" is missing')
    text = {}
    for key in _OPTIONAL_TEXT:
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            fail(f'"{key}" must be text')
        text[key] = value or None

    builtin_on = data.get("builtin_on", [])
    if not isinstance(builtin_on, list) or not all(
        isinstance(model, str) for model in builtin_on
    ):
        fail('"builtin_on" must be a list of model names')
    unknown = sorted(set(builtin_on) - set(KNOWN_MODELS))
    if unknown:
        fail(f'"builtin_on" names an unknown model: {", ".join(unknown)}')

    raw_modules = data.get("modules")
    if not isinstance(raw_modules, list) or not raw_modules:
        fail('"modules" must be a list with at least one module')
    modules = []
    seen_numbers = set()
    for raw_module in raw_modules:
        if not isinstance(raw_module, dict):
            fail('each entry of "modules" must be an object')
        number = raw_module.get("xrom")
        if not _is_int(number) or not 1 <= number <= 31:
            fail(f"module number {number!r} is not 1-31")
        if number in seen_numbers:
            fail(f"XROM {number} is listed twice")
        seen_numbers.add(number)
        page_id = raw_module.get("page_id")
        if page_id is not None and not isinstance(page_id, str):
            fail(f'XROM {number}: "page_id" must be text')
        modules.append(
            XromModule(number, _parse_functions(raw_module, number, fail), page_id)
        )

    return XromLibrary(
        id=library_id,
        title=title.strip(),
        builtin_on=frozenset(builtin_on),
        modules=tuple(modules),
        path=path,
        shipped=shipped,
        **text,
    )


def _parse_functions(raw_module: dict, number: int, fail) -> Tuple[XromFunction, ...]:
    raw_functions = raw_module.get("functions")
    if not isinstance(raw_functions, list) or not raw_functions:
        fail(f'XROM {number}: "functions" must be a list with at least one entry')
    functions = []
    seen = set()
    for raw in raw_functions:
        if not isinstance(raw, dict):
            fail(f'XROM {number}: each entry of "functions" must be an object')
        fn = raw.get("fn")
        if not _is_int(fn) or not 0 <= fn <= 63:
            fail(f"XROM {number}: function number {fn!r} is not 0-63")
        label = xrom_label(number, fn)
        if fn in seen:
            fail(f"{label} is listed twice")
        seen.add(fn)
        name = raw.get("name")
        if not isinstance(name, str) or not name or name != name.strip():
            fail(f"{label}: the name must be text, without leading or trailing spaces")
        kind = raw.get("type")
        if kind is not None and kind not in _FUNCTION_TYPES:
            fail(f'{label}: "type" must be "mcode" or "focal"')
        header = raw.get("header", False)
        if not isinstance(header, bool):
            fail(f'{label}: "header" must be true or false')
        functions.append(XromFunction(number, fn, name, kind, header))
    return tuple(sorted(functions, key=lambda f: f.function))


def load_library(path, *, shipped: bool = False) -> XromLibrary:
    """Reads and validates the library file at `path`. Raises
    XromLibraryError (naming the file) if it cannot be used."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise XromLibraryError(f"{path.name}: cannot be read ({e.strerror})") from e
    except UnicodeDecodeError as e:
        raise XromLibraryError(f"{path.name}: not UTF-8 text") from e
    except json.JSONDecodeError as e:
        raise XromLibraryError(
            f"{path.name}: not valid JSON (line {e.lineno}: {e.msg})"
        ) from e
    return parse_library(data, path.name, path=path, shipped=shipped)


def shipped_library_paths() -> List[Path]:
    return sorted(SHIPPED_DIR.glob("*.json"))


def load_libraries(
    shipped_paths: Iterable = None, user_paths: Iterable = ()
) -> Tuple[List[XromLibrary], List[XromFinding]]:
    """Every usable library, and an ERROR finding for each file that is
    not. Shipped libraries (default: those in SHIPPED_DIR) load first, so
    a user library cannot take a shipped one's id. Only a shipped library
    may be built into a model, and only one per model."""
    if shipped_paths is None:
        shipped_paths = shipped_library_paths()
    libraries: List[XromLibrary] = []
    errors: List[XromFinding] = []
    by_id: Dict[str, XromLibrary] = {}
    builtin_for: Dict[str, XromLibrary] = {}
    for shipped, paths in ((True, shipped_paths), (False, user_paths)):
        for path in paths:
            try:
                library = load_library(path, shipped=shipped)
                _check_fits(library, by_id, builtin_for)
            except XromLibraryError as e:
                errors.append(XromFinding(ERROR, str(e)))
                continue
            libraries.append(library)
            by_id[library.id] = library
            for model in library.builtin_on:
                builtin_for[model] = library
    return libraries, errors


def _check_fits(library: XromLibrary, by_id: dict, builtin_for: dict) -> None:
    where = library.path.name if library.path else library.id
    other = by_id.get(library.id)
    if other is not None:
        raise XromLibraryError(
            f'{where}: its id "{library.id}" is already used by {other.title}'
        )
    if library.builtin_on and not library.shipped:
        raise XromLibraryError(
            f"{where}: only a library that comes with DM41_Explorer can be "
            "built into a model"
        )
    for model in sorted(library.builtin_on):
        if model in builtin_for:
            raise XromLibraryError(
                f"{where}: the {model} already has a built-in library, "
                f"{builtin_for[model].title}"
            )


# -- The active set ---------------------------------------------------------


@dataclass(frozen=True)
class ActiveSet:
    """The libraries in force for a model, highest priority first, and the
    enabled ids that matched no loaded library (shown as "missing", never
    silently dropped)."""

    libraries: Tuple[XromLibrary, ...]
    missing: Tuple[str, ...] = ()


def builtin_library(
    libraries: Iterable[XromLibrary], model: str
) -> Optional[XromLibrary]:
    """The library built into `model`, or None."""
    return next((lib for lib in libraries if model in lib.builtin_on), None)


def active_set(
    libraries: Sequence[XromLibrary], model: str, enabled_ids: Iterable[str] = ()
) -> ActiveSet:
    """`model`'s built-in library, then the libraries named by
    `enabled_ids`, in that order. The built-in library is always first
    and is not repeated if it is also named; another model's built-in
    library can be enabled like any other."""
    by_id = {library.id: library for library in libraries}
    builtin = builtin_library(libraries, model)
    active = [builtin] if builtin else []
    missing = []
    for library_id in enabled_ids:
        library = by_id.get(library_id)
        if library is None:
            if library_id not in missing:
                missing.append(library_id)
        elif library not in active:
            active.append(library)
    return ActiveSet(tuple(active), tuple(missing))


# -- Conflicts --------------------------------------------------------------

_MAINFRAME_NAMES = frozenset(SINGLE_BYTE_FUNCTIONS.values())


def _join(names: Sequence[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def find_conflicts(active: Sequence[XromLibrary]) -> List[XromFinding]:
    """What clashes among `active` (highest priority first): a WARNING for
    each XROM number used by more than one library, then a NOTICE for each
    function whose name is hidden, in priority order."""
    return _number_clashes(active) + _hidden_names(active)


def _number_clashes(active: Sequence[XromLibrary]) -> List[XromFinding]:
    users: Dict[int, List[XromLibrary]] = {}
    for library in active:
        for number in library.module_numbers:
            users.setdefault(number, []).append(library)
    findings = []
    for number, libraries in sorted(users.items()):
        if len(libraries) < 2:
            continue
        first = libraries[0]
        both = "both" if len(libraries) == 2 else "all"
        winner = (
            f"{first.title}, which is built in"
            if first.builtin_on
            else f"{first.title}, which has the highest priority"
            if len(libraries) > 2
            else f"{first.title}, which has the higher priority"
        )
        findings.append(
            XromFinding(
                WARNING,
                f"{_join([lib.title for lib in libraries])} {both} use XROM "
                f"{number}. A calculator can run only one of them at a time; "
                f"XROM {number} is shown as {winner}.",
                tuple(lib.id for lib in libraries),
            )
        )
    return findings


def _hidden_names(active: Sequence[XromLibrary]) -> List[XromFinding]:
    first_holder: Dict[str, Tuple[XromLibrary, XromFunction]] = {}
    findings = []
    for library in active:
        for function in library.functions():
            if function.header:
                continue
            name = function.name
            here = f"{name} in {library.title} ({function.label})"
            reach = f"use {function.label} to reach it."
            if name in _MAINFRAME_NAMES:
                findings.append(
                    XromFinding(
                        NOTICE,
                        f"{here} is hidden by the HP-41's own {name}; {reach}",
                        (library.id,),
                    )
                )
                continue
            holder = first_holder.get(name)
            if holder is None:
                first_holder[name] = (library, function)
                continue
            other_library, other = holder
            if other_library.builtin_on:
                by = f"the built-in {name} ({other.label})"
            elif other_library is library:
                by = f"{other.label}, earlier in the same library"
            else:
                by = f"{name} in {other_library.title} ({other.label})"
            findings.append(
                XromFinding(
                    NOTICE,
                    f"{here} is hidden by {by}; {reach}",
                    tuple(dict.fromkeys((library.id, other_library.id))),
                )
            )
    return findings
