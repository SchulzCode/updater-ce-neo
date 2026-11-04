"""Tracked-player labels name a hardware revision only when it differs.

Updater CE tracks one install per model and per model+revision, so an owner of
two different Y1 revisions can follow both. The revision is only worth naming
when it is the one that makes those rows different: Type A is the plain model
name, and Y2 has a single revision, so neither reads as a variant.
"""

from src.i18n import tr
from src.player_update_check import tracked_model_label, tracked_permutation_label


def _record(model, hw="", software="Reborn (Y2Linux)"):
    return {"model": model, "hardware_type": hw, "software_name": software}


def test_the_only_y2_revision_is_not_named():
    assert tracked_model_label(_record("Y2", "A")) == "Y2"
    assert tracked_model_label(_record("Y2", "Type A")) == "Y2"
    assert tracked_model_label(_record("Y2", "")) == "Y2"


def test_a_second_y2_revision_is_named():
    # Nothing ships one, but a hand-added player can still say which unit it is.
    assert tracked_model_label(_record("Y2", "B")) == "Y2 (Type B)"


def test_y1_type_a_is_the_plain_model():
    assert tracked_model_label(_record("Y1", "A")) == "Y1"


def test_y1_type_b_is_named():
    assert tracked_model_label(_record("Y1", "B")) == "Y1 (Type B)"


def test_the_reminder_row_drops_the_a_variant_but_keeps_another():
    row = tracked_permutation_label(_record("Y2", "A"))
    assert row == tr("settings_reminders_model_fmt").format(
        software="Reborn (Y2Linux)", model="Y2"
    )
    other = tracked_permutation_label(_record("Y1", "B"))
    assert other == tr("settings_reminders_model_fmt").format(
        software="Reborn (Y2Linux)", model="Y1 (Type B)"
    )
