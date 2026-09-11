"""Unit tests for the pinhole ground projection."""

import math

import pytest

from mvp_mission_bebop.controllers.geometry import (
    CameraIntrinsics,
    depression_for_range,
    project_to_ground,
    tilt_for_depression,
)

CAMERA = CameraIntrinsics(width=856, height=480, horizontal_fov_deg=80.0, vertical_fov_deg=50.0)
CENTER = (428.0, 240.0)


def test_rejects_invalid_intrinsics():
    with pytest.raises(ValueError):
        CameraIntrinsics(width=0, height=480, horizontal_fov_deg=80.0, vertical_fov_deg=50.0)
    with pytest.raises(ValueError):
        CameraIntrinsics(width=856, height=480, horizontal_fov_deg=0.0, vertical_fov_deg=50.0)
    with pytest.raises(ValueError):
        CameraIntrinsics(width=856, height=480, horizontal_fov_deg=80.0, vertical_fov_deg=200.0)


def test_optical_axis_has_zero_bearing():
    theta_x, theta_y = CAMERA.bearing(CENTER)
    assert theta_x == pytest.approx(0.0)
    assert theta_y == pytest.approx(0.0)


def test_bearing_at_the_frame_edge_equals_half_the_field_of_view():
    theta_x, _ = CAMERA.bearing((856.0, 240.0))
    assert math.degrees(theta_x) == pytest.approx(CAMERA.horizontal_fov_deg / 2.0)
    _, theta_y = CAMERA.bearing((428.0, 480.0))
    assert math.degrees(theta_y) == pytest.approx(CAMERA.vertical_fov_deg / 2.0)


def test_bearing_is_not_linear_in_pixel_offset():
    """The relation is a tangent, not a constant pixels-per-degree scaling.

    The SDK helper this module replaces divides by a fixed ``pixels_per_degree``,
    which is the approximation that degrades toward the frame edges -- exactly
    where the nadir approach operates.
    """
    quarter, _ = CAMERA.bearing((428.0 + 214.0, 240.0))
    half, _ = CAMERA.bearing((856.0, 240.0))
    assert half < 2.0 * quarter, "a linear model would make these exactly proportional"


@pytest.mark.parametrize(
    "tilt_deg,expected_ratio",
    [(-45.0, 1.0), (-30.0, math.sqrt(3.0)), (-60.0, 1.0 / math.sqrt(3.0))],
)
def test_centred_target_range_matches_the_tangent_relation(tilt_deg, expected_ratio):
    projection = project_to_ground(CAMERA, CENTER, 2.0, tilt_deg)
    assert projection.forward_m == pytest.approx(2.0 * expected_ratio)
    assert projection.depression_deg == pytest.approx(-tilt_deg)


def test_range_scales_linearly_with_altitude():
    low = project_to_ground(CAMERA, CENTER, 1.0, -40.0)
    high = project_to_ground(CAMERA, CENTER, 3.0, -40.0)
    assert high.forward_m == pytest.approx(3.0 * low.forward_m)


def test_target_lower_in_frame_is_closer():
    ranges = [
        project_to_ground(CAMERA, (428.0, row), 1.5, -40.0).forward_m
        for row in (120, 240, 360, 460)
    ]
    for nearer, further in zip(ranges, ranges[1:]):
        assert further < nearer


def test_lateral_sign_follows_the_body_frame():
    """Body frame is FLU, so a target right of centre is at negative y."""
    right = project_to_ground(CAMERA, (700.0, 240.0), 1.5, -60.0)
    left = project_to_ground(CAMERA, (200.0, 240.0), 1.5, -60.0)
    assert right.lateral_m < 0.0
    assert left.lateral_m > 0.0
    assert project_to_ground(CAMERA, CENTER, 1.5, -60.0).lateral_m == pytest.approx(0.0)


def test_ground_range_combines_both_axes():
    projection = project_to_ground(CAMERA, (700.0, 300.0), 1.5, -50.0)
    assert projection.ground_range_m == pytest.approx(
        math.hypot(projection.forward_m, projection.lateral_m)
    )


def test_slant_range_exceeds_ground_range():
    projection = project_to_ground(CAMERA, CENTER, 2.0, -40.0)
    assert projection.slant_range_m > projection.ground_range_m


def test_unusable_geometry_is_reported_rather_than_guessed():
    assert project_to_ground(CAMERA, CENTER, 0.0, -45.0) is None
    assert project_to_ground(CAMERA, CENTER, -1.0, -45.0) is None
    # Near the horizon the range diverges; returning a number would be a lie.
    assert project_to_ground(CAMERA, CENTER, 1.5, -1.0) is None


def test_nadir_tilt_places_the_target_beneath_the_drone():
    projection = project_to_ground(CAMERA, CENTER, 1.5, -90.0)
    assert projection.forward_m == pytest.approx(0.0, abs=1e-9)
    assert projection.depression_deg == pytest.approx(90.0)


def test_tilt_and_depression_are_inverses():
    assert tilt_for_depression(80.0) == -80.0
    assert tilt_for_depression(0.0) == 0.0


def test_depression_for_range_round_trips():
    for forward in (0.2, 1.0, 3.0):
        depression = depression_for_range(1.5, forward)
        assert 1.5 / math.tan(math.radians(depression)) == pytest.approx(forward)


def test_depression_for_degenerate_range_is_nadir():
    assert depression_for_range(1.5, 0.0) == 90.0
    assert depression_for_range(0.0, 1.0) == 90.0
