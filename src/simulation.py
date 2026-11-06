"""Ctrl/Cmd+G: complete the hardware stages virtually, for UI testing.

Two stages in this app cannot be exercised without a player: the firmware
write (SP Flash Tool or MTKClient, twenty minutes or more) and the Rockbox
themes/fonts push over ADB. The switch here makes both report what a
successful run reports, in seconds, through the same Qt signals and the same
widgets the real stages drive, so every route after them — completion, retry,
donation appeal, themes hand-off, completion notes — can be walked by hand.

It is a developer aid, never a user feature: off at every launch, held in
memory only, and reachable only from the keyboard. Nothing here alters a
hardware path — with the switch off the ordinary service and ADB code runs
exactly as before.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

_ENABLED = False


def enabled() -> bool:
    """Whether hardware stages currently complete themselves."""
    return _ENABLED


def set_enabled(value: bool) -> bool:
    global _ENABLED
    _ENABLED = bool(value)
    return _ENABLED


def toggle() -> bool:
    return set_enabled(not _ENABLED)


class FlashRun(QObject):
    """The signals a successful ``FlashService`` run emits, over seconds.

    Emitted on the service itself, so the window's own connections — the same
    ones a real backend reaches — drive the card, the state machine and the
    completion routing. Only the hardware is missing.
    """

    finished = Signal()

    #: Ticks of "please wait" before the device is "found", then before the
    #: write starts, then before the result is reported.
    WAIT_TICKS = 3
    DETECT_TICKS = 3
    FINISH_TICKS = 3
    #: Percent per write tick. The write phase ends short of 100 because the
    #: real worker keeps the last of the bar for its final verification.
    PERCENT_STEP = 6

    def __init__(self, service, parent=None, tick_ms: int = 140):
        super().__init__(parent)
        self.service = service
        self._tick_ms = max(40, int(tick_ms))
        self._timer = QTimer(self)
        self._timer.setInterval(self._tick_ms)
        self._timer.timeout.connect(self._tick)
        self._phase = "wait"
        self._ticks = 0
        self._percent = 0

    def start(self) -> None:
        from .flash_service import STEP_PLEASE_WAIT

        self.service.step_changed.emit(STEP_PLEASE_WAIT)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def is_running(self) -> bool:
        return self._timer.isActive()

    def _tick(self) -> None:
        from .flash_service import STEP_DETECT, STEP_DONE, STEP_WAITING

        self._ticks += 1
        if self._phase == "wait":
            if self._ticks >= self.WAIT_TICKS:
                self._phase = "detect"
                self._ticks = 0
                self.service.step_changed.emit(STEP_WAITING)
            return
        if self._phase == "detect":
            if self._ticks >= self.DETECT_TICKS:
                self._phase = "write"
                self._ticks = 0
                self.service.step_changed.emit(STEP_DETECT)
            return
        if self._phase == "write":
            self._percent = min(94, self._percent + self.PERCENT_STEP)
            self.service.progress.emit(self._percent)
            if self._percent >= 94:
                self._phase = "finish"
                self._ticks = 0
                self.service.step_changed.emit(STEP_DONE)
            return
        if self._ticks >= self.FINISH_TICKS:
            self._timer.stop()
            self.finished.emit()
            self.service.flash_finished.emit(True, "")
