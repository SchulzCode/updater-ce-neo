"""Modal dialogs, flash complete / failed / diagnostics / update available."""

# PEP 810 (Python 3.15+): see src/ui/main_window.py for the convention. The
# update helpers are used to describe an available update, long after the
# window is up, and this is the other module that imports them.
__lazy_modules__ = ["src.updates"]

import re
import sys
from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QDateTime, QSettings, QTime, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..config import install_power_on_steps, device_label_for_model
# Lines kept in the text widget. The manager still retains the full log;
# painting tens of thousands of blocks on the GUI thread is what froze the window.
VISIBLE_LOG_LINES = 2000

from ..diagnostics import (
    CAT_ALL,
    CAT_SP,
    CAT_MTK,
    CAT_GUI,
    DiagnosticsManager,
    available_categories,
    collect_install_sessions,
    filter_log_lines,
    get_log_file,
    log_time_range,
    reveal_in_file_manager,
)
from ..i18n import tr, tr_brand
from ..updates import asset_hint, pick_platform_asset
from .dark import T
from .glass import apply_dialog_theme
from .scrollbars import configure_scroll_area
from .widgets import InstallProgress as _InstallProgress


class FlashCompleteDialog(QDialog):
    def __init__(self, parent=None, package_name="", elapsed="00:00", model="Y1", is_360p_rockbox=False):
        super().__init__(parent)
        apply_dialog_theme(self)
        self.model = model
        self.is_360p_rockbox = is_360p_rockbox
        t = T()
        self.setWindowTitle(tr("flash_complete"))
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(f"<h2 style='color:{t.ok_fg};'>{tr('flash_complete')} &#x2705;</h2>")
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        ndash = '\u2014'
        steps = install_power_on_steps(model)
        body = QLabel(
            f"{tr('flash_current_pkg')}: <b>{package_name or ndash}</b><br>"
            f"{tr('flash_elapsed')}: {elapsed}<br><br>"
            f"<b>{steps}</b>"
        )
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        layout.addWidget(body)

        if self.is_360p_rockbox:
            self._theme_box = QWidget()
            self._theme_box.setStyleSheet(
                f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border};"
                f" border-radius: 8px; }}"
            )
            tb_layout = QVBoxLayout(self._theme_box)
            tb_layout.setContentsMargins(14, 10, 14, 10)
            tb_layout.setSpacing(4)
            self._theme_title = QLabel(f"<b>{tr('themepack_card_title')}</b>")
            self._theme_title.setStyleSheet(f"font-size: 13px; color: {t.fg}; border: none; background: transparent;")
            tb_layout.addWidget(self._theme_title)
            self._theme_desc = QLabel(tr("themepack_card_desc"))
            self._theme_desc.setWordWrap(True)
            self._theme_desc.setStyleSheet(f"font-size: 11px; color: {t.fg}; border: none; background: transparent;")
            tb_layout.addWidget(self._theme_desc)

            tb_row = QHBoxLayout()
            tb_row.setContentsMargins(0, 4, 0, 0)
            self._theme_btn = QPushButton(tr("themepack_install_btn"))
            self._theme_btn.clicked.connect(self._open_theme_pack_flow)
            tb_row.addWidget(self._theme_btn)
            tb_row.addStretch()
            tb_layout.addLayout(tb_row)
            layout.addWidget(self._theme_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _open_theme_pack_flow(self):
        from ..theme_pack import Rockbox360pSetupWizard
        dlg = Rockbox360pSetupWizard(parent=self, model=self.model)
        dlg.installed_success.connect(self._on_theme_pack_installed)
        dlg.exec()

    def _on_theme_pack_installed(self):
        t = T()
        if hasattr(self, "_theme_title"):
            self._theme_title.setText(f"<span style='color:{t.ok_fg}; font-weight:700;'>{tr('themepack_installed_success')}</span>")
        if hasattr(self, "_theme_desc"):
            self._theme_desc.setText(tr("themepack_complete"))
        if hasattr(self, "_theme_btn"):
            self._theme_btn.setText(tr("themepack_installed_success"))
            self._theme_btn.setEnabled(False)
        self.adjustSize()


class FlashFailedDialog(QDialog):
    def __init__(self, parent=None, error_code="", step="", package_name="", retry_count=0):
        super().__init__(parent)
        apply_dialog_theme(self)
        self.setWindowTitle(tr("flash_failed"))
        self.setMinimumWidth(440)
        self._want_retry = False
        self._want_log = False
        t = T()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        title = QLabel(
            f"<h2 style='color:{t.danger_fg};'>{tr('flash_failed')} &#x2715;</h2>"
        )
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        ndash = '\u2014'
        body = QLabel(
            f"{tr('err_error_code')}: <b>{error_code or ndash}</b><br>"
            f"{tr('err_failed_at')}: {step or ndash}<br>"
            f"{tr('flash_current_pkg')}: {package_name or ndash}<br>"
            f"{tr('err_retry_count')}: {retry_count}"
        )
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        layout.addWidget(body)

        row = QHBoxLayout()
        retry_btn = QPushButton(tr("err_btn_retry"))
        retry_btn.setDefault(True)
        retry_btn.clicked.connect(self._on_retry)
        log_btn = QPushButton(tr("err_view_log"))
        log_btn.clicked.connect(self._on_log)
        close_btn = QPushButton(tr("close"))
        close_btn.clicked.connect(self.reject)
        row.addWidget(retry_btn)
        row.addWidget(log_btn)
        row.addWidget(close_btn)
        row.addStretch()
        layout.addLayout(row)

    def _on_retry(self):
        self._want_retry = True
        self.accept()

    def _on_log(self):
        self._want_log = True
        self.accept()

    def want_retry(self):
        return self._want_retry

    def want_log(self):
        return self._want_log


class PreInstallGuidanceDialog(QDialog):
    """Pre-install modal ensuring device is powered off and disconnected before waiting."""

    def __init__(self, parent=None, model="", is_mtk_generic=False):
        super().__init__(parent)
        apply_dialog_theme(self)
        t = T()
        self.setWindowTitle(tr("dialog_pre_install_title"))
        self.setMinimumSize(540, 260)
        self.resize(560, 275)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        device_name = device_label_for_model(model) if model else tr("device_fallback_word")
        header_text = tr("dialog_pre_install_header").format(model=device_name)
        title = QLabel(f"<h3 style='color:{t.warn_fg}; margin:0; padding:0;'>{header_text}</h3>")
        title.setTextFormat(Qt.RichText)
        title.setWordWrap(True)
        layout.addWidget(title)

        tip_text = (
            tr("dialog_pre_install_generic_tip")
            if is_mtk_generic
            else tr("dialog_pre_install_innioasis_tip")
        )
        body = QLabel(tip_text)
        body.setWordWrap(True)
        body.setTextFormat(Qt.PlainText)
        body.setStyleSheet(f"font-size: 13px; line-height: 1.4; color: {t.fg};")
        layout.addWidget(body)

        layout.addStretch()

        from .flash_page import _RoundNextButton

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        cancel_btn = _RoundNextButton(symbol="back", accent=False)
        cancel_btn.setText(tr("cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn, 0, Qt.AlignVCenter)

        continue_btn = _RoundNextButton(symbol="next", accent=True)
        continue_btn.setText(tr("dialog_pre_install_continue"))
        continue_btn.clicked.connect(self.accept)
        btn_row.addWidget(continue_btn, 0, Qt.AlignVCenter)

        layout.addLayout(btn_row)


def notice_see_more_url(url: str, lang: str | None = None) -> str:
    """The See More target: the notice page, or its Google Translate view outside English."""
    from urllib.parse import quote

    from ..i18n import current_language, is_english_language

    code = lang if lang is not None else current_language()
    if not url or is_english_language(code):
        return url
    return (
        "https://translate.google.com/translate?sl=auto&tl="
        f"{quote(code, safe='')}&u={quote(url, safe='')}"
    )


class NoticesDialog(QDialog):
    """Pop-out list of active notices. Each notice expands to its full content."""

    def __init__(self, parent=None, notices=()):
        super().__init__(parent)
        apply_dialog_theme(self)
        t = T()
        self.setWindowTitle(tr("notices_dialog_title"))
        self.setMinimumSize(520, 380)
        self.resize(560, 460)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        heading = QLabel(f"<h3 style='color:{t.fg}; margin:0; padding:0;'>{tr('notices_dialog_title')}</h3>")
        heading.setTextFormat(Qt.RichText)
        layout.addWidget(heading)

        from .flash_page import _RoundNextButton
        from .scrollbars import configure_scroll_area

        self._rows = []
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        for notice in notices:
            row = _NoticeRow(notice, parent=body)
            self._rows.append(row)
            body_layout.addWidget(row)
        if not notices:
            empty = QLabel(tr("notices_dialog_empty"))
            empty.setWordWrap(True)
            body_layout.addWidget(empty)
        body_layout.addStretch()

        from PySide6.QtWidgets import QScrollArea

        from .surfaces import show_glass_backdrop

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(body)
        show_glass_backdrop(scroll.viewport())
        show_glass_backdrop(body)
        configure_scroll_area(scroll)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = _RoundNextButton(symbol="back", accent=False)
        close_btn.setText(tr("toast_dismiss"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn, 0, Qt.AlignVCenter)
        layout.addLayout(btn_row)


class NoticesPage(QWidget):
    """Notices as a main-window page: scrollable, self-refreshing, with dates."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("noticesPage")
        from .scrollbars import configure_scroll_area
        from .surfaces import show_glass_backdrop
        from .widgets import Throbber
        from PySide6.QtWidgets import QScrollArea

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # No refresh button: the feed refreshes itself in the background, and
        # again whenever the sidebar row is clicked. The spinner is the only
        # thing that appears here, and only while a fetch is actually running.
        top = QHBoxLayout()
        top.addStretch(1)
        self._spinner = Throbber(self)
        top.addWidget(self._spinner, 0, Qt.AlignVCenter)
        layout.addLayout(top)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(8)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setWidget(self._body)
        show_glass_backdrop(self._scroll.viewport())
        show_glass_backdrop(self._body)
        configure_scroll_area(self._scroll)
        layout.addWidget(self._scroll, 1)

        # A plain hint, not a different-coloured web link: it is the way to
        # learn how these notices are written and moderated.
        from PySide6.QtWidgets import QPushButton

        self._how_btn = QPushButton(tr("notices_how_community"))
        self._how_btn.setFlat(True)
        self._how_btn.setCursor(Qt.PointingHandCursor)
        self._how_btn.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; color: {T().fg_muted};"
            " font-size: 12px; padding: 2px 4px; }"
            f"QPushButton:hover {{ color: {T().fg}; }}"
        )
        self._how_btn.clicked.connect(self._open_how_notices_work)
        layout.addWidget(self._how_btn, 0, Qt.AlignHCenter)

        try:
            from ..notice_service import NoticeService

            service = NoticeService.instance()
            service.notices_ready.connect(self.set_notices)
            service.refresh_started.connect(self._spinner.start)
            service.refresh_finished.connect(self._spinner.stop)
            service.start_passive_refresh()
            self.set_notices(service.active_notices)
        except Exception:
            self.set_notices([])

    def refresh_from_service(self) -> None:
        """Reload the list from the notice service each time the tab opens."""
        try:
            from ..notice_service import NoticeService

            self.set_notices(NoticeService.instance().active_notices)
        except Exception:
            self.set_notices([])

    def set_notices(self, notices) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()
        notices = list(notices or [])
        for notice in notices:
            row = _NoticeRow(
                notice, parent=self._body, show_dates=True, allow_dismiss=True
            )
            row.dismissed.connect(lambda _notice: self.refresh_from_service())
            self._body_layout.addWidget(row)
        if not notices:
            empty = QLabel(tr("notices_dialog_empty"))
            empty.setWordWrap(True)
            self._body_layout.addWidget(empty)
        self._body_layout.addStretch()

    def refresh(self) -> None:
        """Reload from the cache now, then fetch a fresh copy in the background."""
        self.refresh_from_service()
        try:
            from ..notice_service import NoticeService

            NoticeService.instance().fetch_async()
        except Exception:
            pass

    def _open_how_notices_work(self) -> None:
        from ..browser import open_browser

        open_browser(
            "https://github.com/y1-community/updater-ce-neo/discussions/6"
        )

    def retranslate(self) -> None:
        if hasattr(self, "_how_btn"):
            self._how_btn.setText(tr("notices_how_community"))


class _NoticeRow(QFrame):
    """One notice in the list: its title, dates and the full content, always shown.

    The body is Markdown and its links open in the browser. A notice with a
    ``url`` gets a circular globe button that opens the page on the web. On the
    full page each row can also be dismissed (the banner carries its own cross).
    """

    dismissed = Signal(object)

    def __init__(self, notice, parent=None, show_dates=False, allow_dismiss=False):
        super().__init__(parent)
        self._notice = notice
        self.setObjectName("noticeRow")
        # Same bento card as the toasts: painted rounded plate, no opaque fill.
        self.setProperty("cssClass", "card")
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        t = T()

        col = QVBoxLayout(self)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel(notice.title or notice.id, self)
        title.setStyleSheet(f"font-weight: 600; color: {t.fg}; border: none; background: transparent;")
        title.setWordWrap(True)
        header.addWidget(title, 1)
        if allow_dismiss:
            from .flash_page import _RoundNextButton

            self._dismiss = _RoundNextButton(symbol="cancel", accent=False)
            self._dismiss.setToolTip(tr("toast_dismiss"))
            self._dismiss.clicked.connect(self._on_dismiss_clicked)
            header.addWidget(self._dismiss, 0, Qt.AlignTop)
        col.addLayout(header)

        if show_dates and notice.from_date:
            posted = notice.from_date
            # A bare date carries no time, so show only the date for it.
            stamp = (
                posted.strftime("%Y-%m-%d %H:%M")
                if posted.time() != datetime.min.time()
                else posted.strftime("%Y-%m-%d")
            )
            dates = QLabel(tr("notices_posted").format(date=stamp), self)
            dates.setStyleSheet(f"font-size: 12px; color: {t.fg_muted};")
            col.addWidget(dates)

        body_text = notice.full_content or notice.content or ""
        self._body = QTextBrowser(self)
        self._body.setOpenExternalLinks(True)
        self._body.setReadOnly(True)
        self._body.setFrameShape(QFrame.NoFrame)
        self._body.setStyleSheet(f"font-size: 13px; color: {t.fg}; background: transparent;")
        self._body.setMarkdown(body_text)
        self._body.document().setDocumentMargin(0)
        self._body.document().setTextWidth(-1)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.document().contentsChanged.connect(self._fit_body)
        col.addWidget(self._body)

        from .flash_page import _RoundNextButton

        self._more = _RoundNextButton(symbol="www", accent=True)
        self._more.setText(tr("notices_see_more"))
        self._more.clicked.connect(self._open_see_more)
        self._more.setVisible(bool(notice.url))
        col.addWidget(self._more, 0, Qt.AlignLeft)
        self._fit_body()

    def _on_dismiss_clicked(self) -> None:
        try:
            from ..notice_service import NoticeService

            NoticeService.instance().dismiss_notice(self._notice)
        except Exception:
            pass
        self.dismissed.emit(self._notice)

    def _fit_body(self) -> None:
        """Size the body to its document, so the list scrolls instead of the text."""
        doc = self._body.document()
        width = self._body.viewport().width() or 400
        doc.setTextWidth(width)
        self._body.setFixedHeight(int(doc.size().height()) + 4)

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._fit_body()

    def _open_see_more(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(notice_see_more_url(self._notice.url)))

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        from .widgets import _paint_bento_surface

        _paint_bento_surface(self, event)


class RetryGuidanceDialog(QDialog):
    """Hardware reset instructions shown when the target stops answering.

    A player left in a half-initialised state answers the BROM handshake with
    errno 5 / errno 2 rather than a handshake, and retrying in place cannot
    recover it: the cable has to come out and the hidden reset button has to
    be pressed first. This dialog is that instruction, and it doubles as the
    retry entry point so the next attempt starts from a clean device state.
    """

    def __init__(self, parent=None, detail=""):
        super().__init__(parent)
        apply_dialog_theme(self)
        t = T()
        self.setWindowTitle(tr("retry_guidance_title"))
        self.setMinimumSize(610, 275)
        self.resize(610, 275)
        self._want_retry = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(
            f"<h2 style='color:{t.warn_fg};'>{tr('retry_guidance_title')}</h2>"
        )
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        intro = QLabel(tr("retry_guidance_intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        steps = QLabel(
            f"<b>{tr('retry_guidance_step_1')}</b><br>"
            f"<b>{tr('retry_guidance_step_2')}</b><br>"
            f"<b>{tr('retry_guidance_step_3')}</b>"
        )
        steps.setTextFormat(Qt.RichText)
        steps.setWordWrap(True)
        layout.addWidget(steps)

        if detail:
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setProperty("cssClass", "dimmed")
            layout.addWidget(detail_label)

        from .flash_page import _RoundNextButton

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch()
        retry_btn = _RoundNextButton(symbol="retry", accent=True)
        retry_btn.setText(tr("retry_guidance_btn_retry"))
        retry_btn.clicked.connect(self._on_retry)
        close_btn = _RoundNextButton(symbol="back", accent=False)
        close_btn.setText(tr("close"))
        close_btn.clicked.connect(self.reject)
        row.addWidget(retry_btn, 0, Qt.AlignVCenter)
        row.addWidget(close_btn, 0, Qt.AlignVCenter)
        layout.addLayout(row)

    def _on_retry(self):
        self._want_retry = True
        self.accept()

    def want_retry(self):
        return self._want_retry


def _to_qdatetime(value: datetime) -> QDateTime:
    """QDateTime for a Python datetime (log timestamps are local time)."""
    return QDateTime(
        QDate(value.year, value.month, value.day),
        QTime(value.hour, value.minute, value.second),
    )


def _from_python(edit) -> datetime | None:
    """Read a QDateTimeEdit back as a Python datetime, or None if unreadable."""
    try:
        value = edit.dateTime()
    except Exception:
        return None
    try:
        return value.toPython()
    except Exception:
        d, tm = value.date(), value.time()
        return datetime(d.year(), d.month(), d.day(), tm.hour(), tm.minute(), tm.second())


class DiagnosticsView(QWidget):
    """Diagnostics console.

    Lives in the main window so it can be opened during an install. A
    ``DiagnosticsDialog`` embeds the same view when a standalone window is
    needed.
    """

    close_requested = Signal()

    def __init__(self, parent=None, lines=None, initial_category=CAT_ALL):
        super().__init__(parent)
        t = T()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # ── Header Bar: Dropdown for log categories + path indicator ──
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        self._header_lbl = QLabel(f"{tr('log_center')}:")
        self._header_lbl.setStyleSheet(f"font-weight: 600; color: {t.fg};")
        header_row.addWidget(self._header_lbl)

        self._category_combo = QComboBox()
        # Only the backends this host can actually produce (macOS has no SP
        # Flash Tool; Windows hides MTKClient unless it was revealed with M).
        for cat, label_key in available_categories():
            # The app-log entry names the app, so it follows the active brand.
            self._category_combo.addItem(tr_brand(label_key), cat)

        idx = self._category_combo.findData(initial_category)
        if idx < 0:
            idx = self._category_combo.findData(CAT_ALL)
        if idx >= 0:
            self._category_combo.setCurrentIndex(idx)
        self._category_combo.currentIndexChanged.connect(self._on_category_changed)
        header_row.addWidget(self._category_combo, 1)

        self._file_badge = QLabel()
        self._file_badge.setStyleSheet(
            f"font-size: 11px; color: {t.fg}; background: {t.bg_card}; padding: 3px 8px;"
            f" border-radius: 4px; border: 1px solid {t.border};"
        )
        header_row.addWidget(self._file_badge)
        layout.addLayout(header_row)

        # ── Time filter row: jump to an install attempt, or pick a window ──
        self._updating_filters = False
        # A range is only applied once the user asks for one, so the default
        # view never hides lines behind a window the user did not choose.
        self._range_active = False
        self._open_end = False
        self._sessions = []
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._session_lbl = QLabel(tr("log_session_label"))
        self._session_lbl.setStyleSheet(f"color: {t.fg};")
        filter_row.addWidget(self._session_lbl)

        self._session_combo = QComboBox()
        self._session_combo.setToolTip(tr("log_session_tip"))
        self._session_combo.currentIndexChanged.connect(self._on_session_changed)
        filter_row.addWidget(self._session_combo, 1)

        self._problems_check = QCheckBox(tr("log_problems_only"))
        self._problems_check.setToolTip(tr("log_problems_tip"))
        self._problems_check.toggled.connect(self._on_filter_changed)
        filter_row.addWidget(self._problems_check)

        layout.addLayout(filter_row)

        range_row = QHBoxLayout()
        range_row.setSpacing(8)

        self._from_lbl = QLabel(tr("log_range_from"))
        self._from_lbl.setStyleSheet(f"color: {t.fg};")
        range_row.addWidget(self._from_lbl)

        self._from_edit = QDateTimeEdit()
        self._from_edit.setCalendarPopup(True)
        self._from_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._from_edit.setToolTip(tr("log_range_from_tip"))
        self._from_edit.dateTimeChanged.connect(self._on_filter_changed)
        range_row.addWidget(self._from_edit, 1)

        self._to_lbl = QLabel(tr("log_range_to"))
        self._to_lbl.setStyleSheet(f"color: {t.fg};")
        range_row.addWidget(self._to_lbl)

        self._to_edit = QDateTimeEdit()
        self._to_edit.setCalendarPopup(True)
        self._to_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._to_edit.setToolTip(tr("log_range_to_tip"))
        self._to_edit.dateTimeChanged.connect(self._on_filter_changed)
        range_row.addWidget(self._to_edit, 1)

        layout.addLayout(range_row)

        # ── Log View Area ──
        self._view = QTextEdit()
        self._view.setObjectName("logView")
        self._view.setReadOnly(True)
        # Monospace comes from the widget font. A stylesheet on this view, or
        # an app rule that matches it, would make Qt draw classic arrow bars
        # (see src/ui/scrollbars.py).
        log_font = QFont(self._view.font())
        log_font.setFamilies(["SF Mono", "Cascadia Code", "Consolas", "Courier New"])
        log_font.setStyleHint(QFont.StyleHint.Monospace)
        log_font.setPointSize(10)
        self._view.setFont(log_font)
        # Windows hides the bar. Other desktops keep a vertical bar only when needed.
        configure_scroll_area(
            self._view,
            horizontal=Qt.ScrollBarAlwaysOff,
            vertical=Qt.ScrollBarAsNeeded,
        )
        layout.addWidget(self._view, 1)

        # ── Action Buttons Bar: Go To File, Save File, Copy, Close ──
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self._goto_btn = QPushButton(tr("log_btn_goto_file"))
        self._goto_btn.setToolTip("Reveal log file in Finder / Explorer")
        self._goto_btn.clicked.connect(self._on_goto_file)
        action_row.addWidget(self._goto_btn)

        self._save_btn = QPushButton(tr("log_btn_save_file"))
        self._save_btn.setToolTip("Export the displayed log file to disk")
        self._save_btn.clicked.connect(self._on_save_file)
        action_row.addWidget(self._save_btn)

        self._copy_btn = QPushButton(tr("log_btn_copy"))
        self._copy_btn.setToolTip("Copy displayed log to clipboard")
        self._copy_btn.clicked.connect(self._on_copy)
        action_row.addWidget(self._copy_btn)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"font-size: 11px; color: {t.ok_fg};")
        action_row.addWidget(self._status_label)

        # How much of the log the filter is showing — silent filtering is how a
        # user concludes the tool "lost" an install.
        self._count_label = QLabel("")
        self._count_label.setStyleSheet(f"font-size: 11px; color: {t.fg};")
        action_row.addWidget(self._count_label)
        action_row.addStretch()

        self._close_btn = QPushButton(tr("close"))
        self._close_btn.setDefault(True)
        self._close_btn.setAutoDefault(False)
        self._close_btn.clicked.connect(self.close_requested.emit)
        action_row.addWidget(self._close_btn)

        layout.addLayout(action_row)

        self._empty = True
        self._custom_lines = list(lines) if lines is not None else None
        self._rebuild_session_combo()
        self._refresh_content()

    def current_category(self) -> str:
        return self._category_combo.currentData() or CAT_ALL

    def _on_category_changed(self, _index: int = 0):
        self._rebuild_session_combo()
        self._refresh_content()

    # -- date/time + install session filtering ------------------------------
    def raw_lines(self, cat: str | None = None) -> list[str]:
        """Unfiltered lines for a category, as the view would show them."""
        cat = cat or self.current_category()
        mgr = DiagnosticsManager.instance()
        # The caller may pass the lines it already has in memory, but an empty
        # list must not hide the persisted diagnostics: reopening the window
        # after an install is exactly how a finished run gets inspected.
        custom = list(self._custom_lines or [])
        if custom and cat == CAT_ALL:
            return custom
        lines = mgr.get_display_lines(cat)
        if not lines and custom:
            lines = [ln for ln in custom if mgr.classify_line(ln) == cat]
        return lines

    def _selected_session(self):
        data = self._session_combo.currentData()
        sessions = self._sessions or []
        for session in sessions:
            if session.number == data:
                return session
        return None

    def _rebuild_session_combo(self):
        """List the install attempts found in the current category's log."""
        self._sessions = collect_install_sessions(self.raw_lines())
        previous = self._session_combo.currentData()
        self._updating_filters = True
        try:
            self._session_combo.clear()
            self._session_combo.addItem(tr("log_session_all"), None)
            for session in reversed(self._sessions):  # newest first
                self._session_combo.addItem(self._session_label(session), session.number)
            idx = self._session_combo.findData(previous)
            self._session_combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._updating_filters = False
        self._sync_range_edits()

    def _session_label(self, session) -> str:
        stamp = session.started.strftime("%Y-%m-%d %H:%M:%S") if session.started else "?"
        status = {
            "ok": tr("log_session_ok"),
            "failed": tr("log_session_failed"),
        }.get(session.status, tr("log_session_running"))
        if session.success is False and session.detail:
            status = f"{status}: {session.detail}"
        target = session.package or tr("log_session_unknown_package")
        return f"{stamp} · {target} · {status}"

    def _sync_range_edits(self):
        """Point the From/To pickers at the selected install, or the whole log.

        An install still in progress (no completion banner yet) keeps an open
        end, so lines written while the user watches stay visible instead of
        being clipped the moment the picker's value goes stale.
        """
        log_start, log_end = log_time_range(self.raw_lines())
        now = datetime.now()
        session = self._selected_session()
        if session is not None:
            start = session.started or log_start or (now - timedelta(hours=1))
            end = session.ended
            self._open_end = session.running
        else:
            start = log_start or (now - timedelta(hours=1))
            end = log_end or now
            self._open_end = False
        if end is None:
            end = max(now, log_end or now)
        to_value = max(end, start)

        self._updating_filters = True
        try:
            self._from_edit.setDateTime(_to_qdatetime(start))
            self._to_edit.setDateTime(_to_qdatetime(to_value))
        finally:
            self._updating_filters = False

    def _on_session_changed(self, _index: int = 0):
        if self._updating_filters:
            return
        self._sync_range_edits()
        # "All installs" clears the window; a specific install narrows it.
        self._range_active = self._selected_session() is not None
        self._refresh_content(preserve_scroll=True)

    def _on_filter_changed(self, *_args):
        if self._updating_filters:
            return
        self._range_active = True
        self._refresh_content(preserve_scroll=True)

    def _filter_active(self) -> bool:
        try:
            return bool(self._problems_check.isChecked()) or self._range_active
        except AttributeError:
            return False

    def _filter_description(self) -> str:
        """Human-readable summary of the active filter, for exports."""
        parts = []
        session = self._selected_session()
        if session is not None:
            parts.append(self._session_label(session))
        else:
            parts.append(
                f"{_from_python(self._from_edit).strftime('%Y-%m-%d %H:%M:%S')} → "
                f"{_from_python(self._to_edit).strftime('%Y-%m-%d %H:%M:%S')}"
            )
        if self._problems_check.isChecked():
            parts.append(tr("log_problems_only"))
        return " · ".join(parts)

    def _refresh_content(self, preserve_scroll: bool = False):
        cat = self.current_category()
        self._file_badge.setText(get_log_file(cat).name)

        raw = self.raw_lines(cat)
        # Remember the retained totals so an incremental append can keep the
        # counter exact without re-reading (and re-classifying) the whole log.
        self._raw_total = len(raw)
        self._buffer_was_empty = DiagnosticsManager.instance().buffer_count(cat) == 0
        lines = self._apply_filters(raw)
        shown = lines[-VISIBLE_LOG_LINES:] if len(lines) > VISIBLE_LOG_LINES else lines
        content = "\n".join(shown)
        bar = self._view.verticalScrollBar()
        at_end = self._pinned_to_end()
        scroll = bar.value() if preserve_scroll else 0

        if content.strip():
            self._view.setPlainText(content)
            self._empty = False
            self._last_appended = shown[-1] if shown else None
            if preserve_scroll and not at_end:
                bar.setValue(min(scroll, bar.maximum()))
            else:
                bar.setValue(bar.maximum())
        else:
            self._view.setPlainText(tr("log_no_entries"))
            self._empty = True
            self._last_appended = None

        self._set_count(lines, raw)

    def _apply_filters(self, lines: list[str]) -> list[str]:
        """Apply the date/time window and the problems-only switch."""
        try:
            problems = self._problems_check.isChecked()
        except AttributeError:
            return lines
        if not problems and not self._range_active:
            return lines
        start = _from_python(self._from_edit) if self._range_active else None
        end = None if self._open_end else (_from_python(self._to_edit) if self._range_active else None)
        return filter_log_lines(lines, start=start, end=end, problems_only=problems)

    def _set_count(self, shown, raw) -> None:
        """Show how many lines are on screen.

        With no filter that is the retained log. A category, session, or
        problems filter shows the matching count against the retained total,
        and both numbers move only when lines are added or the filter changes.
        """
        shown_n = len(shown)
        total_n = len(raw)
        if self._filter_active() or shown_n != total_n:
            self._count_label.setText(
                tr("log_lines_count").format(shown=shown_n, total=total_n)
            )
        else:
            self._count_label.setText(tr("log_lines_total").format(total=total_n))

    def set_lines(self, lines):
        self._custom_lines = list(lines) if lines else None
        self._rebuild_session_combo()
        self._refresh_content()

    def _pinned_to_end(self) -> bool:
        """True when the reader is already at the latest line."""
        bar = self._view.verticalScrollBar()
        if bar.maximum() <= 0:
            return True
        return bar.value() >= bar.maximum() - 2

    def _trim_visible_blocks(self) -> None:
        doc = self._view.document()
        extra = doc.blockCount() - VISIBLE_LOG_LINES
        if extra <= 0:
            return
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(
            QTextCursor.MoveOperation.NextBlock,
            QTextCursor.MoveMode.KeepAnchor,
            extra,
        )
        cursor.removeSelectedText()

    def _insert_log_line(self, stored: str) -> None:
        """Add one line without replacing the document.

        Follow the tail only when the reader is already there. A scroll
        position above the end stays put.
        """
        follow = self._pinned_to_end()
        bar = self._view.verticalScrollBar()
        keep = bar.value()
        cursor = QTextCursor(self._view.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        prefix = "" if self._empty or not self._view.toPlainText().strip() else "\n"
        cursor.insertText(prefix + stored)
        self._empty = False
        self._last_appended = stored
        self._trim_visible_blocks()
        if follow:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(keep, bar.maximum()))

    def append_line(self, line):
        mgr = DiagnosticsManager.instance()
        line_str = str(line).rstrip("\r\n")
        if not line_str:
            return
        # The window records the line before it tells the view. A second
        # record is a duplicate and used to force a full document rebuild
        # on every line, which is what made the screen lag.
        stored = mgr.record_log(line_str, dedupe=True)
        if stored is None:
            recent = mgr.get_display_lines(CAT_ALL)
            candidate = recent[-1] if recent else ""
            if not candidate or candidate == getattr(self, "_last_appended", None):
                return
            stored = candidate
        self._custom_lines = None

        cat = self.current_category()
        if (
            not self._empty
            and not self._filter_active()
            and not getattr(self, "_buffer_was_empty", True)
            and (cat == CAT_ALL or mgr.classify_line(line_str) == cat)
        ):
            self._insert_log_line(stored)
            self._raw_total = getattr(self, "_raw_total", 0) + 1
            self._count_label.setText(
                tr("log_lines_total").format(total=self._raw_total)
            )
            return
        self._refresh_content(preserve_scroll=True)

    def _on_goto_file(self):
        cat = self.current_category()
        target_file = get_log_file(cat)
        if not target_file.is_file():
            try:
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(self._view.toPlainText(), encoding="utf-8")
            except Exception:
                pass
        reveal_in_file_manager(target_file)
        self._show_temp_status(f"Revealed in file manager: {target_file.name}")

    def _on_save_file(self):
        from pathlib import Path
        cat = self.current_category()
        default_name = f"updater_ce_{cat}_log.txt"
        default_path = str(Path.home() / default_name)

        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("log_save_title"),
            default_path,
            "Text Files (*.log *.txt);;All Files (*)",
        )
        if not path:
            return

        try:
            content = self._view.toPlainText()
            if self._filter_active():
                # Record what the view was filtered to: a developer reading the
                # export must not mistake a time window for the whole log.
                header = tr("log_export_filter").format(
                    desc=self._filter_description(), category=tr("log_center")
                )
                content = f"# {header}\n{content}"
            Path(path).write_text(content, encoding="utf-8")
            self._show_temp_status(tr("log_save_success").format(path=Path(path).name))
        except Exception as e:
            self._show_temp_status(f"Error saving log: {e}")

    def _on_copy(self):
        from PySide6.QtWidgets import QApplication
        text = self._view.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)
            self._show_temp_status(tr("log_copied_hint"))

    def _show_temp_status(self, msg: str, timeout_ms: int = 3500):
        self._status_label.setText(msg)
        label = self._status_label

        def _clear():
            try:
                label.setText("")
            except RuntimeError:
                pass

        QTimer.singleShot(timeout_ms, _clear)

    def retranslate(self):
        """Apply the active language without rebuilding the log widget."""
        t = T()
        self._header_lbl.setText(f"{tr('log_center')}:")
        self._session_lbl.setText(tr("log_session_label"))
        self._session_combo.setToolTip(tr("log_session_tip"))
        self._from_lbl.setText(tr("log_range_from"))
        self._from_edit.setToolTip(tr("log_range_from_tip"))
        self._to_lbl.setText(tr("log_range_to"))
        self._to_edit.setToolTip(tr("log_range_to_tip"))
        self._problems_check.setText(tr("log_problems_only"))
        self._problems_check.setToolTip(tr("log_problems_tip"))
        self._goto_btn.setText(tr("log_btn_goto_file"))
        self._save_btn.setText(tr("log_btn_save_file"))
        self._copy_btn.setText(tr("log_btn_copy"))
        self._close_btn.setText(tr("close"))
        self._header_lbl.setStyleSheet(f"font-weight: 600; color: {t.fg};")
        current = self.current_category()
        self._category_combo.blockSignals(True)
        try:
            self._category_combo.clear()
            for cat, label_key in available_categories():
                self._category_combo.addItem(tr_brand(label_key), cat)
            idx = self._category_combo.findData(current)
            self._category_combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._category_combo.blockSignals(False)
        self._rebuild_session_combo()
        self._refresh_content(preserve_scroll=True)


class DiagnosticsDialog(QDialog):
    """Standalone diagnostics window. The console itself is ``DiagnosticsView``."""

    def __init__(self, parent=None, lines=None, initial_category=CAT_ALL):
        super().__init__(parent)
        apply_dialog_theme(self)
        self.setWindowTitle(tr("log_center"))
        self.resize(740, 500)
        self.setMinimumSize(540, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._panel = DiagnosticsView(
            parent=self, lines=lines, initial_category=initial_category
        )
        self._panel.close_requested.connect(self.accept)
        layout.addWidget(self._panel)

    def __getattr__(self, name):
        panel = self.__dict__.get("_panel")
        if panel is not None:
            attr = getattr(panel, name, None)
            if attr is not None or hasattr(panel, name):
                return getattr(panel, name)
        raise AttributeError(name)


class UpdateAvailableDialog(QDialog):
    """Wizard-style update dialog with platform-specific assisted installation.

    Shown inline in the main window, so it takes no window-level theme: the
    host window keeps its own glass and the cards keep their material.
    """

    def __init__(self, parent=None, info=None, current_version="", on_skip=None):
        super().__init__(parent)
        self._info = info
        self._on_skip = on_skip
        self._worker = None
        self._dest_path = None
        t = T()

        # Shown inline on the window's glass. Paint the same rounded bento
        # pane as the Settings and release-notes cards, instead of letting the
        # dialog's own opaque plate cover the page.
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self.setWindowTitle(tr("update_available"))
        # Inline in the host window: take the host's width and never ask for
        # a wider one. Text and buttons wrap to whatever width is available.
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        from .flash_page import _RoundNextButton
        from ..updates import asset_hint, pick_platform_asset
        from ..store_package import is_packaged

        self._asset = pick_platform_asset(self._info.assets) if self._info else None
        # A Store build is updated by the Store. Its release only offers that
        # route when the release itself says the version is on the Store
        # (msstore.txt), so a direct-download build is never sent there for a
        # version the Store does not have.
        self._store = bool(getattr(self._info, "store_available", False)) and is_packaged()

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(24, 20, 24, 20)
        self._main_layout.setSpacing(14)

        # Header Row: Squircle icon + title + version badge
        header_row = QHBoxLayout()
        header_row.setSpacing(14)

        self._icon_label = QLabel(self)
        self._icon_label.setFixedSize(48, 48)
        self._icon_label.setAlignment(Qt.AlignCenter)
        from . import icons

        pix = icons.app_icon_pixmap(48)
        if pix.isNull():
            # Windows and Linux set the window icon at startup; use it when
            # the resource path differs from the development checkout.
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None and not app.windowIcon().isNull():
                pix = app.windowIcon().pixmap(48, 48)
        if pix.isNull():
            from .release_icon import app_icon_pixmap as bundled_app_icon

            bundled = bundled_app_icon()
            if not bundled.isNull():
                pix = bundled.scaled(
                    48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
        if not pix.isNull():
            self._icon_label.setPixmap(pix)
            self._icon_label.setStyleSheet("background: transparent; border: none;")
        else:
            # No pictograph when the app icon is unavailable: the header still
            # reads without one, and no platform gets an emoji in its place.
            self._icon_label.setVisible(False)
        header_row.addWidget(self._icon_label, 0, Qt.AlignVCenter)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self._title_label = QLabel(f"<h2 style='margin:0; padding:0; color:{t.fg};'>{tr('update_available')}</h2>")
        self._title_label.setTextFormat(Qt.RichText)
        self._title_label.setWordWrap(True)
        title_col.addWidget(self._title_label)

        ver_key = "update_store_available_fmt" if self._store else "update_available_fmt"
        ver_str = tr(ver_key).format(
            new=self._info.version if self._info else "",
            current=current_version,
        )
        self._subtitle_label = QLabel(ver_str)
        self._subtitle_label.setTextFormat(Qt.RichText)
        self._subtitle_label.setStyleSheet(f"color: {t.fg_muted}; font-size: 13px;")
        self._subtitle_label.setWordWrap(True)
        title_col.addWidget(self._subtitle_label)
        header_row.addLayout(title_col, 1)

        self._main_layout.addLayout(header_row)

        # What's New container. The release notes are the whole body of this
        # window: no per-platform hint card above them (the installer knows
        # where its own file came from), and the notes scroll with the host's
        # own scrollbar, exactly like the Settings menu.
        self._notes_label = QLabel(f"<b>{tr('update_notes')}</b>")
        self._notes_label.setTextFormat(Qt.RichText)
        self._main_layout.addWidget(self._notes_label)

        self._notes = QTextBrowser()
        self._notes.setObjectName("updateNotes")
        self._notes.setFrameShape(QFrame.NoFrame)
        from PySide6.QtGui import QTextOption

        self._notes.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._notes.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self._notes.setMinimumWidth(0)
        self._notes.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._notes.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._notes.setOpenExternalLinks(True)
        self._notes.setMinimumHeight(170)
        self._notes.viewport().setAutoFillBackground(False)
        self._notes.viewport().setAttribute(Qt.WA_TranslucentBackground, False)
        self._notes.document().setDefaultStyleSheet(
            f"body {{ background: transparent; }} a {{ color: {t.accent}; font-weight: 600; text-decoration: none; }}"
        )
        body_text = self._info.body.strip() if self._info else ""
        self._translated = None  # (name, body) once Google Translate returns
        self._translate_worker = None
        self._is_translating = False
        self._translate_label = QLabel()
        self._translate_label.setTextFormat(Qt.RichText)
        self._translate_label.setOpenExternalLinks(False)
        self._translate_label.setWordWrap(True)
        self._translate_label.linkActivated.connect(self._on_translate_link)
        self._main_layout.addWidget(self._translate_label)
        self._render_notes()
        self._update_translate_label()
        if self._should_auto_translate():
            self._start_translation()
        # No widget stylesheet: a local one would replace the platform
        # scrollbar. The notes stay clear so the card pane behind them is what
        # the reader sees, exactly as on the Select Software release notes.
        configure_scroll_area(
            self._notes, vertical=Qt.ScrollBarAsNeeded, transparent=True
        )
        self._notes.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        from .surfaces import clear_glass_plate

        clear_glass_plate(self._notes)
        self._make_notes_seamless()
        self._main_layout.addWidget(self._notes, 1)

        # Progress bar container (hidden until download starts)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(10)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet(
            f"QProgressBar {{ background-color: {t.bg_card}; border: 1px solid {t.border}; border-radius: 5px; }}"
            f"QProgressBar::chunk {{ background-color: {t.accent}; border-radius: 4px; }}"
        )
        self._progress_bar.setVisible(False)
        self._main_layout.addWidget(self._progress_bar)

        self._progress_label = QLabel("")
        self._progress_label.setWordWrap(True)
        self._progress_label.setStyleSheet(f"font-size: 12px; color: {t.fg_muted};")
        self._progress_label.setVisible(False)
        self._main_layout.addWidget(self._progress_label)

        # Action Buttons row with Round Next Buttons
        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(12)
        self._btn_row.addStretch(1)

        # "Remind me later" wears the same platform back arrow as the steps
        # that go back in the installer, not a cancel cross.
        self._cancel_btn = _RoundNextButton(symbol="back", accent=False)
        self._cancel_btn.setText(tr("update_btn_later"))
        self._cancel_btn.setToolTip(tr("update_btn_later"))
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        self._btn_row.addWidget(self._cancel_btn, 0, Qt.AlignVCenter)

        self._action_btn = _RoundNextButton(symbol="next", accent=True)
        # Older callers and the update wiring tests address the primary chip as
        # the download button; it is the same widget.
        self._download_btn = self._action_btn
        action_key = "update_btn_store" if self._store else "update_btn_download"
        self._action_btn.setText(tr(action_key))
        self._action_btn.setToolTip(tr(action_key))
        self._action_btn.clicked.connect(self._on_action_clicked)
        self._btn_row.addWidget(self._action_btn, 0, Qt.AlignVCenter)

        self._main_layout.addLayout(self._btn_row)

        self._download_state = "initial"  # initial -> downloading -> ready
        # The window grows to the notes it has to show and no further. A
        # single-shot wait lets the notes document settle first, so a short
        # release note gives a short dialog instead of a fixed 440 px one.
        QTimer.singleShot(0, self._fit_to_content)

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        """Draw the same bento pane as Settings and release-note cards."""
        from .widgets import _paint_bento_surface

        _paint_bento_surface(self, event)

    def _make_notes_seamless(self) -> None:
        """Let the update card's painted material show through the notes area.

        A QTextBrowser paints its own Base plate by default. That plate is a
        different shade from the rounded card behind it (and an obvious dark
        rectangle on the glass themes), so both the scroll area and its
        viewport are cleared to the card instead of to a theme surface.
        """
        clear = QColor(0, 0, 0, 0)
        for widget in (self._notes, self._notes.viewport()):
            if widget is None:
                continue
            widget.setAutoFillBackground(False)
            widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            palette = widget.palette()
            for group in (
                QPalette.ColorGroup.Active,
                QPalette.ColorGroup.Inactive,
                QPalette.ColorGroup.Disabled,
            ):
                palette.setColor(group, QPalette.ColorRole.Base, clear)
                palette.setColor(group, QPalette.ColorRole.Window, clear)
            widget.setPalette(palette)
        self._notes.viewport().update()

    def _fit_to_content(self) -> None:
        """Keep the notes inside the host's width and height; never resize the window.

        The notes wrap to the host's width and take the space that is left, so
        the update view follows the window instead of widening it.
        """
        try:
            self._notes.setMinimumHeight(120)
            self._notes.setMaximumHeight(16777215)
        except Exception:
            pass

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        # Stack the two action chips into a column when the window is too
        # narrow for them side by side, so their labels are never clipped.
        try:
            narrow = self.width() < 420
            direction = QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
            if self._btn_row.direction() != direction:
                self._btn_row.setDirection(direction)
        except Exception:
            pass

    def _on_cancel_clicked(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(500)
            self._worker = None
        self.reject()

    def _on_action_clicked(self):
        if self._store:
            from ..store_package import open_store_updates

            open_store_updates()
            self.accept()
            return
        if self._download_state == "ready":
            self._launch_installer()
            return
        self._start_update_download()

    @staticmethod
    def _remove_older_update_packages(folder, keep: str) -> None:
        """Delete earlier Updater CE packages in ``folder``, keeping ``keep``."""
        import re

        pattern = re.compile(r"^(UpdaterCE-Setup-|Innioasis\.Updater)", re.IGNORECASE)
        try:
            entries = list(folder.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.name == keep or not entry.is_file():
                continue
            if not pattern.match(entry.name):
                continue
            try:
                entry.unlink()
            except OSError as e:
                logger.debug("Could not remove older update package %s: %s", entry, e)

    def _start_update_download(self):
        import os
        import subprocess
        import tempfile
        from pathlib import Path
        from PySide6.QtWidgets import QApplication
        from ..browser import open_browser
        from ..downloads import DownloadWorker

        # Always download the newest release, not whatever this dialog was
        # opened with. The lookup can only refresh the asset; if it fails the
        # asset already on screen is used.
        try:
            from ..config import UPDATE_REPO
            from ..updates import UpdateChecker

            latest = UpdateChecker(UPDATE_REPO).simulate_latest(force=True)
            if latest and latest.tag and latest.assets:
                self._info = latest
                self._asset = pick_platform_asset(latest.assets)
        except Exception as e:
            logger.debug("Could not refresh the latest update before download: %s", e)

        # Identify platform download asset
        target_asset = self._asset
        if not target_asset and self._info:
            target_asset = pick_platform_asset(self._info.assets)

        # Linux: AppImages and portable tar.gz / zip packages are downloaded.
        # Fall back to the guide only when no suitable asset exists.
        if sys.platform.startswith("linux") and not target_asset:
            open_browser("https://innioasis.app/guide.html")
            self.accept()
            return

        if not target_asset:
            open_browser(self._info.html_url if self._info else "https://innioasis.app/guide.html")
            self.accept()
            return

        url = target_asset.get("browser_download_url") or ""
        if not url:
            open_browser(self._info.html_url or "https://innioasis.app/guide.html")
            self.accept()
            return

        filename = target_asset.get("name") or (
            "Innioasis.Updater.for.Mac.dmg" if sys.platform == "darwin" else f"UpdaterCE-Setup-{self._info.version}.exe"
        )

        if sys.platform == "darwin" or sys.platform.startswith("linux"):
            dest_dir = Path.home() / "Downloads"
            dest_dir.mkdir(parents=True, exist_ok=True)
            self._dest_path = dest_dir / filename
        else:
            self._dest_path = Path(tempfile.gettempdir()) / filename

        # A file from an earlier download of this name is not this release. The
        # downloader resumes any file it finds, so an old installer would be
        # kept as the start of the new one. Clear it, and any partial copy.
        for stale in (self._dest_path, Path(f"{self._dest_path}.part")):
            try:
                stale.unlink(missing_ok=True)
            except OSError as e:
                logger.debug("Could not remove stale update file %s: %s", stale, e)
        # Older app packages from earlier downloads are removed too, so only the
        # latest build is ever kept on disk.
        self._remove_older_update_packages(self._dest_path.parent, keep=self._dest_path.name)

        # Switch to wizard progress view
        self._download_state = "downloading"
        self._notes.setVisible(False)
        self._notes_label.setVisible(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._progress_label.setVisible(True)
        self._progress_label.setText(f"Connecting... ({filename})")
        self._action_btn.setEnabled(False)

        worker = DownloadWorker(url, str(self._dest_path), parent=self)
        self._worker = worker

        # DownloadWorker.progress carries a 0-100 int and .status the byte/speed
        # line. Connecting to a three-argument slot left the label frozen at
        # "Connecting..." for the whole download, so wire each signal to a
        # handler that takes exactly what it emits.
        def on_progress(pct):
            try:
                value = int(pct)
            except (TypeError, ValueError):
                return
            self._progress_bar.setValue(max(0, min(100, value)))

        def on_status(text):
            if text:
                self._progress_label.setText(str(text))

        def on_done(ok, err):
            if ok and self._dest_path and self._dest_path.is_file():
                self._progress_bar.setValue(100)
                self._progress_label.setText(f"Saved to: {self._dest_path}")
                if sys.platform == "darwin":
                    # macOS: reveal in Finder and quit
                    try:
                        subprocess.Popen(["open", "-R", str(self._dest_path)])
                    except Exception as e:
                        logger.debug("Failed to reveal dmg in finder: %s", e)
                    QApplication.quit()
                    return

                if sys.platform.startswith("linux"):
                    # Linux portable packages (.tar.gz / .zip): reveal in file manager and quit
                    if not str(self._dest_path).lower().endswith(".appimage"):
                        try:
                            from ..diagnostics import reveal_in_file_manager
                            reveal_in_file_manager(self._dest_path)
                        except Exception as e:
                            logger.debug("Failed to reveal download in file manager: %s", e)
                        QApplication.quit()
                        return

                # Windows and Linux AppImage: ready to install
                self._download_state = "ready"
                from .icons import get_symbol_icon

                self._icon_label.setPixmap(
                    get_symbol_icon("complete", 40, color=T().fg).pixmap(40, 40)
                )
                self._progress_label.setText("Ready to install.")
                self._action_btn.setEnabled(True)
                self._action_btn.setText(tr("install"))
                self._action_btn.setToolTip(tr("install"))
            else:
                self._progress_label.setText(f"Download failed: {err or 'Network error'}")
                self._action_btn.setEnabled(True)
                open_browser(url)
                self.accept()

        worker.progress.connect(on_progress)
        worker.status.connect(on_status)
        worker.finished.connect(on_done)
        worker.start()

    def _launch_installer(self):
        import os
        import subprocess
        from PySide6.QtWidgets import QApplication

        if self._dest_path and self._dest_path.is_file():
            if sys.platform.startswith("linux"):
                # An AppImage is a program: make it executable and run it
                # directly (no shell), then let this older build exit.
                try:
                    self._dest_path.chmod(self._dest_path.stat().st_mode | 0o755)
                    subprocess.Popen([str(self._dest_path)], start_new_session=True)
                except Exception as e:
                    logger.error("Could not start the AppImage %s: %s", self._dest_path, e)
                    self._progress_label.setText(f"Could not start the update: {e}")
                    return
            else:
                # The Windows installer understands -update: keep settings,
                # downloads and the existing per-user data instead of treating
                # this as a clean install.
                try:
                    subprocess.Popen([str(self._dest_path), "-update"])
                except Exception:
                    try:
                        os.startfile(str(self._dest_path))
                    except Exception:
                        subprocess.Popen([str(self._dest_path), "-update"], shell=True)
            QApplication.quit()
        else:
            self.reject()

    def _on_skip_version(self):
        if self._on_skip and self._info:
            self._on_skip(self._info.version)
        self.reject()


def _symbol_label(symbol: str, size: int, color: str) -> QLabel:
    """A small host-drawn symbol label, for headers and status rows."""
    from .icons import get_symbol_icon

    label = QLabel()
    label.setPixmap(get_symbol_icon(symbol, size, color=color).pixmap(size, size))
    label.setStyleSheet("border: none; background: transparent;")
    return label


class LinuxSetupDialog(QDialog):
    """User-friendly Linux environment onboarding and SP Flash Tool setup."""

    def __init__(self, parent=None, auto_start: bool = False):
        super().__init__(parent)
        apply_dialog_theme(self)
        from .. import linux_sp_flash
        self._linux = linux_sp_flash
        self._worker = None
        self._report = {}

        t = T()
        self.setWindowTitle(tr("linux_setup_title"))
        self.resize(700, 580)
        self.setMinimumWidth(580)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 20)
        main_layout.setSpacing(16)

        # Header
        header_row = QHBoxLayout()
        header_row.setSpacing(14)
        icon_badge = _symbol_label("tools", 26, t.fg)
        icon_badge.setStyleSheet(
            f"background-color: {t.accent_bg}; "
            f"border-radius: 16px; padding: 6px 12px;"
        )
        header_row.addWidget(icon_badge)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(2)
        self._title_lbl = QLabel(tr("linux_setup_welcome"))
        self._title_lbl.setStyleSheet(f"font-size: 18px; font-weight: 800; color: {t.fg};")
        self._desc_lbl = QLabel(tr("linux_setup_welcome_desc"))
        self._desc_lbl.setWordWrap(True)
        self._desc_lbl.setStyleSheet(f"font-size: 12px; color: {t.fg};")
        header_text_layout.addWidget(self._title_lbl)
        header_text_layout.addWidget(self._desc_lbl)
        header_row.addLayout(header_text_layout)
        main_layout.addLayout(header_row)

        # Download / Staging Progress Bar
        self._progress_box = QWidget()
        prog_layout = QVBoxLayout(self._progress_box)
        prog_layout.setContentsMargins(0, 0, 0, 0)
        prog_layout.setSpacing(6)

        self._progress_msg = QLabel(tr("linux_card_downloading_badge"))
        self._progress_msg.setStyleSheet(f"font-size: 12px; color: {t.fg}; font-weight: 500;")
        prog_layout.addWidget(self._progress_msg)

        self._progress_bar = _InstallProgress()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        # Left to the native QStyle: styling a progress bar with QSS replaces
        # the platform's own bar/track rendering.
        prog_layout.addWidget(self._progress_bar)
        main_layout.addWidget(self._progress_box)

        # Cards Container
        cards_layout = QVBoxLayout()
        cards_layout.setSpacing(10)

        # Card 1: Flashing Engine
        self._engine_card = QWidget()
        self._engine_card.setStyleSheet(
            f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border}; "
            f"border-radius: 12px; padding: 12px; }}"
        )
        ec_layout = QHBoxLayout(self._engine_card)
        ec_layout.setContentsMargins(14, 12, 14, 12)
        ec_icon = _symbol_label("settings", 20, t.fg)
        ec_layout.addWidget(ec_icon)

        ec_text = QVBoxLayout()
        ec_text.setSpacing(2)
        ec_title = QLabel(tr("linux_card_engine_title"))
        ec_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {t.fg}; border: none; background: transparent;")
        self._ec_desc = QLabel(tr("linux_card_engine_desc"))
        self._ec_desc.setStyleSheet(f"font-size: 12px; color: {t.fg}; border: none; background: transparent;")
        ec_text.addWidget(ec_title)
        ec_text.addWidget(self._ec_desc)
        ec_layout.addLayout(ec_text)
        ec_layout.addStretch()

        self._engine_badge = QLabel(tr("linux_card_downloading_badge"))
        self._engine_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; "
            f"background-color: {t.accent_bg}; color: {t.fg}; border: none;"
        )
        ec_layout.addWidget(self._engine_badge)
        cards_layout.addWidget(self._engine_card)

        # Card 2: USB Device Access (udev)
        self._usb_card = QWidget()
        self._usb_card.setStyleSheet(
            f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border}; "
            f"border-radius: 12px; padding: 12px; }}"
        )
        uc_layout = QHBoxLayout(self._usb_card)
        uc_layout.setContentsMargins(14, 12, 14, 12)
        uc_icon = _symbol_label("install", 20, t.fg)
        uc_layout.addWidget(uc_icon)

        uc_text = QVBoxLayout()
        uc_text.setSpacing(2)
        uc_title = QLabel(tr("linux_card_usb_title"))
        uc_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {t.fg}; border: none; background: transparent;")
        self._uc_desc = QLabel(tr("linux_card_usb_desc"))
        self._uc_desc.setStyleSheet(f"font-size: 12px; color: {t.fg}; border: none; background: transparent;")
        uc_text.addWidget(uc_title)
        uc_text.addWidget(self._uc_desc)
        uc_layout.addLayout(uc_text)
        uc_layout.addStretch()

        self._grant_btn = QPushButton(tr("linux_card_grant_btn"))
        self._grant_btn.clicked.connect(self._on_auto_configure)
        uc_layout.addWidget(self._grant_btn)

        self._usb_badge = QLabel(tr("linux_card_granted_badge"))
        self._usb_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; "
            f"background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;"
        )
        uc_layout.addWidget(self._usb_badge)
        cards_layout.addWidget(self._usb_card)

        # Card 3: System Environment
        self._sys_card = QWidget()
        self._sys_card.setStyleSheet(
            f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border}; "
            f"border-radius: 12px; padding: 12px; }}"
        )
        sc_layout = QHBoxLayout(self._sys_card)
        sc_layout.setContentsMargins(14, 12, 14, 12)
        sc_icon = _symbol_label("diagnostics", 20, t.fg)
        sc_layout.addWidget(sc_icon)

        sc_text = QVBoxLayout()
        sc_text.setSpacing(2)
        sc_title = QLabel(tr("linux_card_system_title"))
        sc_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {t.fg}; border: none; background: transparent;")
        self._sys_desc = QLabel("Linux x86_64")
        self._sys_desc.setStyleSheet(f"font-size: 12px; color: {t.fg}; border: none; background: transparent;")
        sc_text.addWidget(sc_title)
        sc_text.addWidget(self._sys_desc)
        sc_layout.addLayout(sc_text)
        sc_layout.addStretch()

        self._sys_badge = QLabel(tr("linux_card_ready_badge"))
        self._sys_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; "
            f"background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;"
        )
        sc_layout.addWidget(self._sys_badge)
        cards_layout.addWidget(self._sys_card)

        main_layout.addLayout(cards_layout)

        # Technical diagnostics toggle & view (hidden by default)
        diag_toggle_row = QHBoxLayout()
        self._diag_toggle_btn = QPushButton(f"▸ {tr('linux_setup_view_log')}")
        self._diag_toggle_btn.setStyleSheet(
            f"color: {t.fg}; border: none; background: transparent; font-size: 11px; text-align: left;"
        )
        self._diag_toggle_btn.clicked.connect(self._toggle_diagnostics)
        diag_toggle_row.addWidget(self._diag_toggle_btn)
        diag_toggle_row.addStretch()
        main_layout.addLayout(diag_toggle_row)

        self._status_view = QTextEdit()
        self._status_view.setObjectName("statusView")
        self._status_view.setReadOnly(True)
        self._status_view.setVisible(False)
        self._status_view.setFixedHeight(120)
        # Platform style and the live palette. No stylesheet: that would
        # replace the host scrollbar (see src/ui/scrollbars.py).
        status_font = QFont(self._status_view.font())
        status_font.setStyleHint(QFont.StyleHint.Monospace)
        status_font.setPointSize(10)
        self._status_view.setFont(status_font)
        main_layout.addWidget(self._status_view)

        # Footer Actions
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._recheck_btn = QPushButton(tr("linux_setup_recheck"))
        self._recheck_btn.clicked.connect(lambda: self._start_staging(force_download=False))

        self._copy_btn = QPushButton(tr("linux_setup_copy_cmd"))
        self._copy_btn.clicked.connect(self._on_copy_command)

        self._sp_gui_btn = QPushButton(tr("system_checker_launch_gui_btn"))
        self._sp_gui_btn.clicked.connect(self._on_launch_sp_gui)
        advanced = bool(QSettings("innioasis", "updater").value("advanced_settings", False))
        parent = self.parent()
        if parent is not None and hasattr(parent, "_settings_page"):
            advanced = parent._settings_page.advanced_methods_revealed()
        self._sp_gui_btn.setVisible(advanced)

        self._continue_btn = QPushButton(tr("linux_setup_done_btn"))
        self._continue_btn.setDefault(True)
        self._continue_btn.clicked.connect(self.accept)

        btn_row.addWidget(self._recheck_btn)
        btn_row.addWidget(self._copy_btn)
        btn_row.addWidget(self._sp_gui_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._continue_btn)
        main_layout.addLayout(btn_row)

        # Initial refresh or auto-start
        self.refresh_status()
        stage = self._linux.stage_dir()
        if auto_start and not self._linux.files_ready(stage):
            self._start_staging(force_download=False)
        else:
            self._progress_box.setVisible(not self._linux.files_ready(stage))

    def _stop_worker(self):
        if self._worker and self._worker.isRunning():
            try:
                self._worker.requestInterruption()
                if not self._worker.wait(800):
                    self._worker.terminate()
                    self._worker.wait(500)
            except Exception:
                pass

    def _start_staging(self, force_download: bool = False):
        t = T()
        self._progress_box.setVisible(True)
        self._progress_bar.setValue(10)
        self._progress_msg.setText(tr("linux_connecting_github"))
        self._engine_badge.setText(tr("linux_card_downloading_badge"))
        self._engine_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; "
            f"background-color: {t.accent_bg}; color: {t.fg}; border: none;"
        )

        self._worker = self._linux.LinuxStagingWorker(self, force_download=force_download)
        self._worker.progress.connect(self._on_staging_progress)
        self._worker.finished.connect(self._on_staging_finished)
        self._worker.start()

    def _on_staging_progress(self, pct: int, msg: str):
        self._progress_bar.setValue(pct)
        if msg:
            self._progress_msg.setText(msg)

    def _on_staging_finished(self, ok: bool, msg: str, report: dict):
        self._progress_bar.setValue(100)
        if ok:
            self._progress_msg.setText(tr("linux_engine_ready_msg"))
            QTimer.singleShot(1200, lambda: self._progress_box.setVisible(False))
        else:
            self._progress_msg.setText(tr("linux_setup_issue_fmt").format(msg=msg))
        self.refresh_status()

    def _toggle_diagnostics(self):
        is_visible = self._status_view.isVisible()
        self._status_view.setVisible(not is_visible)
        text_key = "linux_setup_view_log" if is_visible else "linux_setup_hide_log"
        arrow = "▸" if is_visible else "▾"
        self._diag_toggle_btn.setText(f"{arrow} {tr(text_key)}")

    def refresh_status(self):
        t = T()
        report = self._linux.run_system_checker()
        distro = report.get("distro", {})

        # System card
        self._sys_desc.setText(f"{distro.get('pretty_name', 'Linux')} ({distro.get('family', 'generic')}) • x86_64")
        if report.get("arch_ok"):
            self._sys_badge.setText(tr("linux_card_ready_badge"))
            self._sys_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;")
        else:
            self._sys_badge.setText(tr("linux_unsupported_badge"))
            self._sys_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.danger_bg}; color: {t.danger_fg}; border: none;")

        # Engine card
        if report.get("sp_exec_ok"):
            self._engine_badge.setText(tr("linux_card_ready_badge"))
            self._engine_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;")
            self._ec_desc.setText(tr("linux_engine_staged_desc"))
        elif self._worker and self._worker.isRunning():
            self._engine_badge.setText(tr("linux_card_downloading_badge"))
            self._engine_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.accent_bg}; color: {t.fg}; border: none;")
        else:
            self._engine_badge.setText(tr("linux_card_action_badge"))
            self._engine_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.warn_bg}; color: {t.warn_fg}; border: none;")
            self._ec_desc.setText(tr("linux_engine_recheck_desc"))

        # USB permissions card
        if report.get("udev_ok"):
            self._usb_badge.setVisible(True)
            self._grant_btn.setVisible(False)
            self._usb_badge.setText(tr("linux_card_granted_badge"))
            self._usb_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;")
            self._uc_desc.setText(tr("linux_usb_rules_active_desc"))
        else:
            self._usb_badge.setVisible(False)
            self._grant_btn.setVisible(True)
            self._uc_desc.setText(tr("linux_usb_grant_desc"))

        # Bottom Continue button
        if report.get("overall_ready"):
            self._continue_btn.setEnabled(True)
        else:
            self._continue_btn.setEnabled(True)

        # Technical logs & full system checker checklist
        lines = []
        lines.append("=== SP Flash Tool Verification Report ===")
        lines.append(f"Target Distribution: {distro.get('pretty_name')} ({distro.get('family')})")
        lines.append(f"Overall Flashing Status: {'READY TO FLASH' if report.get('overall_ready') else 'ACTION NEEDED'}")
        lines.append("")
        lines.append("Diagnostic Checklist:")
        for item in report.get("items", []):
            mark = "[PASS]" if item["status"] == "ok" else ("[WARN]" if item["status"] in ("warn", "info") else "[FAIL]")
            lines.append(f"  {mark:<7} {item['title']}: {item['badge']}")
            lines.append(f"          Detail: {item['detail']}")
        lines.append("")
        lines.append(f"Stage Directory: {report.get('stage_dir')}")
        lines.append(f"Setup Script: {report.get('setup_script_path')}")
        self._status_view.setPlainText("\n".join(lines))
        self._report = report

    def _on_auto_configure(self):
        from PySide6.QtWidgets import QMessageBox
        ok, msg = self._linux.auto_fix_permissions()
        if ok:
            QMessageBox.information(
                self, tr("linux_perms_ok_title"), tr("linux_perms_ok_msg")
            )
        else:
            QMessageBox.warning(self, tr("perm_setup_title"), msg)
        self.refresh_status()

    def _on_copy_command(self):
        from PySide6.QtWidgets import QApplication, QMessageBox
        script_path = self._report.get("setup_script_path", "")
        cmd = f"sudo bash {script_path}"
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(cmd)
            QMessageBox.information(
                self, tr("linux_copied_title"),
                tr("linux_copied_msg_fmt").format(cmd=cmd)
            )

    def _on_launch_sp_gui(self):
        from .. import sp_flash_gui
        from PySide6.QtWidgets import QMessageBox
        ok, msg = sp_flash_gui.open_sp_flash_tool_gui()
        if not ok:
            QMessageBox.warning(self, tr("sp_gui_error_title"), msg)

    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

    def reject(self):
        self._stop_worker()
        super().reject()

    def accept(self):
        self._stop_worker()
        super().accept()


