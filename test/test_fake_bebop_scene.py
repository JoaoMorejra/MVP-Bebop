"""Camera scene v1 of the driver emulator: a frame chosen by the phase of the pose (4.1.3).

v1 covers flow, timing and abort; it does not close the visual loop (that is
v2). The frame follows the flight: empty floor on the ground and before the
target, the bench target once the aircraft has flown past ``reveal_x_m``, and
the landing marker (AprilTag 36h11, the mission's ``rtl.target_aruco_id``) when
it is back over the pad looking down after the target phase.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from support.fake_bebop.scene import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    CameraView,
    PhaseScene,
    build_scene,
    floor_texture,
)


def view(x=0.0, y=0.0, z=1.0, tilt=-20.0, state=2):
    return CameraView(x=x, y=y, z=z, tilt_deg=tilt, pan_deg=0.0, flying_state=state)


def marker_ids(frame):
    from nectar.vision.algorithms.markers.aruco import resolve_aruco_dict

    dictionary = cv2.aruco.getPredefinedDictionary(resolve_aruco_dict("DICT_APRILTAG_36h11"))
    detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
    _corners, ids, _ = detector.detectMarkers(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    return [] if ids is None else sorted(int(i) for i in ids.flatten())


@pytest.fixture
def scene():
    return PhaseScene()


def test_every_frame_has_the_stream_geometry(scene):
    for v in (view(state=0, z=0.0), view(x=2.0), view(x=0.0, tilt=-80.0)):
        frame = scene.render(v)
        assert frame.shape == (FRAME_HEIGHT, FRAME_WIDTH, 3)
        assert frame.dtype == np.uint8


def test_on_the_ground_the_camera_sees_the_empty_floor(scene):
    assert np.array_equal(scene.render(view(state=0, z=0.0, x=3.0)), floor_texture())


def test_the_target_appears_only_past_the_reveal_distance(scene):
    assert np.array_equal(scene.render(view(x=scene.reveal_x_m - 0.05)), floor_texture())
    assert not np.array_equal(scene.render(view(x=scene.reveal_x_m + 0.05)), floor_texture())


def test_back_over_the_pad_looking_down_after_the_target_the_marker_is_seen(scene):
    scene.render(view(x=scene.reveal_x_m + 0.5))
    assert marker_ids(scene.render(view(x=0.1, y=-0.1, tilt=-80.0))) == [8]


def test_no_marker_before_the_target_phase_or_while_looking_ahead(scene):
    assert marker_ids(scene.render(view(x=0.1, tilt=-80.0))) == []
    scene.render(view(x=scene.reveal_x_m + 0.5))
    assert marker_ids(scene.render(view(x=0.1, tilt=-20.0))) == []
    assert marker_ids(scene.render(view(x=scene.marker_radius_m + 0.3, tilt=-80.0))) == []


def test_the_marker_id_and_dictionary_follow_the_configuration():
    scene = PhaseScene(marker_id=3)
    scene.render(view(x=scene.reveal_x_m + 0.5))
    assert marker_ids(scene.render(view(x=0.0, tilt=-80.0))) == [3]


def test_the_scenarios_are_built_by_name():
    assert isinstance(build_scene("accident"), PhaseScene)
    with pytest.raises(ValueError):
        build_scene("moon")


@pytest.mark.parametrize("kwargs", [{"reveal_x_m": -1.0}, {"marker_radius_m": 0.0}, {"marker_id": -1}])
def test_an_invalid_scene_is_refused(kwargs):
    with pytest.raises(ValueError):
        PhaseScene(**kwargs)
