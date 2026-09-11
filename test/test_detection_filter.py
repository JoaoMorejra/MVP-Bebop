"""Unit tests for temporal persistence filtering of detections."""

import pytest

from mvp_mission_bebop.estimation.detection_filter import (
    ConfirmationState,
    HysteresisConfirmer,
)


def test_rejects_invalid_thresholds():
    with pytest.raises(ValueError):
        HysteresisConfirmer(0, 2)
    with pytest.raises(ValueError):
        HysteresisConfirmer(3, 0)


def test_requires_consecutive_detections_to_confirm():
    confirmer = HysteresisConfirmer(confirm_frames=3, release_frames=2)
    assert not confirmer.update(True).confirmed
    assert not confirmer.update(True).confirmed
    report = confirmer.update(True)
    assert report.confirmed
    assert report.just_confirmed


def test_a_single_miss_resets_acquisition():
    confirmer = HysteresisConfirmer(confirm_frames=3, release_frames=2)
    confirmer.update(True)
    confirmer.update(True)
    confirmer.update(False)
    assert not confirmer.update(True).confirmed


def test_a_confirmed_target_survives_a_dropped_frame():
    """The defect the single counter could not express.

    One counter means a confirmed target is discarded by the first missed
    frame. With a detector on a vibrating airframe, missed frames are routine.
    """
    confirmer = HysteresisConfirmer(confirm_frames=3, release_frames=2)
    for _ in range(3):
        confirmer.update(True)
    assert confirmer.confirmed

    assert confirmer.update(False).confirmed, "one dropped frame must not release"
    assert confirmer.update(True).confirmed


def test_sustained_loss_releases_the_target():
    confirmer = HysteresisConfirmer(confirm_frames=3, release_frames=2)
    for _ in range(3):
        confirmer.update(True)

    confirmer.update(False)
    report = confirmer.update(False)
    assert not report.confirmed
    assert report.just_released
    assert confirmer.state is ConfirmationState.SEARCHING


def test_transition_flags_fire_only_on_the_edge():
    confirmer = HysteresisConfirmer(confirm_frames=2, release_frames=2)
    confirmer.update(True)
    assert confirmer.update(True).just_confirmed
    assert not confirmer.update(True).just_confirmed


def test_reset_returns_to_searching():
    confirmer = HysteresisConfirmer(confirm_frames=2, release_frames=2)
    confirmer.update(True)
    confirmer.update(True)
    assert confirmer.confirmed
    confirmer.reset()
    assert not confirmer.confirmed
    assert confirmer.hits == 0