def format_markdown_release_notes(body: str, name: str = "") -> str:
    """Format release notes markdown into clean HTML with native fonts and typography."""
    t = T()
    lines = []
    if name:
        lines.append(f"### {name}")
        lines.append("")
    clean_body = (body or tr("update_no_notes")).strip()
    lines.append(clean_body[:4000])
    raw_md = "\n".join(lines)

    # 1. Clean markdown & remove images per native design
    cleaned_md = re.sub(r"!\[.*?\]\(.*?\)", "", raw_md)
    cleaned_md = re.sub(r"<img[^>]*>", "", cleaned_md, flags=re.IGNORECASE)
    cleaned_md = re.sub(r"\[\s*\]\(.*?\)", "", cleaned_md)

    doc = QTextDocument()
    doc.setMarkdown(cleaned_md)
    html = doc.toHtml()

    # 2. Re-style links with accent color & underline
    html = re.sub(
        r'<a\s+href="([^"]+)">',
        f'<a href="\\1" style="color: {t.fg}; font-weight: 700; text-decoration: none;">',
        html,
    )

    # 3. Inject native rich typography CSS
    css = f"""
    <style type="text/css">
    body {{
        color: {t.fg};
        font-size: 13px;
        line-height: 1.45;
        background: transparent;
        margin: 0;
        padding: 0;
    }}
    h1, h2, h3, h4 {{
        color: {t.fg};
        margin-top: 10px;
        margin-bottom: 6px;
        font-weight: 700;
    }}
    p {{
        margin-top: 4px;
        margin-bottom: 8px;
        color: {t.fg};
        white-space: normal;
        word-wrap: break-word;
    }}
    ul, ol {{
        margin-left: 18px;
        margin-top: 4px;
        margin-bottom: 8px;
    }}
    li {{
        margin-bottom: 3px;
        color: {t.fg};
        white-space: normal;
        word-wrap: break-word;
    }}
    code {{
        background-color: {t.bg_input};
        color: {t.fg};
        padding: 2px 4px;
        border-radius: 3px;
    }}
    </style>
    """
    return re.sub(r"<head>", f"<head>{css}", html, count=1)


