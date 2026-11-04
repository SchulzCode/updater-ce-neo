"""Short support headline: a round portrait beside a warm one-line pitch.

The same block is used in two places, and they do not say the same thing:

* the Support page introduces the project (``INTRO_KEYS``), and
* the appeal under a finished install speaks about the install that just
  succeeded (``COMPLETION_KEYS``) — the install is why the reader is here, so
  it is the natural thing to name before asking for anything.

The portrait is circle-cropped. A bundled copy is shown immediately, and the
network image replaces it when it can be loaded. Nothing here paints a solid
plate; the window material shows through the square outside the circle.
"""

from __future__ import annotations

import datetime as _dt
import os
import random
import re
import urllib.request

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import paths
from ..i18n import tr

PORTRAIT_URL = "https://innioasis.app/mtkclient/gui/images/developer.png"
PORTRAIT_DIAMETER = 56

# (headline, subtitle) key pairs. The Support page keeps the project pitch;
# the post-install appeal uses the completion wording.
INTRO_KEYS = ("donate_headline", "donate_subtitle")
COMPLETION_KEYS = ("donate_completion_headline", "donate_completion_subtitle")
# Firmware plus themes pack: the power-on steps already ran before placement.
THEMES_COMPLETION_KEYS = ("donate_completion_headline", "donate_completion_themes_subtitle")

#: The Support Us paragraph rotates through these, so the ask is not the same
#: sentence every time the page or its pop-out modal is opened. Most are single
#: appeals with their own angle (servers, hours, testing, independence); one is
#: the original lead/followup pair, rendered with its bold lead.
LEGACY_APPEAL_KEY = "donate_support_appeal_lead"
SUPPORT_APPEAL_KEYS = (
    LEGACY_APPEAL_KEY,
    "donate_appeal_always_free",
    "donate_appeal_community_archive",
    "donate_appeal_family_and_time",
    "donate_appeal_human_touch",
    "donate_appeal_keep_going",
    "donate_support_appeal_servers",
    "donate_support_appeal_evenings",
    "donate_support_appeal_independent",
    "donate_support_appeal_testbench",
    "donate_support_appeal_bandwidth",
    "donate_support_appeal_monthly_costs",
    "donate_support_appeal_small_change",
    "donate_support_appeal_long_nights",
    "donate_support_appeal_behind_the_scenes",
    "donate_support_appeal_next_feature",
    "donate_support_appeal_kindness",
    "donate_support_appeal_rescue",
    "donate_support_appeal_no_subscription",
    "donate_support_appeal_one_person",
    "donate_support_appeal_archive_forever",
    "donate_support_appeal_coffee_math",
)

#: Ryan and his partner are expecting a child. These are only ever shown to the
#: cohort below, only while the window is open, and never after 17 April 2027.
FAMILY_APPEAL_KEYS = (
    "donate_family_appeal_expected",
    "donate_family_appeal_new_arrival",
    "donate_family_appeal_nursery",
)
FAMILY_COHORT_KEY = "donation_family_appeal_cohort"
FAMILY_COHORT_FRACTION = 0.30
FAMILY_APPEAL_LAST_DAY = _dt.date(2027, 4, 17)

# Relative weights, not probabilities: the family wording shares the rotation
# with every general appeal, and its weight grows on a day with no donations
# logged and on the days the daily/weekly goal is the one being shown.
FAMILY_APPEAL_WEIGHT = 3.0
FAMILY_NO_DONATION_BOOST = 2.0
FAMILY_SHORT_GOAL_BOOST = 1.8

# The last few wordings, so reopening the page does not repeat the same line.
_RECENT_KEYS: list[str] = []

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])\s*")


def _fill(text: str, **fields) -> str:
    """Substitute the placeholders the caller actually has.

    A subtitle is only rewritten for the fields passed in with a real value, so
    a language that phrases the sentence without ``{model}`` — or a caller that
    has no model to name — never leaves a literal ``{model}`` on screen.
    """
    for name, value in fields.items():
        value = str(value or "").strip()
        if value:
            text = text.replace("{%s}" % name, value)
    return text


