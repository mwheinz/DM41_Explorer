# Contributing to DM41L Explorer

Thanks for taking a look at this project. It's an independently-developed
hobby tool, so there's no formal process — just a few notes to make
issues and pull requests easy to act on.

## Getting set up

```sh
git clone https://github.com/mwheinz/DM41L_Explorer.git
cd DM41L_Explorer
python3 -m venv dm41l-venv
source dm41l-venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
cd src
python3 -m gui.app
```

`requirements-dev.txt` pulls in the runtime dependencies plus everything
needed to test, format, and build a standalone binary. On Linux, also
install `python3-tk` if `tkinter` fails to import (e.g. `sudo apt
install python3-tk` on Debian/Ubuntu).

See the [README](README.md) for more on running from source, building a
standalone app, and what each tab of the GUI does.

## Running the tests

```sh
pytest
```

The suite runs headless on Linux CI via `xvfb-run`; if you're testing
locally on Linux without a display, you'll need `xvfb` installed and
should run tests the same way (`xvfb-run -a pytest`). Every push and
pull request against `main` also runs the full suite on Linux, macOS,
and Windows via GitHub Actions (`.github/workflows/test.yml`) — a green
check there is expected before a PR gets merged.

## Code style

The project uses [`black`](https://black.readthedocs.io/) for
formatting (already in `requirements-dev.txt`):

```sh
black src
```

There's no CI enforcement of this yet, but please run it before
submitting so diffs stay clean and reviewable.

## Logging

Every module should get its own logger, right after its imports:

```python
import logging

logger = logging.getLogger(__name__)
```

Always `__name__`, never the root logger directly — that's what makes a
log line traceable back to the module that wrote it (`gui.hex_view_tab`,
`memory.registers`, etc.) once more than a couple of modules are writing
to the same file. `gui/app.py`'s `_setup_logging()` attaches the one
handler everything shares (a rotating file at the location and level set
in Preferences); modules never configure their own handlers.

Pick a level by what the record means, not by habit:

- **DEBUG** — internal detail only useful while actively debugging (raw
  serial bytes, state-machine transitions, an exception that was caught
  and handled as an expected, no-op case — e.g. "no dump loaded yet").
- **INFO** — a normal lifecycle event a user could plausibly want to see
  in their own log: connect/disconnect, a dump loaded or saved, an XM
  file added/edited/removed, a register or flag edited, preferences
  saved.
- **WARNING** — something unexpected happened but the app recovered on
  its own and kept going (a bad log directory fell back to the home
  directory, a user typed an invalid value that got rejected).
- **ERROR** — an operation the user asked for failed and was surfaced to
  them via a dialog. **Every `messagebox.showerror(...)` call should
  have a `logger.error(...)` or `logger.exception(...)` right next to
  it** (use `logger.exception(...)` inside an `except` block so the
  traceback gets captured) — the dialog tells the user something broke,
  the log records enough detail to diagnose *why* after the fact. This
  is the rule most worth remembering: a new error dialog with no log
  call next to it is the single most common way this codebase drifts
  back into inconsistent logging.
- **CRITICAL** — reserved for failures serious enough to abort a whole
  run loop (see `serial_manager.py`'s read-thread crash handling).

The data/model layer (`src/memory/`) deliberately has no loggers of its
own — it raises (`ValueError`/`DM41MemoryError`) rather than swallowing,
so logging happens exactly once, at whichever GUI code catches the
exception and decides how to present it to the user. Keep that
separation: don't add a `logger.error()` call in `memory/*.py` right
before a `raise` — let the catching code log it instead.

## Making changes

- Keep pull requests focused — one bug fix or one feature per PR is
  much easier to review than a grab-bag of changes.
- Add or update tests for anything behavioral you change, especially in
  `src/memory/` (the data model) or `src/engine/` (the serial protocol
  state machine) — both are exercised heavily by the existing suite in
  `src/tests/`.
- If a change touches how memory, extended memory, or program memory is
  decoded, check it against [`docs/memory.md`](docs/memory.md),
  [`docs/flags.md`](docs/flags.md), and [`docs/program.md`](docs/program.md)
  — those docs describe what's confirmed vs. still-under-research, and
  should stay in sync with the code.
- Describe *why* a change is needed in the PR description, not just
  what changed — especially for anything reverse-engineered from real
  DM41L memory dumps, since the reasoning is often as valuable as the
  fix itself.

## Reporting bugs / requesting features

Open a [GitHub issue](https://github.com/mwheinz/DM41L_Explorer/issues)
with:

- What you did, what you expected, and what actually happened.
- Your OS and how you're running the app (from source vs. a built
  binary).
- A `.dm41` dump file if the issue is about how memory is decoded or
  displayed — that's usually the fastest way to reproduce it.

See the README's [Known limitations](README.md#known-limitations)
section before filing — a few gaps (key assignments/alarms decoding,
program editing) are already tracked there rather than as open issues.

## License

By contributing, you agree your changes are licensed under this
project's [Simplified BSD license](LICENSE), same as the rest of the
codebase.
