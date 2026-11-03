"""Stage 5 with the 0.40 m and 0.45 m pad markers flown in the field.

The return was tuned on a 0.20 m tag. A larger tag is detected from higher and
estimated more precisely, but its quiet zone leaves the frame higher too: these
tests pin the framing floor, the descent heights derived from it and the
frame-edge guard of the -80 deg centring to the tag size.
"""

from __future__ import annotations

import math
from typing import Optional

import pytest

from mvp_mission_bebop.controllers.precision_descent import (
    COMMIT_LOSS_BAND_M,
    DescentPhase,
    PrecisionDescentController,
)
from mvp_mission_bebop.controllers.rtl_guidance import (
    ADVANCED_RETURN_TILT_DEG,
    FRAME_BOTTOM_TAN,
    QUIET_ZONE_HALF_FRACTION,
    ArucoCenteringController,
    MarkerObservation,
    marker_framing_floor_m,
    visibility_standoff_m,
)
from mvp_mission_bebop.parameters import FlightKinematicsConfig, ReturnToLaunchConfig

DT = 1.0 / 15.0
SIMPLE_TILT = -80.0
FIELD_TAGS = (0.40, 0.45)


def config(tag: float) -> ReturnToLaunchConfig:
    cfg = ReturnToLaunchConfig()
    cfg.tag_size = tag
    return cfg


def centring(cfg: ReturnToLaunchConfig, *, advanced: bool = False) -> ArucoCenteringController:
    law = ArucoCenteringController(
        cfg,
        FlightKinematicsConfig(),
        simple=True,
        tilt_deg=ADVANCED_RETURN_TILT_DEG if advanced else None,
        standoff=advanced,
    )
    law.reset()
    return law


def sighting(ex: float, ey: float, h: float, tilt_deg: float = SIMPLE_TILT) -> MarkerObservation:
    """Camera-frame sighting of a marker at body error ``(ex, ey)``, lens height ``h``."""
    t = math.radians(abs(tilt_deg))
    z = math.cos(t) * ex + math.sin(t) * h
    y = -math.sin(t) * ex + math.cos(t) * h
    return MarkerObservation(marker_id=8, x_cam_m=-ey, y_cam_m=y, z_cam_m=z)


def fits_somewhere(tag: float, tilt_deg: float, h: float) -> bool:
    """Brute-force placement search: some lead keeps the quiet zone in the band."""
    phi = math.degrees(math.atan(FRAME_BOTTOM_TAN)) - 2.0
    low = math.radians(abs(tilt_deg) + phi)
    high = math.radians(abs(tilt_deg) - phi)
    q = QUIET_ZONE_HALF_FRACTION * tag
    for i in range(-4000, 4001):
        s = i * 0.0005
        if h / math.tan(low) <= s - q and s + q <= h / math.tan(high):
            return True
    return False


# ════════════════════════════════════════════════════════════ framing floor


@pytest.mark.parametrize(
    ("tag", "tilt", "expected"),
    [
        (0.20, -80.0, 0.303),
        (0.40, -80.0, 0.607),
        (0.45, -80.0, 0.682),
        (0.20, -70.0, 0.271),
        (0.40, -70.0, 0.542),
        (0.45, -70.0, 0.610),
    ],
)
def test_framing_floor_of_the_field_tags(tag: float, tilt: float, expected: float) -> None:
    assert marker_framing_floor_m(tag, tilt) == pytest.approx(expected, abs=0.002)


@pytest.mark.parametrize("tag", (0.20,) + FIELD_TAGS)
@pytest.mark.parametrize("tilt", (-70.0, -80.0))
def test_framing_floor_is_the_lowest_height_any_placement_frames(tag: float, tilt: float) -> None:
    floor = marker_framing_floor_m(tag, tilt)
    assert fits_somewhere(tag, tilt, floor * 1.01)
    assert not fits_somewhere(tag, tilt, floor * 0.99)


def test_framing_floor_scales_linearly_with_the_tag() -> None:
    assert marker_framing_floor_m(0.40, -80.0) == pytest.approx(2.0 * marker_framing_floor_m(0.20, -80.0))


@pytest.mark.parametrize(
    ("tag", "tilt"),
    [(0.0, -80.0), (-0.4, -80.0), (math.nan, -80.0), (0.4, math.inf), (True, -80.0), (0.4, -10.0)],
)
def test_framing_floor_refuses_meaningless_inputs(tag: float, tilt: float) -> None:
    with pytest.raises(ValueError):
        marker_framing_floor_m(tag, tilt)


