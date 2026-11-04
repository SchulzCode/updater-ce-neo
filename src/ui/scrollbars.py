"""Scroll areas that keep the platform's own scrollbars.

macOS and Linux keep the policy the caller asked for, drawn by the platform
style. Windows hides scrollbars outright (``ScrollBarAlwaysOff``): a matching
application stylesheet forces classic arrow bars, and those are not repainted
here. Nothing in this module adds a ``QScrollBar`` stylesheet rule.

The one thing that breaks it: giving a scroll area its own stylesheet. A widget
with a local stylesheet is drawn by ``QStyleSheetStyle`` instead of the platform
style, so the host's floating bar silently becomes a classic always-visible one
that also reserves layout space. That is why transparency is applied through the
palette and widget attributes here — the same result, drawn by the OS.

So: use these helpers instead of ``setStyleSheet`` on a scroll area (or on a
scrollbar), and the platform's scrollbars are simply what you get.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QFrame, QWidget

__all__ = [
    "configure_scroll_area",
    "ensure_native_scrolling",
    "make_transparent",
    "refresh_scrollbar_palettes",
]


def _theme_role(role: QPalette.ColorRole, fallback: str) -> QColor:
    """Opaque theme color. A zero-alpha black is painted as a solid black plate."""
    app = QApplication.instance()
    color = app.palette().color(role) if app is not None else QColor(fallback)
    if not color.isValid() or color.alpha() == 0:
        color = QColor(fallback)
    color = QColor(color)
    color.setAlpha(255)
    return color


def _paint_theme_surface(widget: QWidget) -> None:
    if widget is None:
        return
    base = _theme_role(QPalette.ColorRole.Base, "#ffffff")
    window = _theme_role(QPalette.ColorRole.Window, "#ffffff")
    text = _theme_role(QPalette.ColorRole.Text, "#1a1a1a")
    window_text = _theme_role(QPalette.ColorRole.WindowText, "#1a1a1a")
    palette = widget.palette()
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        palette.setColor(group, QPalette.ColorRole.Base, base)
        palette.setColor(group, QPalette.ColorRole.Window, window)
        palette.setColor(group, QPalette.ColorRole.Text, text)
        palette.setColor(group, QPalette.ColorRole.WindowText, window_text)
    widget.setPalette(palette)


def _opaque_scrollbar_palette() -> QPalette | None:
    """The current application palette with every role made opaque."""
    app = QApplication.instance()
    if app is None:
        return None
    # Roles are copied one by one: ``app.palette()`` carries no resolve bits of
    # its own, and a bare ``setPalette`` copy would leave the bar inheriting the
    # transparent roles from the scroll area.
    theme = QPalette(app.palette())
    roles = (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.Button,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.Light,
        QPalette.ColorRole.Midlight,
        QPalette.ColorRole.Mid,
        QPalette.ColorRole.Dark,
        QPalette.ColorRole.Shadow,
        QPalette.ColorRole.Highlight,
        QPalette.ColorRole.HighlightedText,
    )
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role in roles:
            color = theme.color(group, role)
            if not color.isValid() or color.alpha() == 0:
                color = _theme_role(role, "#ffffff")
            color = QColor(color)
            color.setAlpha(255)
            theme.setColor(group, role, color)
    return theme


def refresh_scrollbar_palettes(area: QAbstractScrollArea) -> None:
    """Re-take the bars' colours from the current theme.

    A bar keeps the palette it was given when the area was built, so a theme
    switch after that (for example a dark Plasma theme applied once the window
    exists) left the groove and track in the old light colours.
    """
    theme = _opaque_scrollbar_palette()
    if theme is None:
        return
    for bar in (area.verticalScrollBar(), area.horizontalScrollBar()):
        if bar is None:
            continue
        bar.setPalette(theme)
        bar.setAutoFillBackground(False)


def _keep_scrollbars_opaque(area: QAbstractScrollArea) -> None:
    """Give the bars the theme's solid roles after the viewport went transparent.

    A scrollbar is a child of the scroll area, so it inherits the transparent
    Base/Window the viewport needs. The style then has no colour to paint the
    groove, track and thumb bevel with, and that strip of the window is left
    unpainted: on a solid window it showed the desktop behind the app (in the
    report, the Plasma window under the settings list) as a gradient band.
    """
    refresh_scrollbar_palettes(area)


class _ViewportFillKeeper(QObject):
    """Stop the viewport painting its Base plate once Qt re-polishes the view.

    ``QScrollArea`` re-enables the viewport's own background fill — and hands it
    back the application palette — when it polishes the widget, so the
    transparency ``make_transparent`` applies at construction was undone by the
    first show. The page then painted a solid ``QPalette::Base`` block behind its
    cards: pure white in light mode, near-black in dark, spanning the whole
    content column. Re-asserting the fill on the events that precede that paint
    is what keeps the page reading as one surface.
    """

    def __init__(self, area: QAbstractScrollArea):
        super().__init__(area)
        self._area = area

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        if event.type() in (
            QEvent.Type.Polish,
            QEvent.Type.Show,
            QEvent.Type.StyleChange,
            QEvent.Type.PaletteChange,
        ):
            # Qt re-enables the fill as part of the same show/polish pass, so
            # clear it on the next turn of the loop rather than inside it.
            QTimer.singleShot(0, self._clear)
        return False

    def _clear(self) -> None:
        viewport = self._area.viewport() if hasattr(self._area, "viewport") else None
        if viewport is not None and viewport.autoFillBackground():
            viewport.setAutoFillBackground(False)


def _keep_viewport_unfilled(area: QAbstractScrollArea) -> None:
    viewport = area.viewport() if hasattr(area, "viewport") else None
    if viewport is None or getattr(viewport, "_viewport_fill_keeper", None) is not None:
        return
    keeper = _ViewportFillKeeper(area)
    viewport._viewport_fill_keeper = keeper
    viewport.installEventFilter(keeper)


def make_transparent(area: QAbstractScrollArea) -> None:
    """Leave the scroll area on its native style and erase to the parent card.

    No stylesheet is used, so host scrollbars stay native. On solid desktops
    the viewport Base is made equal to the surrounding card fill; on glass it
    is left transparent for the system material.
    """
    area.setFrameShape(QFrame.NoFrame)
    area.setFrameShadow(QFrame.Plain)
    area.setBackgroundRole(QPalette.ColorRole.Base)
    area.setAutoFillBackground(False)
    area.setAttribute(Qt.WA_TranslucentBackground, False)
    area.setAttribute(Qt.WA_NoSystemBackground, False)
    viewport = area.viewport()
    if viewport is not None:
        viewport.setBackgroundRole(QPalette.ColorRole.Base)
        viewport.setAttribute(Qt.WA_TranslucentBackground, False)
        viewport.setAttribute(Qt.WA_NoSystemBackground, False)
    try:
        from .surfaces import glass_surfaces_enabled, show_glass_backdrop

        if glass_surfaces_enabled():
            show_glass_backdrop(area)
            if viewport is not None:
                show_glass_backdrop(viewport)
            area.setAutoFillBackground(False)
            if viewport is not None:
                viewport.setAutoFillBackground(False)
        else:
            clear = QColor(0, 0, 0, 0)
            for widget in (area, viewport):
                if widget is None:
                    continue
                palette = widget.palette()
                for group in (
                    QPalette.ColorGroup.Active,
                    QPalette.ColorGroup.Inactive,
                    QPalette.ColorGroup.Disabled,
                ):
                    palette.setColor(group, QPalette.ColorRole.Base, clear)
                    palette.setColor(group, QPalette.ColorRole.Window, clear)
                widget.setPalette(palette)
            if viewport is not None:
                viewport.setAutoFillBackground(False)
            area.setAutoFillBackground(False)
            _keep_scrollbars_opaque(area)
    except Exception:
        pass
    _keep_viewport_unfilled(area)
    inner = area.widget() if hasattr(area, "widget") else None
    if isinstance(inner, QWidget):
        inner.setAutoFillBackground(False)
        inner.setAttribute(Qt.WA_TranslucentBackground, False)
        from .surfaces import glass_surfaces_enabled

        if not glass_surfaces_enabled():
            # QScrollArea hands its content widget a Base-role background, and the
            # app's Base is pure white (light) or near-black (dark) while the page
            # surface is the window colour. Left alone, that painted a rectangle
            # behind every page's cards — the white/black block on Select
            # Software and Settings. Clear it the same way as the viewport.
            clear = QColor(0, 0, 0, 0)
            palette = inner.palette()
            for group in (
                QPalette.ColorGroup.Active,
                QPalette.ColorGroup.Inactive,
                QPalette.ColorGroup.Disabled,
            ):
                palette.setColor(group, QPalette.ColorRole.Base, clear)
                palette.setColor(group, QPalette.ColorRole.Window, clear)
            inner.setPalette(palette)
            inner.setAutoFillBackground(False)


def _windows_hides_scrollbars() -> bool:
    return sys.platform == "win32"


def _scroll_policy(requested: Qt.ScrollBarPolicy) -> Qt.ScrollBarPolicy:
    """Windows scroll areas show no bar. Other desktops keep the caller's policy."""
    if _windows_hides_scrollbars():
        return Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    return requested


