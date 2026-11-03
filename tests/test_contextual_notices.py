"""Contextual notice targeting: model, hardware revision, repo and version.

The Select Software card shows one notice at a time, chosen for the reader's
selection and for every device they logged for update reminders. The Notices
list keeps showing every active notice.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

import src.remote_flags
from src.notice_service import Notice, NoticeContext, NoticeService, parse_notice_xml


def _service(monkeypatch, notices):
    # The feed switch and the logged devices come from the host otherwise.
    monkeypatch.setattr(src.remote_flags, "is_remote_flag_active", lambda *a, **k: False)
    monkeypatch.setattr(
        "src.player_update_check.get_all_device_installs",
        lambda settings=None: {},
    )
    service = NoticeService()
    service._active_notices = list(notices)
    return service


def test_notice_parses_context_targets():
    notice = parse_notice_xml(
        """<notices><notice id="targeted" models="Y1" device_type="Type A"
        firmware_repo="rockbox-y1/rockbox" firmware_version="v3.2.*,3.1.0">
        <title>Rockbox advisory</title><content>Check your build.</content>
        </notice></notices>"""
    )[0]
    assert notice.models == ("Y1",)
    assert notice.device_types == ("A",)
    assert notice.firmware_repo == "rockbox-y1/rockbox"
    assert notice.firmware_versions == ("v3.2.*", "3.1.0")
    assert notice.is_contextual is True
    assert notice.is_active_for(
        "Y1",
        device_type="A",
        firmware_repo="rockbox-y1/rockbox",
        firmware_version="v3.2.1",
    )
    # A different revision, repository or release does not match.
    assert not notice.is_active_for(
        "Y1", device_type="B", firmware_repo="rockbox-y1/rockbox", firmware_version="v3.2.1"
    )
    assert not notice.is_active_for(
        "Y1", device_type="A", firmware_repo="spycemagic/y2-rockbox", firmware_version="v3.2.1"
    )
    assert not notice.is_active_for(
        "Y1", device_type="A", firmware_repo="rockbox-y1/rockbox", firmware_version="v3.0.0"
    )
    # The leading "v" and a GitHub URL are both conveniences, not requirements.
    assert notice.is_active_for(
        "Y1", device_type="A", firmware_repo="https://github.com/rockbox-y1/rockbox.git",
        firmware_version="3.1.0",
    )


def test_untargeted_notice_is_for_everyone():
    notice = parse_notice_xml(
        "<notices><notice id='all'><title>Hello</title><content>Everyone.</content>"
        "</notice></notices>"
    )[0]
    assert notice.is_contextual is False
    assert notice.is_active_for("Y2", firmware_repo="anything/at-all", firmware_version="v9.9")


def test_logged_device_brings_its_model_notice_to_the_card(monkeypatch):
    y1 = Notice(id="y1", title="Y1", content="Y1 only", models=("Y1",))
    y2 = Notice(id="y2", title="Y2", content="Y2 only", models=("Y2",))
    service = _service(monkeypatch, [y1, y2])
    service.set_context(model="Y1", device_type="A")

    # Only the selected model applies...
    assert [n.id for n in service.contextual_notices()] == ["y1"]

    # ...until that model is logged for update reminders, manually or by an
    # install: then the card may talk about it too.
    monkeypatch.setattr(
        "src.player_update_check.get_all_device_installs",
        lambda settings=None: {
            "Y2": {"model": "Y2", "software_name": "Rockbox", "tag_name": "v1.0.0"}
        },
    )
    monkeypatch.setattr(
        "src.player_update_check.is_device_reminder_enabled",
        lambda model, settings=None: True,
    )
    shown = [n.id for n in service.contextual_notices()]
    assert "y1" in shown and "y2" in shown


def test_reminders_switched_off_keep_the_model_out(monkeypatch):
    y1 = Notice(id="y1", title="Y1", content="Y1 only", models=("Y1",))
    y2 = Notice(id="y2", title="Y2", content="Y2 only", models=("Y2",))
    service = _service(monkeypatch, [y1, y2])
    service.set_context(model="Y1")
    monkeypatch.setattr(
        "src.player_update_check.get_all_device_installs",
        lambda settings=None: {"Y2": {"model": "Y2", "software_name": "Rockbox"}},
    )
    monkeypatch.setattr(
        "src.player_update_check.is_device_reminder_enabled",
        lambda model, settings=None: False,
    )
    assert [n.id for n in service.contextual_notices()] == ["y1"]


def test_repo_notice_leads_for_its_firmware_and_the_list_keeps_everything(monkeypatch):
    general = Notice(id="general", title="Everyone", content="Hello all")
    rockbox = Notice(
        id="rockbox",
        title="Rockbox",
        content="About Rockbox",
        firmware_repo="rockbox-y1/rockbox",
        firmware_versions=("*",),
    )
    service = _service(monkeypatch, [general, rockbox])
    listed = []
    shown = []
    service.notices_ready.connect(lambda notices: listed.append([n.id for n in notices]))
    service.notice_ready.connect(lambda notice: shown.append(getattr(notice, "id", None)))

    service.set_context(model="Y1", firmware_repo="rockbox-y1/rockbox", firmware_version="v1.0.0")
    # The full list is everyone's, in feed order.
    assert listed[-1] == ["general", "rockbox"]
    # The card leads with the firmware the reader is looking at.
    assert shown[-1] == "rockbox"

    service.set_context(model="Y1", firmware_repo="y1-community/y1-stock-rom", firmware_version="v3.2.0")
    assert [n.id for n in service.contextual_notices()] == ["general"]
    assert listed[-1] == ["general", "rockbox"]


def test_version_targeted_notice_follows_the_selected_release(monkeypatch):
    notice = Notice(
        id="tagged",
        title="3.2.0",
        content="About 3.2.0",
        firmware_versions=("v3.2.0",),
    )
    service = _service(monkeypatch, [notice])
    service.set_context(model="Y1", firmware_version="v3.2.0")
    assert [n.id for n in service.contextual_notices()] == ["tagged"]
    service.set_context(model="Y1", firmware_version="v3.1.2")
    assert service.contextual_notices() == []


def test_card_rotates_one_notice_at_a_time(monkeypatch):
    first = Notice(id="first", title="A", content="One")
    second = Notice(id="second", title="B", content="Two")
    service = _service(monkeypatch, [first, second])
    QApplication.instance() or QApplication(sys.argv)
    shown = []
    service.notice_ready.connect(lambda notice: shown.append(getattr(notice, "id", None)))

    service.set_context(model="")
    assert shown[-1] == "first"
    service._rotate_notice()
    assert shown[-1] == "second"
    service._rotate_notice()
    assert shown[-1] == "first"


def test_context_change_surfaces_the_new_models_notice_first(monkeypatch):
    y1 = Notice(id="y1", title="Y1", content="Y1 only", models=("Y1",))
    y2 = Notice(id="y2", title="Y2", content="Y2 only", models=("Y2",))
    service = _service(monkeypatch, [y1, y2])
    shown = []
    service.notice_ready.connect(lambda notice: shown.append(getattr(notice, "id", None)))
    service.set_context(model="Y1")
    assert shown[-1] == "y1"
    # Selecting Y2 is the moment to bring the Y2 notice onto the card.
    service.set_context(model="Y2")
    assert shown[-1] == "y2"


def test_dismissed_banner_notice_stays_out_of_the_rotation(monkeypatch):
    first = Notice(id="first", title="A", content="One")
    second = Notice(id="second", title="B", content="Two")
    service = _service(monkeypatch, [first, second])
    service.set_context(model="")
    shown = []
    service.notice_ready.connect(lambda notice: shown.append(getattr(notice, "id", None)))
    service.dismiss_for_session(first)
    assert shown[-1] == "second"
    assert [n.id for n in service.contextual_notices()] == ["second"]
