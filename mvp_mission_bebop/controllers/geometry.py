"""Pinhole camera geometry for image-based guidance.

The Nectar SDK ships ``ImageCalculus.calculate_vector_from_drone_to_ground``,
and it is not usable for this stage. Two reasons, both structural:

*Its stated validity envelope excludes the manoeuvre.* The class documents
itself as valid "for small pitch and roll angles (typically <= 5 degrees)"
(``nectar/vision/utils/image_calculus.py:170``). The approach this module serves
sweeps the camera from -18 degrees to -80 degrees; the whole point of the stage
is operating far outside that envelope.

*It linearizes the pixel-to-bearing conversion.* It divides a pixel offset by a
constant ``pixels_per_degree`` (``:183-184``) rather than applying the tangent
relation. That approximation is good near the optical axis and degrades toward
the frame edges -- and the error it makes is largest at the large depression
angles that dominate the nadir approach, which is exactly where the estimate has
to be trusted.

It also folds in drone body pitch and roll, which this mission does not observe:
the Bebop publishes no attitude beyond the odometry quaternion, and its gimbal
is independently stabilized, so the camera's depression is the gimbal command
rather than a sum of airframe and gimbal angles.

What follows is the exact projection instead. Note that the along-track result
reduces to ``altitude / tan(tilt + bearing)`` -- the same shape as the
approximation -- but that identity falls out of the tangent addition formula and
holds exactly, with no small-angle assumption.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Optional, Tuple

#: A ray within this angle of horizontal never meets the ground at a usable
#: range; the projection is reported as unavailable instead of returning a
#: distance that tends to infinity.
MIN_DEPRESSION_DEG: Final[float] = 3.0


@dataclass(frozen=True)
class CameraIntrinsics:
    """Field-of-view description of a rectilinear camera.

    Expressed as field of view rather than a focal-length matrix because that is
    what is actually known about the Bebop's front camera; the two are
    interchangeable through ``f = (w / 2) / tan(fov_h / 2)``.
    """

    width: int
    height: int
    horizontal_fov_deg: float
    vertical_fov_deg: float

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"frame dimensions must be positive, got {self.width}x{self.height}")
        if not 0.0 < self.horizontal_fov_deg < 180.0:
            raise ValueError(f"horizontal_fov_deg out of range: {self.horizontal_fov_deg!r}")
        if not 0.0 < self.vertical_fov_deg < 180.0:
            raise ValueError(f"vertical_fov_deg out of range: {self.vertical_fov_deg!r}")

    @property
    def center(self) -> Tuple[float, float]:
        """Principal point, assumed to be the frame centre."""
        return self.width / 2.0, self.height / 2.0

    def bearing(self, target_px: Tuple[float, float]) -> Tuple[float, float]:
        """Angular offset of a pixel from the optical axis.

        Returns
        -------
        Tuple[float, float]
            ``(theta_x, theta_y)`` in radians. ``theta_x`` is positive to the
            right of the axis, ``theta_y`` positive below it. Both follow the
            exact pinhole relation ``tan(theta) = (offset / half_extent) *
            tan(fov / 2)``, not a linear pixels-per-degree scaling.
        """
        center_x, center_y = self.center
        half_h = math.tan(math.radians(self.horizontal_fov_deg) / 2.0)
        half_v = math.tan(math.radians(self.vertical_fov_deg) / 2.0)

        theta_x = math.atan((target_px[0] - center_x) / center_x * half_h)
        theta_y = math.atan((target_px[1] - center_y) / center_y * half_v)
        return theta_x, theta_y


@dataclass(frozen=True)
class GroundProjection:
    """Where a pixel ray meets flat ground, in the drone's body frame (FLU)."""

    #: Along-track distance, positive ahead of the nose.
    forward_m: float
    #: Cross-track distance, positive to the left.
    lateral_m: float
    #: Angle of the ray below horizontal, in degrees.
    depression_deg: float
    #: Straight-line distance from the camera to the ground point.
    slant_range_m: float

    @property
    def ground_range_m(self) -> float:
        """Horizontal distance to the ground point."""
        return math.hypot(self.forward_m, self.lateral_m)


def project_to_ground(
    intrinsics: CameraIntrinsics,
    target_px: Tuple[float, float],
    altitude_m: float,
    tilt_deg: float,
) -> Optional[GroundProjection]:
    """Intersect the ray through ``target_px`` with the ground plane.

    Parameters
    ----------
    intrinsics : CameraIntrinsics
        Camera field of view and frame size.
    target_px : Tuple[float, float]
        Target centre in pixels.
    altitude_m : float
        Height of the camera above the ground plane, in metres.
    tilt_deg : float
        Gimbal pitch, negative downward, matching the SDK's
        ``camera_control`` convention where -80 is near nadir.

    Returns
    -------
    Optional[GroundProjection]
        The intersection, or ``None`` when the geometry is unusable: a
        non-positive altitude, or a ray too close to horizontal for the range
        to be meaningful.
    """
    if altitude_m <= 0.0:
        return None

    theta_x, theta_y = intrinsics.bearing(target_px)

    # Depression of the optical axis, then of the ray itself. The identity
    # forward = h / tan(axis + theta_y) is exact: it follows from the tangent
    # addition formula applied to the rotated ray, not from linearization.
    axis_depression = math.radians(-tilt_deg)
    depression = axis_depression + theta_y

    if depression <= math.radians(MIN_DEPRESSION_DEG):
        return None
    if depression >= math.pi / 2.0:
        # Past vertical: the camera is looking behind the drone.
        depression = math.pi / 2.0

    tan_depression = math.tan(depression)
    forward = altitude_m / tan_depression if tan_depression > 0.0 else 0.0

    sin_depression = math.sin(depression)
    lateral = -altitude_m * math.tan(theta_x) * math.cos(theta_y) / sin_depression
    slant = altitude_m / sin_depression

    return GroundProjection(
        forward_m=forward,
        lateral_m=lateral,
        depression_deg=math.degrees(depression),
        slant_range_m=slant,
    )


def tilt_for_depression(depression_deg: float) -> float:
    """Gimbal command that places the optical axis at a given depression."""
    return -depression_deg


def depression_for_range(altitude_m: float, forward_m: float) -> float:
    """Depression angle whose ray lands ``forward_m`` ahead, in degrees."""
    if altitude_m <= 0.0:
        return 90.0
    if forward_m <= 0.0:
        return 90.0
    return math.degrees(math.atan2(altitude_m, forward_m))
