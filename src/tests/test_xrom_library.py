"""Tests for memory/xrom_library.py: loading and validating XROM library
files, the active set, and the conflict report (GitHub issue #50).

The shipped files themselves are checked here too: they all load, each
model has exactly one built-in library, and builtin_xrom_names() -- what
the device profiles and the instruction registry are built from (issue
#51) -- reads them correctly. test_dm41x_xroms.py pins the built-in
libraries' contents to what real DM41L and DM41X states confirm.
"""

import copy
import json

import pytest

from memory import xrom_library
from memory.xrom_library import (
    ERROR,
    NOTICE,
    WARNING,
    XromLibraryError,
    active_set,
    builtin_library,
    builtin_xrom_names,
    find_conflicts,
    load_libraries,
    load_library,
    name_search_order,
    parse_library,
    shipped_library_paths,
)

# Ids of the pacs that share XROM 19, highest priority first when the
# shipped libraries are enabled in id order.
XROM_19 = ("aviation", "clinical", "securities", "structural")


@pytest.fixture(scope="module")
def shipped():
    libraries, errors = load_libraries()
    assert errors == []
    return libraries


def _by_id(libraries):
    return {library.id: library for library in libraries}


def _pairs(library):
    """(byte1, byte2) -> name for every function that isn't a header,
    worked out here independently of XromFunction.code."""
    return {
        (0xA0 + f.module // 4, (f.module % 4) << 6 | f.function): f.name
        for f in library.functions()
        if not f.header
    }


def _minimal(**changes):
    data = {
        "format": "dm41-xrom-library",
        "format_version": 1,
        "id": "mine",
        "title": "My ROM",
        "builtin_on": [],
        "modules": [
            {
                "xrom": 11,
                "functions": [
                    {"fn": 0, "name": "MY ROM 1A", "header": True},
                    {"fn": 1, "name": "FOO", "type": "mcode"},
                    {"fn": 2, "name": "BAR", "type": "focal"},
                ],
            }
        ],
    }
    data.update(changes)
    return data


def _write(tmp_path, data, name=None):
    path = tmp_path / f"{name or data['id']}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# -- The shipped files ------------------------------------------------------


def test_every_shipped_file_loads(shipped):
    assert len(shipped) == len(shipped_library_paths()) >= 23
    assert all(library.shipped for library in shipped)
    for library in shipped:
        assert library.id == library.path.stem


def test_each_model_has_exactly_its_own_builtin_library(shipped):
    builtin = {lib.id: sorted(lib.builtin_on) for lib in shipped if lib.builtin_on}
    assert builtin == {"hp41cx": ["DM41L"], "dm41x-cx": ["DM41X"]}
    assert builtin_library(shipped, "DM41L").id == "hp41cx"
    assert builtin_library(shipped, "DM41X").id == "dm41x-cx"


def test_builtin_xrom_names_reads_each_models_library(shipped):
    libraries = _by_id(shipped)
    assert builtin_xrom_names("DM41L") == _pairs(libraries["hp41cx"])
    assert builtin_xrom_names("DM41X") == _pairs(libraries["dm41x-cx"])
    # The DM41X's library includes the DM41L's, so merged they are the
    # DM41X's.
    assert builtin_xrom_names() == builtin_xrom_names("DM41X")


def test_builtin_xrom_names_returns_a_fresh_dict():
    names = builtin_xrom_names("DM41L")
    names.clear()
    assert len(builtin_xrom_names("DM41L")) == 95


def test_builtin_xrom_names_leaves_out_section_headers_unless_asked(monkeypatch):
    library = parse_library(_minimal())
    monkeypatch.setattr(
        xrom_library, "_shipped_builtins", lambda: {"DM41L": library, "DM41X": library}
    )
    assert builtin_xrom_names() == {(0xA2, 0xC1): "FOO", (0xA2, 0xC2): "BAR"}
    assert builtin_xrom_names("DM41X", headers=True) == {
        (0xA2, 0xC0): "MY ROM 1A",
        (0xA2, 0xC1): "FOO",
        (0xA2, 0xC2): "BAR",
    }


def test_builtin_libraries_that_disagree_on_a_name_are_refused(monkeypatch):
    """One function must not have two names depending on the mode."""
    first = parse_library(_minimal())
    data = _minimal(id="other", title="Other")
    data["modules"][0]["functions"][1]["name"] = "FOO2"
    second = parse_library(data)
    monkeypatch.setattr(
        xrom_library, "_shipped_builtins", lambda: {"DM41L": first, "DM41X": second}
    )
    with pytest.raises(XromLibraryError, match="XROM 11,01 is FOO in one"):
        builtin_xrom_names()


def test_xrom_function_code_matches_the_encoding():
    function = parse_library(_minimal()).modules[0].functions[1]  # XROM 11,01
    assert function.code == (0xA2, 0xC1)


def test_a_library_spanning_two_xrom_numbers(shipped):
    advantage = _by_id(shipped)["advantage"]
    assert advantage.title == "Advantage Pac"
    assert advantage.module_numbers == (22, 24)
    solve = next(f for f in advantage.functions() if f.name == "SOLVE")
    assert (solve.module, solve.function, solve.label) == (24, 3, "XROM 24,03")


# -- Validation ---------------------------------------------------------------


def test_a_minimal_library_parses():
    library = parse_library(_minimal())
    assert (library.id, library.title, library.module_numbers) == (
        "mine",
        "My ROM",
        (11,),
    )
    header, foo, bar = library.functions()
    assert header.header and not foo.header
    assert (foo.label, foo.type, bar.type) == ("XROM 11,01", "mcode", "focal")


def test_unknown_fields_are_ignored():
    data = _minimal(colour="blue")
    data["modules"][0]["functions"][1]["notes"] = "anything"
    assert parse_library(data).id == "mine"


def test_functions_are_put_in_number_order():
    data = _minimal()
    data["modules"][0]["functions"].reverse()
    assert [f.function for f in parse_library(data).functions()] == [0, 1, 2]


def _break(path, value):
    """_minimal() with the field at `path` (keys and indexes) set to
    `value`, or removed if `value` is _DELETE."""
    data = copy.deepcopy(_minimal())
    *parents, last = path
    target = data
    for key in parents:
        target = target[key]
    if value is _DELETE:
        del target[last]
    else:
        target[last] = value
    return data


_DELETE = object()
_FN = ("modules", 0, "functions", 1)


@pytest.mark.parametrize(
    "path, value, expected",
    [
        (("format",), "something-else", "not an XROM library"),
        (("format_version",), 2, "newer version of DM41_Explorer"),
        (("format_version",), 0, "format_version"),
        (("format_version",), True, "format_version"),
        (("id",), "My ROM", '"id"'),
        (("id",), "-mine", '"id"'),
        (("title",), "  ", '"title" is missing'),
        (("title",), _DELETE, '"title" is missing'),
        (("version",), 3, '"version" must be text'),
        (("builtin_on",), ["DM41Q"], "unknown model: DM41Q"),
        (("builtin_on",), "DM41X", '"builtin_on" must be a list'),
        (("modules",), [], "at least one module"),
        (("modules", 0, "xrom"), 0, "module number 0 is not 1-31"),
        (("modules", 0, "xrom"), 32, "module number 32 is not 1-31"),
        (("modules", 0, "xrom"), True, "is not 1-31"),
        (("modules", 0, "page_id"), 7, '"page_id" must be text'),
        (("modules", 0, "functions"), [], "at least one entry"),
        ((*_FN, "fn"), 64, "function number 64 is not 0-63"),
        ((*_FN, "fn"), 2, "XROM 11,02 is listed twice"),
        ((*_FN, "name"), " FOO", "without leading or trailing spaces"),
        ((*_FN, "name"), "", "the name must be text"),
        ((*_FN, "type"), "basic", '"type" must be "mcode" or "focal"'),
        ((*_FN, "header"), "yes", '"header" must be true or false'),
    ],
)
def test_a_bad_library_is_rejected_with_a_reason(path, value, expected):
    with pytest.raises(XromLibraryError, match="^mine.json: ") as caught:
        parse_library(_break(path, value), "mine.json")
    assert expected in str(caught.value)


def test_the_same_xrom_number_twice_in_one_library_is_rejected():
    data = _minimal()
    data["modules"].append(copy.deepcopy(data["modules"][0]))
    with pytest.raises(XromLibraryError, match="XROM 11 is listed twice"):
        parse_library(data)


def test_a_file_that_is_not_json_names_the_line(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{\n  "format": "dm41-xrom-library",\n  oops\n}\n')
    with pytest.raises(
        XromLibraryError, match=r"^broken.json: not valid JSON \(line 3"
    ):
        load_library(path)


def test_a_file_that_is_not_utf8_is_rejected(tmp_path):
    path = tmp_path / "latin.json"
    path.write_bytes(b'{"title": "\xe9"}')
    with pytest.raises(XromLibraryError, match="not UTF-8"):
        load_library(path)


def test_a_missing_file_is_rejected(tmp_path):
    with pytest.raises(XromLibraryError, match="^gone.json: cannot be read"):
        load_library(tmp_path / "gone.json")


# -- Loading several ---------------------------------------------------------


def test_a_bad_file_is_reported_and_the_rest_still_load(tmp_path):
    good = _write(tmp_path, _minimal())
    bad = _write(tmp_path, _minimal(id="bad", format_version=9))

    libraries, errors = load_libraries([], [bad, good])

    assert [lib.id for lib in libraries] == ["mine"]
    assert not libraries[0].shipped
    assert [e.level for e in errors] == [ERROR]
    assert errors[0].message.startswith("bad.json: written for a newer version")


def test_a_user_library_cannot_take_a_shipped_id(tmp_path, shipped):
    path = _write(tmp_path, _minimal(id="math"))

    libraries, errors = load_libraries(shipped_library_paths(), [path])

    assert _by_id(libraries)["math"].shipped
    assert len(libraries) == len(shipped)
    assert errors[0].message == ('math.json: its id "math" is already used by Math Pac')


def test_a_user_library_cannot_be_built_in(tmp_path):
    path = _write(tmp_path, _minimal(builtin_on=["DM41X"]))
    libraries, errors = load_libraries([], [path])
    assert libraries == []
    assert "only a library that comes with DM41_Explorer" in errors[0].message


def test_a_model_has_only_one_builtin_library(tmp_path):
    first = _write(tmp_path, _minimal(id="one", builtin_on=["DM41L"]))
    second = _write(tmp_path, _minimal(id="two", title="Two", builtin_on=["DM41L"]))

    libraries, errors = load_libraries([first, second])

    assert [lib.id for lib in libraries] == ["one"]
    assert "the DM41L already has a built-in library, My ROM" in errors[0].message


# -- The active set ----------------------------------------------------------


def test_the_builtin_library_comes_first_then_the_users_order(shipped):
    active = active_set(shipped, "DM41X", ["math", "advantage", "dm41x-cx"])
    assert [lib.id for lib in active.libraries] == ["dm41x-cx", "math", "advantage"]
    assert active.missing == ()


def test_each_mode_gets_its_own_builtin_library(shipped):
    assert [lib.id for lib in active_set(shipped, "DM41L").libraries] == ["hp41cx"]
    assert [lib.id for lib in active_set(shipped, "DM41X").libraries] == ["dm41x-cx"]


def test_unknown_ids_are_kept_as_missing_once(shipped):
    active = active_set(shipped, "DM41L", ["gone", "math", "gone", "math"])
    assert [lib.id for lib in active.libraries] == ["hp41cx", "math"]
    assert active.missing == ("gone",)


def test_a_model_without_a_builtin_library_gets_just_the_enabled_ones(tmp_path):
    libraries, _ = load_libraries([], [_write(tmp_path, _minimal())])
    assert [lib.id for lib in active_set(libraries, "DM41X", ["mine"]).libraries] == [
        "mine"
    ]


# -- Conflicts ---------------------------------------------------------------


@pytest.fixture(scope="module")
def everything(shipped):
    """Every shipped pac enabled in DM41X mode, in id order."""
    ids = sorted(lib.id for lib in shipped if not lib.builtin_on)
    return active_set(shipped, "DM41X", ids).libraries


def test_the_builtin_library_alone_has_no_conflicts(shipped):
    assert find_conflicts(active_set(shipped, "DM41X").libraries) == []
    assert find_conflicts(active_set(shipped, "DM41L").libraries) == []


def test_shared_xrom_numbers_in_the_shipped_set(everything):
    warnings = [f for f in find_conflicts(everything) if f.level == WARNING]
    assert [f.library_ids for f in warnings] == [("games", "ppc"), XROM_19]
    assert warnings[0].message == (
        "Games Pac and PPC ROM both use XROM 10. A calculator can run only "
        "one of them at a time; XROM 10 is shown as Games Pac, which has the "
        "higher priority."
    )
    assert warnings[1].message.startswith(
        "Aviation Pac, Clinical Lab & Nuclear Medicine Pac, Securities Pac "
        "and Structural Analysis Pac all use XROM 19."
    )
    assert warnings[1].message.endswith(
        "XROM 19 is shown as Aviation Pac, which has the highest priority."
    )


def test_warnings_come_before_notices(everything):
    levels = [f.level for f in find_conflicts(everything)]
    assert levels == sorted(levels, key=[WARNING, NOTICE].index)


def _notices(active):
    return [f for f in find_conflicts(active) if f.level == NOTICE]


def test_a_plugins_size_hides_the_builtin_one(everything):
    """On a real DM41X, ASN "SIZE?" with Real Estate plugged in took Real
    Estate's: the calculator searches the plug-in pages before the
    Extended Functions ROM (issue #52). With several pacs active, the
    first in priority order (Games) hides the built-in one and the rest."""
    notices = [f for f in _notices(everything) if "SIZE?" in f.message.split(" (")[0]]
    builtin = [f for f in notices if f.message.startswith("The built-in SIZE?")]
    assert [f.library_ids for f in builtin] == [("dm41x-cx", "games")]
    assert builtin[0].message == (
        "The built-in SIZE? (XROM 25,44) is hidden by SIZE? in Games Pac "
        "(XROM 10,12), which the calculator finds first. In a program "
        "listing, SIZE? still means the built-in one; elsewhere, use "
        "XROM 25,44 to reach it."
    )
    others = [f for f in notices if f not in builtin]
    assert sorted(f.library_ids for f in others) == [
        ("real-estate", "games"),
        ("stress", "games"),
        ("structural", "games"),
    ]


def test_with_one_pac_its_size_hides_the_builtin(shipped):
    active = active_set(shipped, "DM41X", ["real-estate"]).libraries
    (notice,) = [f for f in _notices(active) if "SIZE?" in f.message]
    assert notice.library_ids == ("dm41x-cx", "real-estate")


def test_names_shared_with_mainframe_functions(everything):
    """A pac's function wins by name over the mainframe's (ASN "LOG" on a
    real DM41X), but not in a program listing, so it is a notice of its
    own rather than "hidden". Where two pacs share the name, the later
    one is hidden by the earlier."""
    shared = {
        (f.message.split(" in ")[0], f.library_ids)
        for f in _notices(everything)
        if "the HP-41's own" in f.message
    }
    assert shared == {
        ("*", ("clinical",)),
        ("+", ("standard",)),
        ("-", ("standard",)),
        ("/", ("standard",)),
        ("LOG", ("standard",)),
        ("PI", ("circuit",)),
    }
    log = next(f for f in _notices(everything) if f.message.startswith("LOG in"))
    assert log.message == (
        "LOG in Standard Applications Pac (XROM 05,45) has the same name as "
        "the HP-41's own LOG. By name, the calculator uses Standard "
        "Applications Pac's; in a program listing, LOG still means the "
        "HP-41's own, so write XROM 05,45 there."
    )
    stars = [
        f.library_ids for f in _notices(everything) if f.message.startswith("* in")
    ]
    assert stars == [("clinical",), ("home-mgt", "clinical"), ("standard", "clinical")]


def test_the_time_rom_is_searched_before_the_plugins(shipped):
    """The built-in Time ROM (XROM 26, page 5) comes before the plug-in
    pages, so a pac reusing one of its names is the one hidden."""
    clash = parse_library(
        _minimal(modules=[{"xrom": 11, "functions": [{"fn": 1, "name": "TIME"}]}])
    )
    (notice,) = _notices([builtin_library(shipped, "DM41X"), clash])
    assert notice.message == (
        "TIME in My ROM (XROM 11,01) is hidden by the built-in TIME "
        "(XROM 26,28), which the calculator finds first; use XROM 11,01 to "
        "reach it."
    )


def test_name_search_order(shipped):
    active = active_set(shipped, "DM41X", ["math", "advantage"]).libraries
    assert [(lib.id, module.xrom) for lib, module in name_search_order(active)] == [
        ("dm41x-cx", 26),
        ("math", 1),
        ("advantage", 22),
        ("advantage", 24),
        ("dm41x-cx", 25),
    ]


def _solve(active):
    return [f.message for f in _notices(active) if f.message.startswith("SOLVE in")]


def test_priority_decides_which_solve_is_hidden(shipped):
    math_first = active_set(shipped, "DM41X", ["math", "advantage"]).libraries
    assert _solve(math_first) == [
        "SOLVE in Advantage Pac (XROM 24,03) is hidden by SOLVE in Math Pac "
        "(XROM 01,09), which the calculator finds first; use XROM 24,03 to "
        "reach it."
    ]
    advantage_first = active_set(shipped, "DM41X", ["advantage", "math"]).libraries
    assert _solve(advantage_first) == [
        "SOLVE in Math Pac (XROM 01,09) is hidden by SOLVE in Advantage Pac "
        "(XROM 24,03), which the calculator finds first; use XROM 01,09 to "
        "reach it."
    ]


def test_only_active_libraries_are_compared(shipped):
    assert _solve(active_set(shipped, "DM41X", ["math"]).libraries) == []


def test_headers_never_hide_or_get_hidden():
    first = parse_library(_minimal())
    # The same "MY ROM 1A" header, and the same FOO and BAR, in both.
    second = parse_library(_minimal(id="second", title="Second"))
    messages = [f.message for f in _notices([first, second])]
    assert [m.split(" in ")[0] for m in messages] == ["FOO", "BAR"]


def test_a_name_twice_in_one_library_is_a_notice():
    data = _minimal()
    data["modules"][0]["functions"][2]["name"] = "FOO"
    (finding,) = find_conflicts([parse_library(data)])
    assert finding.message == (
        "FOO in My ROM (XROM 11,02) is hidden by XROM 11,01, earlier in the "
        "same library, which the calculator finds first; use XROM 11,02 to "
        "reach it."
    )
    assert finding.library_ids == ("mine",)


def test_reusing_a_builtin_xrom_number_is_a_warning(shipped):
    clash = parse_library(
        _minimal(modules=[{"xrom": 26, "functions": [{"fn": 60, "name": "ZZZ"}]}])
    )
    builtin = builtin_library(shipped, "DM41X")
    (finding,) = find_conflicts([builtin, clash])
    assert finding.level == WARNING
    assert finding.library_ids == ("dm41x-cx", "mine")
    assert finding.message.endswith(
        "XROM 26 is shown as HP-41CX Extended Functions and Time, DM41X "
        "version, which is built in."
    )