class ReleaseReminderDialog(QDialog):
    """Dialog alerting the user that a newer firmware release is available for their device."""

    def __init__(
        self,
        parent=None,
        update_info=None,
        on_view_release=None,
        on_disable_reminders=None,
        on_start_install=None,
        flash_method="",
    ):
        super().__init__(parent)
        apply_dialog_theme(self)
        self.update_info = update_info or {}
        self.on_view_release = on_view_release
        self.on_disable_reminders = on_disable_reminders
        self.on_start_install = on_start_install
        self.flash_method = flash_method
        self._install_started = False

        model = self.update_info.get("model", "Y1")
        device_label = device_label_for_model(model)
        sw_name = self.update_info.get("software_name", "Firmware")
        installed_lbl = (
            self.update_info.get("installed_label")
            or self.update_info.get("installed_tag")
            or ""
        )
        latest_lbl = (
            self.update_info.get("latest_label")
            or self.update_info.get("latest_tag")
            or ""
        )

        self.setWindowTitle(tr("reminder_new_release_title"))
        self.setMinimumWidth(520)
        self.resize(560, 480)
        t = T()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # Header with title and device badge
        hdr_row = QHBoxLayout()
        title_lbl = QLabel(
            f"<h3 style='margin:0; color:{t.fg_primary};'>{tr('reminder_new_release_title')}</h3>"
        )
        title_lbl.setTextFormat(Qt.RichText)
        hdr_row.addWidget(title_lbl, 1)

        badge = QLabel(device_label)
        badge.setStyleSheet(
            f"background-color: {t.accent}; color: {t.fg};"
            f" border-radius: 10px; font-size: 11px; font-weight: 700; padding: 3px 10px;"
        )
        hdr_row.addWidget(badge)
        layout.addLayout(hdr_row)

        # Message
        msg = QLabel(
            tr("reminder_new_release_msg").format(
                device=device_label, software=sw_name
            )
        )
        msg.setTextFormat(Qt.RichText)
        msg.setWordWrap(True)
        msg.setStyleSheet(f"font-size: 13px; color: {t.fg}; line-height: 1.4;")
        layout.addWidget(msg)

        # Comparison Card
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: {t.bg_card};"
            f" border: 1px solid {t.border};"
            f" border-radius: 10px; padding: 12px; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(8)

        inst_row = QHBoxLayout()
        inst_title = QLabel(tr("reminder_installed_version"))
        inst_title.setStyleSheet(f"color: {t.fg}; font-size: 12px;")
        inst_val = QLabel(installed_lbl)
        inst_val.setStyleSheet(f"color: {t.fg}; font-size: 12px; font-weight: 600;")
        inst_row.addWidget(inst_title)
        inst_row.addStretch()
        inst_row.addWidget(inst_val)
        card_layout.addLayout(inst_row)

        latest_row = QHBoxLayout()
        latest_title = QLabel(tr("reminder_latest_version"))
        latest_title.setStyleSheet(f"color: {t.fg_primary}; font-size: 12px; font-weight: 600;")
        latest_val = QLabel(latest_lbl)
        latest_val.setStyleSheet(f"color: {t.ok_fg}; font-size: 12px; font-weight: 700;")
        latest_row.addWidget(latest_title)
        latest_row.addStretch()
        latest_row.addWidget(latest_val)
        card_layout.addLayout(latest_row)

        layout.addWidget(card)

        # Release Notes Markdown Display
        latest_rel = self.update_info.get("latest_release") or {}
        notes_body = latest_rel.get("body", "")
        if notes_body:
            notes_hdr = QLabel(f"<b>{tr('update_notes')}</b>")
            notes_hdr.setStyleSheet(f"color: {t.fg}; font-size: 12px; margin-top: 2px;")
            layout.addWidget(notes_hdr)

            self._notes_view = QTextBrowser()
            self._notes_view.setObjectName("updateNotes")
            self._notes_view.setOpenExternalLinks(True)
            self._notes_view.setMinimumHeight(130)
            self._notes_view.document().setDefaultStyleSheet(
                f"a {{ color: {t.fg}; font-weight: 700; text-decoration: none; }}"
            )
            # Platform style and the live palette. No widget stylesheet:
            # that would replace the host scrollbar (see src/ui/scrollbars.py).
            html = format_markdown_release_notes(notes_body, latest_rel.get("name", ""))
            self._notes_view.setHtml(html)
            layout.addWidget(self._notes_view, 1)

        # Method explanation & install prompt
        if paths.IS_MAC:
            method_str = tr("flash_method_mtk")
        else:
            m_lower = (self.flash_method or "").lower()
            if m_lower == "sp":
                method_str = tr("flash_method_sp")
            elif m_lower in ("mtk", "mtk_mac"):
                method_str = tr("flash_method_mtk")
            elif self.flash_method:
                method_str = self.flash_method
            else:
                method_str = ""

        if method_str:
            prompt_text = tr("reminder_prompt_install").format(method=method_str)
        else:
            prompt_text = tr("reminder_prompt_install_generic")

        self._prompt_lbl = QLabel(prompt_text)
        self._prompt_lbl.setWordWrap(True)
        self._prompt_lbl.setStyleSheet(f"font-size: 13px; color: {t.fg}; font-weight: 500; line-height: 1.4;")
        layout.addWidget(self._prompt_lbl)

        # Don't remind about this release checkbox
        self.cb_dont_remind = QCheckBox(tr("reminder_dont_remind_release"))
        layout.addWidget(self.cb_dont_remind)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        btn_dismiss = QPushButton(tr("reminder_dismiss"))
        btn_dismiss.clicked.connect(self._on_dismiss)
        btn_layout.addWidget(btn_dismiss)

        self._btn_install = QPushButton(tr("reminder_start_install"))
        self._btn_install.setDefault(True)
        self._btn_install.clicked.connect(self._on_start_install)
        btn_layout.addWidget(self._btn_install)

        layout.addLayout(btn_layout)

    def _check_disable_opt_out(self):
        if self.cb_dont_remind.isChecked():
            model = self.update_info.get("model", "")
            tag = self.update_info.get("latest_tag", "")
            try:
                from .. import device_tracking
                if model and tag:
                    device_tracking.set_release_skipped(model, tag, True)
            except Exception:
                pass
            if callable(self.on_disable_reminders) and model:
                self.on_disable_reminders(model)

    def _on_dismiss(self):
        self._check_disable_opt_out()
        self.reject()

    def _on_start_install(self):
        self._install_started = True
        self._check_disable_opt_out()
        self.accept()
        if callable(self.on_start_install):
            self.on_start_install(self.update_info)
        elif callable(self.on_view_release):
            self.on_view_release(self.update_info)

    def _on_view(self):
        self._on_start_install()


