from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..browser import open_browser
from .. import fx
from ..config import DONATION_CRYPTO, DONATION_LINKS
from ..i18n import tr
from .dark import page_margins, theme_foreground
from .support_appeal import (
    bundled_portrait_path,
    circle_pixmap,
    pick_support_appeal_key,
    support_appeal_html,
)
from .surfaces import show_glass_backdrop
from .widgets import Card, FlowLayout

class _WrappedCopy(QLabel):

    height_changed = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)

    def needed_height(self) -> int:
        if self.width() <= 0:
            return 0
        return max(0, int(self.heightForWidth(self.width())))

    def height_for_width(self, width: int) -> int:
        width = max(1, int(width))
        if self.textFormat() != Qt.TextFormat.RichText:
            return max(0, int(self.heightForWidth(width)))
        from PySide6.QtGui import QTextDocument

        doc = QTextDocument()
        doc.setDefaultFont(self.font())
        doc.setDocumentMargin(0)
        doc.setHtml(self.text())
        doc.setTextWidth(width)
        return int(doc.size().height())

    def setText(self, text: str) -> None:
        super().setText(text)
        self._sync_min_height()

    def _sync_min_height(self) -> None:
        needed = self.needed_height()
        if needed <= 0 or needed == self.minimumHeight():
            return
        self.setMinimumHeight(needed)
        self.updateGeometry()
        self.height_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_min_height()

