DM41_Explorer
==============

This folder is the complete application:

  dm41explorer (or dm41explorer.exe on Windows)     <- run this
  _internal/                                        <- required support
                                                        files (Python
                                                        runtime, bundled
                                                        libraries, docs)
  library/                                          <- sample .dm41
                                                        programs, browse
                                                        or copy freely

_internal/ isn't optional or extra -- it's not safe to delete it, and if
you move the app somewhere else (a different folder, a USB drive, etc.),
move it along with the executable rather than leaving it behind. This is
standard PyInstaller "onedir" packaging, not something specific to this
app.

library/ is just data -- sample programs to load via File > Open State,
or Import into an existing state. Nothing else in the app depends on it,
so it's fine to browse, copy files out of, or delete.

Full usage instructions, known limitations, and how to get past the
Windows SmartScreen "unknown publisher" warning on this unsigned build
are in the project's README on GitHub:
https://github.com/mwheinz/DM41_Explorer

License
-------

DM41_Explorer is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License, version 3, as published by
the Free Software Foundation. It is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

The full license text is in the LICENSE file next to this one. The source code
for this build is at the GitHub address above.
