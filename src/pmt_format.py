"""Remember firmware that SP Flash Tool's Download mode cannot install.

A package can declare ``PMT_format_needed="true"`` in ``slidia_manifest.xml``
when its partition table differs from the installed one. When a package does
not declare it, SP Flash Tool may still report error 8502 ("PMT changed for
the ROM; it must be downloaded"). Once that happens, the app records the
software so later installs of the same software use Format All + Download
without trial-and-error, and Keep Data stays unavailable.

The record lives in QSettings, keyed by repository/model (or name/model for a
local package), because ``PMT_format_needed`` is a property of the firmware's
partition layout rather than of one downloaded file.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from PySide6.QtCore import QSettings

logger = logging.getLogger(__name__)

PMT_FORMAT_KEY = "flash/pmt_format_required"


def _norm(value) -> str:
    return str(value or "").strip().casefold()


def _keys(pkg=None, *, model: str = "", software: str = "") -> set[str]:
    model = str(getattr(pkg, "model", "") or model or "").strip().upper()
    repo = _norm(getattr(pkg, "repo", "") or "")
    name = _norm(getattr(pkg, "name", "") or software or "")
    keys: set[str] = set()
    if model and repo:
        keys.add(f"repo:{repo}|{model}")
    if model and name:
        keys.add(f"name:{name}|{model}")
    return keys


def _stored(settings: QSettings) -> set[str]:
    raw = settings.value(PMT_FORMAT_KEY, [])
    if isinstance(raw, str):
        values: Iterable = [raw]
    elif raw is None:
        values = []
    else:
        values = raw
    return {str(value) for value in values if str(value).strip()}


def pmt_format_required(
    pkg=None,
    *,
    model: str = "",
    software: str = "",
    settings: Optional[QSettings] = None,
) -> bool:
    """True when this firmware must use Format All + Download.

    The manifest's own flag is authoritative. A previously observed error 8502
    is kept as a fallback for packages whose manifest entry has not been
    updated yet.
    """
    if bool(getattr(pkg, "pmt_format_needed", False)):
        return True
    wanted = _keys(pkg, model=model, software=software)
    if not wanted:
        return False
    s = settings or QSettings("innioasis", "updater")
    return bool(wanted.intersection(_stored(s)))


def mark_pmt_format_required(
    pkg=None,
    *,
    model: str = "",
    software: str = "",
    settings: Optional[QSettings] = None,
) -> bool:
    """Record that this firmware hit error 8502.

    Returns True when a new key was added, False when it was already recorded
    or there was not enough information to identify the firmware.
    """
    wanted = _keys(pkg, model=model, software=software)
    if not wanted:
        return False
    s = settings or QSettings("innioasis", "updater")
    stored = _stored(s)
    added = wanted - stored
    if not added:
        return False
    s.setValue(PMT_FORMAT_KEY, sorted(stored | added))
    s.sync()
    logger.warning(
        "Firmware requires Format All + Download after SP Flash Tool error 8502: %s",
        ", ".join(sorted(added)),
    )
    return True