def configure_scroll_area(
    area: QAbstractScrollArea,
    *,
    horizontal: Qt.ScrollBarPolicy = Qt.ScrollBarPolicy.ScrollBarAsNeeded,
    vertical: Qt.ScrollBarPolicy = Qt.ScrollBarPolicy.ScrollBarAsNeeded,
    transparent: bool = False,
) -> QAbstractScrollArea:
    """Let the platform widget decide: as-needed bars, no stylesheet of ours."""
    area.setHorizontalScrollBarPolicy(_scroll_policy(horizontal))
    area.setVerticalScrollBarPolicy(_scroll_policy(vertical))
    if transparent:
        make_transparent(area)
    return area


def ensure_native_scrolling(widget) -> None:
    """Put scroll areas back on the platform's widgets, whatever they were.

    Only clears a local stylesheet (the one thing that replaces the platform's
    scrollbars) and restores as-needed policies; called for every scroll area on
    a theme refresh, so a stray ``setStyleSheet`` cannot quietly undo this.
    """
    if not isinstance(widget, QAbstractScrollArea):
        return
    if widget.styleSheet():
        widget.setStyleSheet("")
        make_transparent(widget)
    if _windows_hides_scrollbars():
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return
    if widget.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn:
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    if widget.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn:
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)


def apply_native_scrolling(root) -> None:
    """Walk a window (or the whole application) and let its scroll areas be."""
    if isinstance(root, QApplication):
        widgets = root.allWidgets()
    elif isinstance(root, QWidget):
        widgets = [root] + root.findChildren(QAbstractScrollArea)
    else:
        return
    for widget in widgets:
        ensure_native_scrolling(widget)