# Alias for cross-platform and explicit system checking invocations
SystemCheckerDialog = LinuxSetupDialog


class LegacyMigrationDialog(QDialog):
    """Dialog prompting users to clean up legacy pre-3.0 installations and reclaim disk space."""

    def __init__(self, parent=None, scan_info: dict = None):
        super().__init__(parent)
        self.setWindowTitle(tr("legacy_cleanup_title"))
        self.setMinimumWidth(520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self.scan_info = scan_info
        if self.scan_info is None:
            from ..legacy_cleanup import detect_legacy_installations
            self.scan_info = detect_legacy_installations()

        self._build_ui()

    def _build_ui(self):
        t = T()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Header Title
        title_lbl = QLabel(tr("legacy_cleanup_title"))
        title_lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {t.fg};")
        layout.addWidget(title_lbl)

        # Description
        desc_lbl = QLabel(tr("legacy_cleanup_desc"))
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"font-size: 13px; color: {t.fg}; line-height: 1.4;")
        layout.addWidget(desc_lbl)

        # Reclaim Space Badge / Label
        from ..legacy_cleanup import format_bytes
        est_bytes = self.scan_info.get("estimated_bytes", 0)
        reclaim_text = tr("legacy_cleanup_reclaim").format(size=format_bytes(est_bytes))
        reclaim_lbl = QLabel(reclaim_text)
        reclaim_lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #22c55e;")
        layout.addWidget(reclaim_lbl)

        # Detected Items List
        items = self.scan_info.get("items_summary", [])
        if items:
            items_box = QGroupBox("Detected Components")
            items_box_layout = QVBoxLayout(items_box)
            items_box_layout.setContentsMargins(12, 10, 12, 10)
            items_box_layout.setSpacing(6)
            for it in items:
                lbl = QLabel(f"• {it}")
                lbl.setStyleSheet("font-size: 12px; font-family: monospace;")
                items_box_layout.addWidget(lbl)
            layout.addWidget(items_box)

        # Checkbox: Remove Legacy App & Library
        self.cb_remove_legacy = QCheckBox(tr("legacy_cleanup_remove_app_cb"))
        self.cb_remove_legacy.setChecked(True)
        self.cb_remove_legacy.setStyleSheet(f"font-size: 13px; color: {t.fg}; font-weight: 500;")
        layout.addWidget(self.cb_remove_legacy)

        # Checkbox: Android Platform Tools (ADB/Fastboot)
        if self.scan_info.get("has_platform_tools"):
            self.cb_keep_platform_tools = QCheckBox(tr("legacy_cleanup_keep_platform_tools_cb"))
            self.cb_keep_platform_tools.setChecked(True)
            self.cb_keep_platform_tools.setStyleSheet(f"font-size: 13px; color: {t.fg}; font-weight: 500;")
            layout.addWidget(self.cb_keep_platform_tools)

            pt_hint = QLabel(tr("legacy_cleanup_platform_tools_hint"))
            pt_hint.setWordWrap(True)
            pt_hint.setStyleSheet(f"font-size: 11px; color: {t.fg_muted}; margin-left: 22px;")
            layout.addWidget(pt_hint)

        layout.addSpacing(6)

        # Button Layout
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        btn_later = QPushButton(tr("legacy_cleanup_btn_later"))
        btn_later.clicked.connect(self.reject)
        btn_layout.addWidget(btn_later)

        self.btn_clean = QPushButton(tr("legacy_cleanup_btn_clean"))
        self.btn_clean.setDefault(True)
        self.btn_clean.clicked.connect(self._on_clean)
        btn_layout.addWidget(self.btn_clean)

        layout.addLayout(btn_layout)

    def _on_clean(self):
        self.btn_clean.setEnabled(False)
        self.btn_clean.setText("Cleaning up...")
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        from .. import paths
        from ..legacy_cleanup import remove_macos_legacy, remove_linux_legacy

        remove_app = self.cb_remove_legacy.isChecked()
        keep_pt = getattr(self, "cb_keep_platform_tools", None)
        uninstall_pt = bool(keep_pt is not None and not keep_pt.isChecked())

        if paths.IS_MAC:
            success, removed, errors = remove_macos_legacy(
                remove_apps=remove_app,
                remove_app_support=remove_app,
                uninstall_platform_tools=uninstall_pt,
            )
        else:
            success, removed, errors = remove_linux_legacy(
                remove_install_dir=remove_app,
                uninstall_platform_tools=uninstall_pt,
            )

        if errors:
            err_msg = "\n".join(errors)
            QMessageBox.warning(
                self,
                tr("legacy_cleanup_title"),
                f"Some items could not be removed:\n\n{err_msg}",
            )
        else:
            QMessageBox.information(
                self,
                tr("legacy_cleanup_done_title"),
                tr("legacy_cleanup_done_msg"),
            )
        self.accept()