# ════════════════════════════════════════════════════════════ tracked descent


def test_the_020_tag_keeps_the_configured_descent_heights() -> None:
    cfg = config(0.20)
    descent = PrecisionDescentController(cfg, centring(cfg))
    assert descent.commit_height_m == pytest.approx(cfg.commit_height_m)
    assert descent.lost_commit_height_m == pytest.approx(cfg.descent_lost_commit_height_m)


@pytest.mark.parametrize("tag", FIELD_TAGS)
@pytest.mark.parametrize("advanced", (False, True))
def test_field_tags_raise_the_commit_to_the_framing_floor(tag: float, advanced: bool) -> None:
    cfg = config(tag)
    law = centring(cfg, advanced=advanced)
    descent = PrecisionDescentController(cfg, law)
    floor = marker_framing_floor_m(tag, law.tilt_deg)
    assert descent.framing_floor_m == pytest.approx(floor)
    assert descent.commit_height_m == pytest.approx(max(cfg.commit_height_m, floor))
    assert descent.commit_height_m > cfg.commit_height_m
    assert descent.lost_commit_height_m == pytest.approx(
        max(cfg.descent_lost_commit_height_m, floor + COMMIT_LOSS_BAND_M)
    )


@pytest.mark.parametrize("tag", FIELD_TAGS)
def test_field_tag_commits_with_the_marker_in_view(tag: float) -> None:
    cfg = config(tag)
    descent = PrecisionDescentController(cfg, centring(cfg))
    floor = marker_framing_floor_m(tag, SIMPLE_TILT)
    h = 1.2
    committed_at: Optional[float] = None
    for _ in range(3000):
        command = descent.update(sighting(0.0, 0.0, h), None, DT, tilt_deg=SIMPLE_TILT)
        assert command.vz_mps <= 0.0
        if command.commit:
            committed_at = h
            assert command.note.startswith("commit at")
            break
        h += command.vz_mps * DT
    assert committed_at is not None
    assert floor - 0.02 <= committed_at <= floor + 1e-9


@pytest.mark.parametrize("tag", FIELD_TAGS)
def test_field_tag_lost_near_the_floor_commits_on_the_first_missed_frame(tag: float) -> None:
    cfg = config(tag)
    descent = PrecisionDescentController(cfg, centring(cfg))
    h = descent.lost_commit_height_m - 0.02
    for _ in range(cfg.centering_settle_cycles + 1):
        command = descent.update(sighting(0.0, 0.0, h), None, DT, tilt_deg=SIMPLE_TILT)
    assert not command.commit
    command = descent.update(None, None, DT, tilt_deg=SIMPLE_TILT)
    assert command.commit and command.phase is DescentPhase.COMMIT


# ═══════════════════════════════════════════════ -80 deg frame-edge guard


@pytest.mark.parametrize("tag", FIELD_TAGS)
def test_centred_field_tag_is_not_backed_off_the_pad(tag: float) -> None:
    """Marker centred in the frame, airframe short of it: the law closes in.

    At 0.8 m the quiet zone of a 0.45 m tag has 0.08 m of slack to the frame
    bottom when centred. The absolute 0.10 m back-off threshold read that as a
    marker leaving the frame and reversed the airframe away from the pad.
    """
    cfg = config(tag)
    law = centring(cfg)
    h = 0.8
    ex = h / math.tan(math.radians(abs(SIMPLE_TILT)))
    commands = [law.update(sighting(ex, 0.0, h), DT, tilt_deg=SIMPLE_TILT) for _ in range(60)]
    assert all(c.vx >= 0.0 for c in commands)
    assert sum(c.vx for c in commands) / len(commands) > 0.0


@pytest.mark.parametrize("tag", (0.20,) + FIELD_TAGS)
def test_marker_low_in_the_frame_still_backs_the_airframe_off(tag: float) -> None:
    """Marker ahead of the CoG yet low in the frame: the guard still reverses."""
    cfg = config(tag)
    law = centring(cfg)
    observation = sighting(0.08, 0.0, 1.2)
    assert observation.y_cam_m > 0.10
    commands = [law.update(observation, DT, tilt_deg=SIMPLE_TILT) for _ in range(60)]
    assert all(c.vx <= 0.0 for c in commands)
    assert sum(c.vx for c in commands) / len(commands) < 0.0
