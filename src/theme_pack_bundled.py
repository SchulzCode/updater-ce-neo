from __future__ import annotations

"""Names for the 360p Rockbox payload assets kept in ``assets/360p``.

Historically the launcher extracted a local ``assets/360p/themes-themepack``
tree at build time. That tree is large enough to dominate portable-app and
Inno Setup file placement time, so it became a single compressed archive.
The builds now ship only the small fonts pack: the themes archive is
downloaded from the Rockbox Y1 release (and refreshed weekly), then merged
over the fonts at install time, which keeps that 62 MB out of every build.
The repository copy stays for the mirror, tests and the legacy offline
payload.

Layout: the themes zip mirrors ``assets/360p/themes-themepack/.rockbox``
directly (no top-level folder). See :func:`bundled_themepack_zip` and
:func:`extract_theme_pack_zip` in ``theme_pack.py``.
"""

from pathlib import Path

#: Current bundled archive, relative to the project root / asset bundle.
BUNDLED_THEME_PACK_ZIP_NAME = "themes-themepack.zip"