def family_appeal_window_open(now=None) -> bool:
    """Whether the child-expecting copy may be shown at all.

    It stops the moment 17 April 2027 ends: nothing about the baby is written
    after that date, whatever the stored cohort says.
    """
    now = now or _dt.datetime.now()
    return now.date() <= FAMILY_APPEAL_LAST_DAY


def family_appeal_cohort(settings=None) -> bool:
    """Sticky 30% of installs that also see the child-expecting wording.

    The decision is made once and stored, so the same person gets a consistent
    experience instead of a different coin flip every time the page opens.
    """
    from PySide6.QtCore import QSettings

    store = settings if settings is not None else QSettings("innioasis", "updater")
    raw = store.value(FAMILY_COHORT_KEY, None)
    if raw is None:
        chosen = random.random() < FAMILY_COHORT_FRACTION
        store.setValue(FAMILY_COHORT_KEY, "1" if chosen else "0")
        try:
            store.sync()
        except Exception:
            pass
        return chosen
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _donated_today(donations, now) -> bool:
    for row in donations or ():
        when = row.get("dt")
        if isinstance(when, _dt.datetime):
            when = when.date()
        if when == now.date():
            try:
                if float(row.get("amount") or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _short_period_goal(donations, now) -> bool:
    """True while the bar names the day's or the week's costs, not the month's."""
    from ..donors import get_goal_stats, goal_scope

    scope = None
    if donations:
        try:
            scope = get_goal_stats(donations, now=now).get("scope")
        except Exception:
            scope = None
    return (scope or goal_scope(now)) in ("daily", "weekly")


def _choose(pool, rng=random) -> str:
    """Pick from ``pool``, skipping the last few wordings when it can."""
    fresh = [key for key in pool if key not in _RECENT_KEYS]
    key = rng.choice(fresh or list(pool))
    _RECENT_KEYS.append(key)
    del _RECENT_KEYS[:-4]
    return key


def pick_support_appeal_key(donations=None, now=None, settings=None, rng=random) -> str:
    """The appeal wording for this opening of the Support Us page or modal.

    General appeals all carry the same weight. The family wording, when its
    cohort and its date window both allow it, weighs more on a day with no
    donation logged and while a daily or weekly goal is on screen, so it comes
    round more often exactly when the ask is smallest and most concrete.
    """
    now = now or _dt.datetime.now()
    weight = 0.0
    if family_appeal_window_open(now) and family_appeal_cohort(settings):
        weight = FAMILY_APPEAL_WEIGHT
        if not _donated_today(donations, now):
            weight *= FAMILY_NO_DONATION_BOOST
        if _short_period_goal(donations, now):
            weight *= FAMILY_SHORT_GOAL_BOOST
    if weight > 0 and rng.random() < weight / (weight + len(SUPPORT_APPEAL_KEYS)):
        return _choose(FAMILY_APPEAL_KEYS, rng)
    return _choose(SUPPORT_APPEAL_KEYS, rng)


def _bold_lead(text: str) -> str:
    """Bold the first sentence, leaving the rest as plain copy.

    The whitespace the sentence break consumed is put back exactly as it was:
    Latin copy had a space there, while Chinese and Japanese punctuation runs
    straight on, and inventing a space for them would show a gap in the text.
    """
    text = str(text or "").strip()
    match = _SENTENCE_BREAK.search(text)
    if match is None:
        return text
    lead, rest = text[: match.start()].strip(), text[match.end():].strip()
    if not lead or not rest:
        return text
    return f"<b>{lead}</b>{match.group(0)}{rest}"


def support_appeal_html(key: str) -> str:
    """Rich text for one appeal key, as the Support Us card shows it."""
    key = key or LEGACY_APPEAL_KEY
    if key == LEGACY_APPEAL_KEY:
        return f"<b>{tr('donate_support_appeal_lead')}</b> {tr('donate_support_appeal_followup')}"
    return _bold_lead(tr(key))


def bundled_portrait_path():
    """Shipped portrait, used when the network image cannot be loaded."""
    return paths.RESOURCES_DIR / "developer.png"


def circle_pixmap(source: QPixmap, diameter: int = PORTRAIT_DIAMETER) -> QPixmap:
    """Scale, center-crop, and clip to a circle on a transparent square."""
    diameter = max(1, int(diameter))
    image = QImage(diameter, diameter, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    if source is not None and not source.isNull():
        scaled = source.scaled(
            diameter,
            diameter,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - diameter) // 2)
        y = max(0, (scaled.height() - diameter) // 2)
        cropped = scaled.copy(x, y, diameter, diameter)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        clip.addEllipse(0, 0, diameter, diameter)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, cropped)
        painter.end()
    return QPixmap.fromImage(image)


class _PortraitFetch(QThread):
    finished_bytes = Signal(bytes)

    def run(self) -> None:
        if self.isInterruptionRequested():
            self.finished_bytes.emit(b"")
            return
        try:
            request = urllib.request.Request(
                PORTRAIT_URL,
                headers={"User-Agent": "UpdaterCE"},
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                data = response.read()
        except Exception:
            data = b""
        if self.isInterruptionRequested():
            data = b""
        self.finished_bytes.emit(data)


class DeveloperPortrait(QLabel):
    """Circular developer photo. Bundled file first, network image when it arrives."""

    def __init__(self, diameter: int = PORTRAIT_DIAMETER, parent=None):
        super().__init__(parent)
        self._diameter = int(diameter)
        self._thread = None
        self.setFixedSize(self._diameter, self._diameter)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAutoFillBackground(False)
        self.setStyleSheet("")
        self.setPixmap(circle_pixmap(QPixmap(str(bundled_portrait_path())), self._diameter))
        self.destroyed.connect(self._abandon)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_fetch)
        self._maybe_fetch()

    def apply_bytes(self, data: bytes) -> bool:
        """Replace the portrait when ``data`` is an image. Keeps the bundled one otherwise."""
        if not data:
            return False
        pix = QPixmap()
        if not pix.loadFromData(data) or pix.isNull():
            return False
        self.setPixmap(circle_pixmap(pix, self._diameter))
        return True

    def _maybe_fetch(self) -> None:
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        thread = _PortraitFetch()
        thread.finished_bytes.connect(self.apply_bytes)
        self._thread = thread
        thread.start()

    def _abandon(self, *_args) -> None:
        thread = self._thread
        if thread is None:
            return
        try:
            thread.finished_bytes.disconnect(self.apply_bytes)
        except Exception:
            pass
        thread.requestInterruption()
        if thread.isRunning():
            thread.wait(1000)

    def _stop_fetch(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is None:
            return
        thread.requestInterruption()
        if thread.isRunning():
            thread.wait(2000)


class SupportIntro(QWidget):
    """Portrait aligned with the headline block. No card behind it."""

    def __init__(self, parent=None, keys=INTRO_KEYS):
        super().__init__(parent)
        self._keys = tuple(keys)
        self.setAutoFillBackground(False)
        self.setStyleSheet("")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.portrait = DeveloperPortrait(PORTRAIT_DIAMETER)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        self.headline = QLabel()
        self.headline.setTextFormat(Qt.TextFormat.RichText)
        self.headline.setWordWrap(True)
        self.headline.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        font = self.headline.font()
        font.setPixelSize(18)
        font.setWeight(QFont.Weight.DemiBold)
        self.headline.setFont(font)
        self.headline.setAutoFillBackground(False)
        self.headline.setStyleSheet("")
        self.subtitle = QLabel()
        self.subtitle.setWordWrap(True)
        self.subtitle.setProperty("cssClass", "dimmed")
        self.subtitle.setAutoFillBackground(False)
        column.addWidget(self.headline)
        column.addWidget(self.subtitle)
        row.addWidget(self.portrait, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(column, 1)
        self.retranslate()

    def set_keys(self, keys) -> None:
        """Choose which wording this block shows (see ``INTRO_KEYS``)."""
        self._keys = tuple(keys)
        self.retranslate()

    def retranslate(self, software: str = "", model: str = "") -> None:
        headline_key, subtitle_key = self._keys
        self.headline.setText(tr(headline_key))
        if subtitle_key == "donate_completion_subtitle" and (
            not software or software.startswith(("Get ready to install", "Installing software", "Get your"))
        ):
            self.subtitle.setText(_fill(tr("donate_completion_subtitle_the_software"), model=model))
        else:
            self.subtitle.setText(_fill(tr(subtitle_key), software=software, model=model))