class SupportPage(QWidget):

    content_size_changed = Signal()

    def __init__(self, parent=None, donations=None):
        super().__init__(parent)
        self._donations = self._cached_donations() if donations is None else list(donations)
        self._appeal_key = pick_support_appeal_key(self._donations)
        self._appeal_shown_once = False
        self._last_card_height = 0
        self._applied_card_height = 0
        self._content_check_queued = False
        self.setObjectName("supportPage")
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet("")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*page_margins())
        layout.setSpacing(10)

        self._card = Card()

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setSpacing(14)

        portrait_lbl = QLabel()
        portrait_lbl.setFixedSize(48, 48)
        portrait_lbl.setAutoFillBackground(False)
        portrait_lbl.setStyleSheet("background: transparent; border: none;")
        shipped = bundled_portrait_path()
        if shipped.exists():
            pix = circle_pixmap(QPixmap(str(shipped)), 48)
            portrait_lbl.setPixmap(pix)
        header_row.addWidget(portrait_lbl, 0, Qt.AlignmentFlag.AlignTop)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)

        self._author_lbl = QLabel(tr("donate_support_author"))
        self._author_lbl.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: palette(window-text); background: transparent; border: none;"
        )
        info_col.addWidget(self._author_lbl)

        self._role_lbl = QLabel(tr("donate_support_role"))
        self._role_lbl.setStyleSheet(
            "font-size: 11px; color: palette(placeholder-text); background: transparent; border: none;"
        )
        info_col.addWidget(self._role_lbl)
        header_row.addLayout(info_col, 1)

        card_layout.addLayout(header_row)

        self._appeal_body = _WrappedCopy()
        self._appeal_body.height_changed.connect(self._queue_content_check)
        self._appeal_body.setTextFormat(Qt.TextFormat.RichText)
        self._appeal_body.setStyleSheet(
            "font-size: 12px; line-height: 1.4; color: palette(window-text); background: transparent; border: none;"
        )
        self._appeal_body.setText(support_appeal_html(self._appeal_key))
        card_layout.addWidget(self._appeal_body)

        btn_row = FlowLayout(spacing=8)

        primary_methods = [
            ("donate_kofi", "kofi"),
            ("donate_bmac", "bmac"),
            ("donate_paypal", "paypal"),
            ("donate_revolut", "revolut"),
            ("donate_patreon", "patreon"),
        ]
        if fx.routed():
            primary_methods = [("donate_boosty", "boosty")]

        self._method_buttons = []
        for key, link_id in primary_methods:
            url = DONATION_LINKS.get(link_id, "")
            btn = QPushButton(tr(key))
            btn.setCursor(Qt.CursorShape.ArrowCursor)
            btn.setMinimumHeight(28)
            btn.clicked.connect(lambda _=False, u=url: open_browser(u))
            btn_row.addWidget(btn)
            self._method_buttons.append((btn, key))

        card_layout.addLayout(btn_row)

        bottom_links_row = QHBoxLayout()
        bottom_links_row.setContentsMargins(2, 4, 2, 0)
        bottom_links_row.setSpacing(14)

        self._honeygain_link = QPushButton(tr("donate_honeygain"))
        self._honeygain_link.setFlat(True)
        self._honeygain_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._honeygain_link.setStyleSheet(
            "QPushButton { font-size: 11px; color: palette(placeholder-text); background: transparent; border: none; text-decoration: underline; text-align: left; }"
            "QPushButton:hover { color: palette(window-text); }"
        )
        self._honeygain_link.clicked.connect(
            lambda _=False, u=DONATION_LINKS.get("honeygain", ""): open_browser(u)
        )
        bottom_links_row.addWidget(self._honeygain_link, 0, Qt.AlignmentFlag.AlignLeft)

        bottom_links_row.addStretch(1)

        self._crypto_toggle_link = QPushButton(tr("donate_crypto_toggle"))
        self._crypto_toggle_link.setFlat(True)
        self._crypto_toggle_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._crypto_toggle_link.setStyleSheet(
            "QPushButton { font-size: 11px; color: palette(placeholder-text); background: transparent; border: none; text-decoration: underline; text-align: right; }"
            "QPushButton:hover { color: palette(window-text); }"
        )
        self._crypto_toggle_link.clicked.connect(self._toggle_crypto_drawer)
        bottom_links_row.addWidget(self._crypto_toggle_link, 0, Qt.AlignmentFlag.AlignRight)

        card_layout.addLayout(bottom_links_row)

        self._crypto_drawer = QWidget()
        self._crypto_drawer.setAutoFillBackground(False)
        self._crypto_drawer.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        drawer_layout = QVBoxLayout(self._crypto_drawer)
        drawer_layout.setContentsMargins(2, 6, 2, 2)
        drawer_layout.setSpacing(4)

        for label, addr in DONATION_CRYPTO.items():
            c_row = QHBoxLayout()
            c_row.setSpacing(6)

            tag = QLabel(f"<b>{label.split(' ')[0]}:</b>")
            tag.setMinimumWidth(55)
            tag.setStyleSheet("font-size: 11px; color: palette(window-text); background: transparent; border: none;")
            c_row.addWidget(tag)

            addr_box = QLabel(addr)
            addr_box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            addr_box.setStyleSheet(
                "font-family: monospace; font-size: 10px; background: rgba(120, 120, 120, 0.15); "
                "padding: 2px 4px; border-radius: 3px; color: palette(window-text);"
            )
            c_row.addWidget(addr_box, 1)

            copy_btn = QPushButton(tr("support_copy_address"))
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setStyleSheet("font-size: 10px; padding: 2px 6px;")
            copy_btn.clicked.connect(lambda _=False, a=addr, b=copy_btn: self._copy_crypto(a, b))
            c_row.addWidget(copy_btn)

            drawer_layout.addLayout(c_row)

        self._crypto_drawer.setVisible(False)
        card_layout.addWidget(self._crypto_drawer)
        if fx.routed():
            self._honeygain_link.setVisible(False)
            self._crypto_toggle_link.setVisible(False)

        self._card.set_layout(card_layout)
        layout.addWidget(self._card)
        layout.addStretch(1)

    @staticmethod
    def _cached_donations() -> list:
        from .. import paths
        from ..donors import cached_donors_path, load_donors_file, parse_donors_csv_text

        try:
            return parse_donors_csv_text(
                load_donors_file([cached_donors_path(), paths.RESOURCES_DIR / "donors.csv"]) or ""
            )
        except Exception:
            return []

    def set_donations(self, donations) -> None:
        self._donations = list(donations or ())

    def _render_appeal(self) -> None:
        self._appeal_body.setText(support_appeal_html(self._appeal_key))

    def showEvent(self, event):
        super().showEvent(event)
        if self._appeal_shown_once:
            self._appeal_key = pick_support_appeal_key(self._donations)
            self._render_appeal()
            self._queue_content_check()
        self._appeal_shown_once = True

    def content_height_hint(self, width: int | None = None) -> int:
        if self.layout() is not None:
            self.layout().activate()
        if hasattr(self, "_card") and self._card.layout() is not None:
            self._card.layout().activate()
        top, right, bottom, left = page_margins()
        page_w = int(width) if width and int(width) > 0 else int(self.width())
        card_w = max(1, page_w - left - right)
        if not hasattr(self, "_card"):
            return 240 + top + bottom
        return self.card_height_for_width(card_w) + top + bottom

    def card_height_for_width(self, card_w: int) -> int:
        if not hasattr(self, "_card"):
            return 240
        if self._card.layout() is not None:
            self._card.layout().activate()
        needed = 0
        if self._card.layout() is not None:
            needed = int(self._card.layout().totalHeightForWidth(max(1, int(card_w))))
            needed = max(needed, int(self._card.layout().minimumSize().height()))
            label = getattr(self, "_appeal_body", None)
            card_now = int(self._card.width())
            if label is not None and card_now > 1 and label.width() > 0 and int(card_w) != card_now:
                inset = card_now - int(label.width())
                needed += label.height_for_width(card_w - inset) - label.needed_height()
        return needed or 240

    def _sync_card_height(self) -> None:
        card = getattr(self, "_card", None)
        if card is None:
            return
        card_w = int(card.width())
        if card_w <= 1:
            top, right, bottom, left = page_margins()
            card_w = max(1, int(self.width()) - left - right)
        needed = self.card_height_for_width(card_w)
        if needed == self._applied_card_height:
            return
        self._applied_card_height = needed
        card.setFixedHeight(needed)

    def _queue_content_check(self) -> None:
        if self._content_check_queued:
            return
        self._content_check_queued = True
        QTimer.singleShot(0, self._report_content_size)

    def _report_content_size(self) -> None:
        self._content_check_queued = False
        self._sync_card_height()
        needed = int(self._applied_card_height)
        if needed <= 0 or needed == self._last_card_height:
            return
        self._last_card_height = needed
        self.content_size_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_card_height()
        self._queue_content_check()

    def _toggle_crypto_drawer(self):
        show = not self._crypto_drawer.isVisible()
        self._crypto_drawer.setVisible(show)
        self._crypto_toggle_link.setText(
            tr("donate_crypto_hide") if show else tr("donate_crypto_toggle")
        )
        self._queue_content_check()
        win = self.window()
        if win is not None and hasattr(win, "_adjust_window_geometry"):
            win._adjust_window_geometry()

    def _copy_crypto(self, address: str, button: QPushButton):
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(address)
        orig_text = tr("support_copy_address")
        button.setText(tr("support_copied_btn"))
        button.setEnabled(False)
        QTimer.singleShot(1500, lambda: (button.setText(orig_text), button.setEnabled(True)))

    def retranslate(self) -> None:
        self._author_lbl.setText(tr("donate_support_author"))
        self._role_lbl.setText(tr("donate_support_role"))
        self._render_appeal()
        self._queue_content_check()
        self._honeygain_link.setText(tr("donate_honeygain"))
        self._crypto_toggle_link.setText(
            tr("donate_crypto_hide") if self._crypto_drawer.isVisible() else tr("donate_crypto_toggle")
        )
        for btn, key in getattr(self, "_method_buttons", []):
            btn.setText(tr(key))

    def refresh_theme(self) -> None:
        self.update()