class InstallConsoleDialog(QDialog):
    """Small live view of the install tool's terminal. Closing it does not cancel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowTitle(tr("install_output_title"))
        self.resize(560, 360)
        self._stick = True
        layout = QVBoxLayout(self)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText(tr("install_output_empty"))
        layout.addWidget(self._log)
        self._log.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def set_lines(self, lines) -> None:
        filtered = []
        for ln in lines or []:
            raw = str(ln or "").strip()
            if not raw or set(raw) <= {'.'}:
                continue
            if any(w in raw for w in ("RuntimeWarning", "innioasis.startup")):
                continue
            if raw.startswith("."):
                stripped = raw.lstrip(". ")
                if not stripped or stripped == "Preloader":
                    continue
                ln = stripped
            filtered.append(str(ln))
        self._log.setPlainText("\n".join(filtered))
        if self._stick:
            self._log.moveCursor(QTextCursor.MoveOperation.End)

    def append_line(self, line) -> None:
        if not self.isVisible():
            return
        raw = str(line or "").strip()
        if not raw or set(raw) <= {'.'}:
            return
        if any(w in raw for w in ("RuntimeWarning", "innioasis.startup")):
            return
        if raw.startswith("."):
            stripped = raw.lstrip(". ")
            if not stripped or stripped == "Preloader":
                return
            line = stripped
        self._log.append(str(line))
        if self._stick:
            self._log.moveCursor(QTextCursor.MoveOperation.End)

    def _on_scroll(self, value: int) -> None:
        bar = self._log.verticalScrollBar()
        self._stick = value >= max(0, bar.maximum() - 2)

    def retranslate(self) -> None:
        self.setWindowTitle(tr("install_output_title"))
        self._log.setPlaceholderText(tr("install_output_empty"))

