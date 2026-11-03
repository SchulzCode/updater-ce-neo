# Mirror Housekeeping

Private macOS control plane for the `y1-community` firmware mirrors. It verifies
that the published `-macos.zip` assets use the current layout:

```text
Install Firmware.app/
Firmware/
  fw_package.ini
  MT6572_Android_scatter.txt
  ...
README.txt
```

The app never downloads or rebuilds a gigabyte archive locally. It reads each
ZIP's central directory over HTTP range requests, then dispatches the mirror's
own `mirror.yml` GitHub Actions workflow when a repair or sync is needed. The
existing packager remains the only thing that writes release assets.

## Start

From the repository root:

```bash
./housekeeping/run.sh app
```

or, using the build virtualenv directly:

```bash
.venv-build/bin/python -m housekeeping app
```

The **Check** button inspects each mirror's newest release. **Deep Check**
inspects every published macOS asset. **Repair macOS** dispatches
`platforms=macos&force=true` only for the tags whose assets failed the layout
check. **Update Stale** dispatches a normal mirror sync for indexes that are
older than the configured age. A run already in progress is skipped, so the app
cannot queue duplicate repairs.

## Authentication

The app resolves a token in this order:

1. `GH_TOKEN` or `GITHUB_TOKEN`
2. the macOS Keychain item `Innioasis Mirror Housekeeping`
3. `gh auth token` (the existing CLI login)
4. `~/Library/Application Support/Innioasis Mirror Housekeeping/token`
5. a token pasted into Settings

On this computer, the existing `gh auth login` is enough. On another computer,
either run `gh auth login`, or open **Settings → Replace token** and paste a
fine-grained token with **Contents: Read and write** and **Actions: Read and
write** on the mirror repositories. Settings stores it in the Keychain (or a
`0600` file if Keychain is unavailable). No token is written into this
repository, logs, or command output.

## CLI

```bash
./housekeeping/run.sh status                       # quick status
./housekeeping/run.sh status --deep                # every macOS asset
./housekeeping/run.sh check --deep --update        # report and dispatch stale syncs
./housekeeping/run.sh repair --dry-run             # show what would be rebuilt
./housekeeping/run.sh repair                       # deep-check and dispatch repairs
./housekeeping/run.sh update --repo y1-community/rondo-mirror
./housekeeping/run.sh token status
./housekeeping/run.sh token set
```

`status` exits `1` when an index is stale, missing, or has a broken macOS
asset. `repair` targets the broken tags and leaves Windows/Linux assets alone.

## Schedule

Install a launchd job that checks every six hours, dispatches stale syncs, and
posts a notification if anything still needs attention:

```bash
./housekeeping/run.sh schedule install --interval 6
./housekeeping/run.sh schedule status
./housekeeping/run.sh schedule remove
```

Add `--check-only` to avoid dispatching updates, or `--auto-repair --deep` to
repair every broken macOS layout automatically. The job lives at
`~/Library/LaunchAgents/app.innioasis.mirror-housekeeping.plist`; output goes to
`~/Library/Logs/InnioasisMirrorHousekeeping.log`. The mirror repositories also
have their own six-hour GitHub Actions schedules, so this local job is a
watchdog and fallback, not the primary updater.

## Build a double-clickable app

```bash
./housekeeping/build_macos_app.sh
open "housekeeping/dist/Mirror Housekeeping.app"
```

The build uses PyInstaller from `.venv-build/` and writes only under
`housekeeping/dist/` and `housekeeping/build/`, both ignored by git. The app is
private and is not part of the Updater CE release pipeline.

## Tests

```bash
.venv-build/bin/python -m pytest housekeeping/tests/test_core.py -q
```
