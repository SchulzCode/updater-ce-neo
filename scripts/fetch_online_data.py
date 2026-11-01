#!/usr/bin/env python3
"""Seed ``assets/online/`` with the latest copies of the app's online files.

Run automatically by every build script before packaging, so the app ships a
recent snapshot of ``slidia_manifest.xml``, ``notice.xml``, ``donors.csv`` and
``release_blocklist.csv`` and can list firmware and show notices on a launch
with no connection (see ``src/online_data.py``).

The step is best-effort by design: a build machine without network keeps the
copies already in the checkout, and only ``--require`` turns a missing file
into a non-zero exit.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src import online_data  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        default=str(REPO_ROOT / "assets" / online_data.BUNDLE_SUBDIR),
        help="folder to write the snapshots into (default: assets/online)",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=24.0,
        help="skip a file whose bundled copy is younger than this (default: 24)",
    )
    parser.add_argument(
        "--force", action="store_true", help="download every file regardless of its age"
    )
    parser.add_argument(
        "--require",
        action="store_true",
        help="fail when a file is missing after the download attempt",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    max_age = max(0.0, args.max_age_hours) * 3600.0

    failures: list[str] = []
    for name, spec in online_data.FILES.items():
        target = dest / spec.name
        if not args.force and target.is_file() and max_age:
            age = time.time() - target.stat().st_mtime
            if age < max_age:
                print(f"  = {spec.name} (bundled copy is {age / 3600:.1f} h old)")
                continue
        text = online_data._download(spec, timeout=args.timeout)
        if not text:
            if target.is_file():
                print(f"  ! {spec.name}: keeping the bundled copy (download failed)")
            else:
                print(f"  ! {spec.name}: no bundled copy and download failed")
                failures.append(spec.name)
            continue
        target.write_text(text, encoding="utf-8")
        print(f"  + {spec.name} ({len(text)} bytes)")

    if failures and args.require:
        print(f"ERROR: missing online snapshots: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
