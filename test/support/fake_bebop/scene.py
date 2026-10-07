"""Camera frames of the emulated Bebop 2, rendered from the plant's pose. Pure numpy and OpenCV.

The driver publishes ``bgr8`` frames of the stream it configured,
``REC1080_STREAM480`` (856x480), and a ``camera_info`` from
``config/bebop2_camera_calib.yaml``. A scene only decides what is in the frame.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Callable, Dict, Final, Optional, Protocol, Union

import numpy as np

#: Stream geometry of ``REC1080_STREAM480``.
FRAME_WIDTH: Final[int] = 856
FRAME_HEIGHT: Final[int] = 480


@dataclass(frozen=True)
class CameraView:
    """What the camera sees from: true pose, gimbal and flying state.

    Attributes
    ----------
    x, y, z : float
        True position, metres (``x`` forward, ``y`` left, ``z`` up).
    tilt_deg, pan_deg : float
        Last ``move_camera`` command, degrees (negative tilt looks down).
    flying_state : int
        ARSDK flying state.
    """

    x: float
    y: float
    z: float
    tilt_deg: float
    pan_deg: float
    flying_state: int


class Scene(Protocol):
    """Renders one frame for a view."""

    def render(self, view: CameraView) -> np.ndarray: ...


def floor_texture(seed: int = 7, width: int = FRAME_WIDTH, height: int = FRAME_HEIGHT) -> np.ndarray:
    """A deterministic grey concrete-like texture: the frame with nothing in it."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(90, 140, size=(height // 8 + 1, width // 8 + 1), dtype=np.uint8)
    import cv2

    base = cv2.resize(coarse, (width, height), interpolation=cv2.INTER_LINEAR)
    grain = rng.integers(-6, 7, size=(height, width), dtype=np.int16)
    gray = np.clip(base.astype(np.int16) + grain, 0, 255).astype(np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


class UniformScene:
    """The same empty floor whatever the pose; enough for contract and interlock tests."""

    def __init__(self, frame: Optional[np.ndarray] = None) -> None:
        self._frame = floor_texture() if frame is None else frame

    def render(self, view: CameraView) -> np.ndarray:
        return self._frame


#: Bench frame with the target (a bicycle) the mission's YOLO confirms.
DEFAULT_TARGET_FRAME: Final[str] = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "bench_target.jpg")
)
#: Flying states with the rotors turning above the ground.
_AIRBORNE: Final[frozenset] = frozenset({1, 2, 3, 4, 8})
#: Lowest altitude at which the camera is taken to see the scene from the air, metres.
_AIRBORNE_MIN_Z_M: Final[float] = 0.3
#: Tilt at or below which the camera looks at the pad, degrees.
_LOOKING_DOWN_DEG: Final[float] = -45.0
#: Side of the rendered marker, pixels, and its white quiet zone.
_MARKER_PX: Final[int] = 140
_QUIET_PX: Final[int] = 24


class PhaseScene:
    """Scene v1: the frame is chosen by the phase of the flight.

    Parameters
    ----------
    target_frame : str or ndarray, optional
        Image with the target, resized to the stream geometry. The bench frame
        by default.
    reveal_x_m : float
        Forward distance past which the target is in view, metres (>= 0).
    marker_radius_m : float
        Horizontal distance from the pad within which the marker is in view
        when looking down, metres (> 0).
    marker_id : int
        Marker identifier (``rtl.target_aruco_id``, default 8).
    marker_dict : int or str
        Marker dictionary, resolved by the SDK's ``resolve_aruco_dict``
        (``rtl.marker_dict``, default ``DICT_APRILTAG_36h11``).

    Notes
    -----
    The marker only appears after the target phase, so a flight that never
    reached the target never finds the pad either -- the return then ends in
    the mission's in-place landing, as it would over an unmarked floor.
    """

    def __init__(
        self,
        target_frame: Union[str, np.ndarray, None] = None,
        reveal_x_m: float = 0.5,
        marker_radius_m: float = 0.8,
        marker_id: int = 8,
        marker_dict: Union[int, str] = "DICT_APRILTAG_36h11",
    ) -> None:
        import cv2

        for name, value in (("reveal_x_m", reveal_x_m), ("marker_radius_m", marker_radius_m)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise TypeError(f"{name} must be a finite number, got {value!r}")
        if reveal_x_m < 0.0:
            raise ValueError(f"reveal_x_m must be non-negative, got {reveal_x_m}")
        if marker_radius_m <= 0.0:
            raise ValueError(f"marker_radius_m must be positive, got {marker_radius_m}")
        if isinstance(marker_id, bool) or not isinstance(marker_id, int) or marker_id < 0:
            raise ValueError(f"marker_id must be a non-negative int, got {marker_id!r}")
        self.reveal_x_m = float(reveal_x_m)
        self.marker_radius_m = float(marker_radius_m)
        self._floor = floor_texture()
        self._target = self._load_target(DEFAULT_TARGET_FRAME if target_frame is None else target_frame)
        self._marker = self._render_marker(marker_id, marker_dict)
        self._target_seen = False

    @staticmethod
    def _load_target(source: Union[str, np.ndarray]) -> np.ndarray:
        import cv2

        image = cv2.imread(source) if isinstance(source, str) else source
        if image is None or image.ndim != 3:
            raise ValueError(f"target frame {source!r} could not be read as a colour image")
        if image.shape[:2] != (FRAME_HEIGHT, FRAME_WIDTH):
            image = cv2.resize(image, (FRAME_WIDTH, FRAME_HEIGHT), interpolation=cv2.INTER_AREA)
        return image

    def _render_marker(self, marker_id: int, marker_dict: Union[int, str]) -> np.ndarray:
        import cv2
        from nectar.vision.algorithms.markers.aruco import resolve_aruco_dict

        dictionary = cv2.aruco.getPredefinedDictionary(resolve_aruco_dict(marker_dict))
        tag = cv2.aruco.generateImageMarker(dictionary, marker_id, _MARKER_PX)
        side = _MARKER_PX + 2 * _QUIET_PX
        patch = np.full((side, side), 255, dtype=np.uint8)
        patch[_QUIET_PX:_QUIET_PX + _MARKER_PX, _QUIET_PX:_QUIET_PX + _MARKER_PX] = tag
        frame = self._floor.copy()
        top = (FRAME_HEIGHT - side) // 2
        left = (FRAME_WIDTH - side) // 2
        frame[top:top + side, left:left + side] = cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR)
        return frame

    def render(self, view: CameraView) -> np.ndarray:
        if view.flying_state not in _AIRBORNE or view.z <= _AIRBORNE_MIN_Z_M:
            return self._floor
        if view.x >= self.reveal_x_m:
            self._target_seen = True
            return self._target
        near_pad = math.hypot(view.x, view.y) <= self.marker_radius_m
        if self._target_seen and near_pad and view.tilt_deg <= _LOOKING_DOWN_DEG:
            return self._marker
        return self._floor


#: Scene factories by ``--scenario`` name.
SCENARIOS: Dict[str, Callable[[], Scene]] = {"empty": UniformScene, "accident": PhaseScene}


def build_scene(name: str) -> Scene:
    """The scene registered as ``name``.

    Raises
    ------
    ValueError
        If no scene has that name.
    """
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; known: {', '.join(sorted(SCENARIOS))}")
    return SCENARIOS[name]()
