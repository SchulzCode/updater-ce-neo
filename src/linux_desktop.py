"""Linux desktop compatibility for the bundled Qt front end.

The same AppImage has to start under GNOME, KDE Plasma, Xfce, Cinnamon, MATE,
LXQt, Budgie and tiling compositors, on Wayland or X11. Qt selects a platform
plugin by itself, but a packaged build inherits the session's choice:
``QT_QPA_PLATFORM=wayland`` against a build without that plugin aborts with
"Could not load the Qt platform plugin", a ``QT_QPA_PLATFORMTHEME`` naming a
plugin the bundle does not ship is reported as a missing theme, and an input
method that cannot reach its daemon (ibus on some Ubuntu-based X11 sessions)
is a reported startup-crash trigger.

:func:`prepare_qt_environment` runs before ``QApplication`` is constructed and
only edits the process environment on Linux. It corrects values the bundle
cannot honour, adds a Wayland/X11 fallback list when both plugins ship, and
drops host theme names. ``INNIOASIS_SAFE_MODE=1`` selects the conservative
stack the launchers retry with after a startup crash: X11 when available
(XWayland covers Wayland desktops), the compose input context instead of ibus,
and software OpenGL. ``INNIOASIS_NO_QT_ENV_FIX=1`` leaves everything untouched.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def _enabled(env, key: str) -> bool:
    return str(env.get(key, "")).strip().lower() in _TRUTHY


def installed_plugin_dir() -> Path | None:
    """Return this process's PySide6 Qt plugin directory, when it has one."""
    try:
        import PySide6

        root = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins"
    except Exception:
        return None
    return root if (root / "platforms").is_dir() else None


def _names(directory: Path, prefix: str, suffix: str) -> set[str] | None:
    """Plugin names in *directory* whose file name matches prefix/suffix."""
    if not directory.is_dir():
        return None
    names: set[str] = set()
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        middle = name[len(prefix) : len(name) - len(suffix)]
        if name.startswith(prefix) and name.endswith(suffix) and middle:
            names.add(middle)
    return names


def _platform_names(plugin_dir: Path | None) -> set[str] | None:
    if plugin_dir is None:
        return None
    return _names(Path(plugin_dir) / "platforms", "libq", ".so")


def _theme_names(plugin_dir: Path | None) -> set[str] | None:
    if plugin_dir is None:
        return None
    return _names(Path(plugin_dir) / "platformthemes", "libq", ".so")


def _input_context_names(plugin_dir: Path | None) -> set[str] | None:
    if plugin_dir is None:
        return None
    directory = Path(plugin_dir) / "platforminputcontexts"
    names = _names(directory, "lib", "platforminputcontextplugin.so")
    if names is None:
        return None
    if (directory / "libqtvirtualkeyboardplugin.so").is_file():
        names.add("qtvirtualkeyboard")
    return names


def _session_order(env, platforms: set[str]) -> list[str]:
    """Display plugins this session can use, preferred first."""
    order: list[str] = []
    if env.get("WAYLAND_DISPLAY") and "wayland" in platforms:
        order.append("wayland")
    if env.get("DISPLAY") and "xcb" in platforms:
        order.append("xcb")
    return order


def prepare_qt_environment(
    plugin_dir: Path | str | None = None,
    environ=None,
    platform_name: str | None = None,
) -> list[str]:
    """Make this process's Qt environment match the plugins the build ships.

    Must run before ``QApplication`` exists. Returns the applied changes,
    ``"KEY=value"`` / ``"KEY=<unset>"``, for logs and tests; never raises.
    """
    if platform_name is None:
        platform_name = sys.platform
    if not str(platform_name).startswith("linux"):
        return []
    env = os.environ if environ is None else environ
    if _enabled(env, "INNIOASIS_NO_QT_ENV_FIX"):
        return []
    if isinstance(plugin_dir, str):
        plugin_dir = Path(plugin_dir)

    changes: list[str] = []

    def set_value(key: str, value: str) -> None:
        if env.get(key) != value:
            env[key] = value
            changes.append(f"{key}={value}")

    def clear(key: str) -> None:
        if key in env:
            env.pop(key, None)
            changes.append(f"{key}=<unset>")

    platforms = _platform_names(plugin_dir)
    safe = _enabled(env, "INNIOASIS_SAFE_MODE")

    if safe:
        if platforms is not None:
            if env.get("DISPLAY") and "xcb" in platforms:
                chosen = "xcb"
            elif env.get("WAYLAND_DISPLAY") and "wayland" in platforms:
                chosen = "wayland"
            else:
                chosen = ";".join(_session_order(env, platforms))
            if chosen:
                set_value("QT_QPA_PLATFORM", chosen)
        clear("QT_QPA_PLATFORMTHEME")
        clear("QT_STYLE_OVERRIDE")
        contexts = _input_context_names(plugin_dir)
        if contexts is not None and "compose" in contexts:
            set_value("QT_IM_MODULE", "compose")
        else:
            clear("QT_IM_MODULE")
        # Qt's xcb integration consults XMODIFIERS when QT_IM_MODULE is unset;
        # safe mode must not fall back into the ibus plugin it just avoided.
        clear("XMODIFIERS")
        set_value("QT_OPENGL", "software")
        set_value("LIBGL_ALWAYS_SOFTWARE", "1")
    else:
        requested = [
            item.strip()
            for item in str(env.get("QT_QPA_PLATFORM", "")).split(";")
            if item.strip()
        ]
        if requested and platforms is not None:
            kept = [item for item in requested if item in platforms]
            if kept != requested:
                logger.info(
                    "This build does not ship the Qt platform plugin(s) %s",
                    ", ".join(item for item in requested if item not in platforms),
                )
                requested = kept
        if requested:
            set_value("QT_QPA_PLATFORM", ";".join(requested))
        elif platforms is not None:
            order = _session_order(env, platforms)
            if order:
                set_value("QT_QPA_PLATFORM", ";".join(order))
            elif env.get("QT_QPA_PLATFORM"):
                clear("QT_QPA_PLATFORM")

        themes = _theme_names(plugin_dir)
        theme = env.get("QT_QPA_PLATFORMTHEME")
        if themes is not None and theme and theme not in themes:
            logger.info("Dropping the unavailable Qt platform theme %r", theme)
            clear("QT_QPA_PLATFORMTHEME")

    if changes:
        logger.info("Linux Qt environment adjusted: %s", ", ".join(changes))
    return changes
