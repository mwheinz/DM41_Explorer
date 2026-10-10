'''
atomic_write_text(): replace a text file in one step, so a crash, full disk
or other failure part-way through a save leaves the existing file untouched
instead of truncated. Used for state files (Memory.to_file()) and the
preferences file (config.py).
'''

import os
import stat
import tempfile
from pathlib import Path
from typing import Union


def atomic_write_text(path: Union[str, Path], text: str):
    '''Writes `text` (UTF-8) to `path` atomically.

    The text goes to a temporary file in the same directory, is flushed to
    disk, and then replaces `path` in one step. On any failure the
    temporary file is removed and the exception propagates; an existing
    file at `path` is left exactly as it was. An existing file keeps its
    permissions, and a symlink at `path` is followed, so the file it points
    to is replaced rather than the link.'''
    target = os.path.realpath(path)
    directory, name = os.path.split(target)
    fd, temp_path = tempfile.mkstemp(
        dir=directory, prefix=f".{name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except FileNotFoundError:
            # mkstemp() creates the file owner-only (0600); give a new file
            # the permissions open() would have.
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(temp_path, mode)
        os.replace(temp_path, target)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
