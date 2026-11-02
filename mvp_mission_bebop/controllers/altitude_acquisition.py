"""Closed-loop, two-sided altitude acquisition with actuator lag lead.

The Stage 1 climb above the firmware plateau used to be open loop in its
dynamics: a braking-envelope feedforward fed through a jerk-limited profile,
clamped to be non-negative, with no position feedback. The planner assumed the
airframe follows the commanded profile. It does not: the Bebop's vertical axis
is a first-order lag (``tau ~ 0.35 s``) behind a 0.10-0.20 s transport delay,
observed through odometry at 5 Hz. The physical excess is therefore
``v * (tau + latency + 1 / (2 * 5 Hz))``, 9 to 13 cm at 0.25 m/s, and once the
airframe was above the target the loop had no authority to descend: flights
held 0.17-0.18 m high for the whole 15 s deadline.

Law
---
A position reference ``r`` starts at the measured altitude and advances along a
jerk-limited velocity profile whose target is the braking envelope
``braking_velocity(target - r, decel, cruise=max_velocity)``. The reference is
independent of the measurement, so the feedforward ``v_ref`` is smooth. The
command is

    vz = v_ref + position_kp * (r - z_pred),    z_pred = z + lag_sec * vz_measured

``z_pred`` is a first-order prediction of where the airframe will be once the
velocity it already has has played out through the lag: for a first-order
plant with time constant ``tau`` driven to rest, the residual travel is
``tau * v``, and the odometry and transport delay add ``latency * v``. Leading
the position error by that travel is what removes the overshoot the planner
cannot see. The output is saturated at ``+-max_velocity`` and slew-limited at
``max_accel`` per second, so the 5 Hz odometry steps reach the airframe as ramps.
There is no integral term. The output may be negative: descending needs no
supervisor window (``FailsafeSupervisor.clamp_kinematics`` passes any downward
command), only ascending does.

No abstraction of this kind exists in the Nectar SDK, whose ``BebopDrone`` only
publishes the Twist it is given.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from mvp_mission_bebop.controllers.profiling import JerkLimitedProfile, ProfileLimits, braking_velocity


@dataclass(frozen=True)
class AcquisitionLimits:
    """Kinematic and loop parameters of :class:`AltitudeAcquisitionController`.

    Parameters
    ----------
    max_velocity_mps : float
        Cruise and saturation of the vertical command, m/s.
    max_accel_mps2 : float
        Acceleration ceiling of the reference profile and slew limit of the
        output, m/s^2.
    max_jerk_mps3 : float
        Jerk ceiling of the reference profile, m/s^3.
    decel_mps2 : float
        Deceleration of the braking envelope, m/s^2.
    lag_sec : float
        Lead applied to the measured velocity, s (``tau`` plus latency). Zero
        disables the prediction.
    position_kp : float
        Position gain, 1/s.
    deadband_m : float
        Arrival band around the target, m.

    Raises
    ------
    ValueError
        If any value is non-finite, ``lag_sec`` is negative or any other value
        is not strictly positive.
    """

    max_velocity_mps: float
    max_accel_mps2: float
    max_jerk_mps3: float
    decel_mps2: float
    lag_sec: float
    position_kp: float
    deadband_m: float

    def __post_init__(self) -> None:
        for name in (
            "max_velocity_mps",
            "max_accel_mps2",
            "max_jerk_mps3",
            "decel_mps2",
            "lag_sec",
            "position_kp",
            "deadband_m",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
            if name == "lag_sec":
                if value < 0.0:
                    raise ValueError(f"lag_sec must be >= 0, got {value!r}")
            elif value <= 0.0:
                raise ValueError(f"{name} must be positive, got {value!r}")


@dataclass(frozen=True)
class AcquisitionCommand:
    """One cycle of :class:`AltitudeAcquisitionController`.

    Parameters
    ----------
    vz_mps : float
        Vertical command, m/s, positive up.
    reference_m : float
        Position reference ``r``.
    predicted_m : float
        Lag-compensated altitude ``z_pred``; NaN when the altitude was invalid.
    error_m : float
        ``target - altitude``; NaN when the altitude was invalid.
    finished : bool
        Reference arrived and the measured altitude inside the deadband.
    """

    vz_mps: float
    reference_m: float
    predicted_m: float
    error_m: float
    finished: bool


class AltitudeAcquisitionController:
    """Two-sided vertical guidance from the current altitude to a target.

    Parameters
    ----------
    limits : AcquisitionLimits
        Kinematic and loop parameters.

    Raises
    ------
    TypeError
        If ``limits`` is not an :class:`AcquisitionLimits`.
    """

    def __init__(self, limits: AcquisitionLimits) -> None:
        if not isinstance(limits, AcquisitionLimits):
            raise TypeError(f"limits must be AcquisitionLimits, got {type(limits).__name__}")
        self._limits: AcquisitionLimits = limits
        self._profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=limits.max_velocity_mps,
                max_accel=limits.max_accel_mps2,
                max_jerk=limits.max_jerk_mps3,
            )
        )
        self._target: Optional[float] = None
        self._reference: float = 0.0
        self._last_vz: float = 0.0

    @property
    def limits(self) -> AcquisitionLimits:
        """Parameters in force."""
        return self._limits

    @property
    def target_m(self) -> Optional[float]:
        """Target altitude, ``None`` before :meth:`start`."""
        return self._target

    def reset(self) -> None:
        """Forget the target, the reference and the profile state."""
        self._target = None
        self._reference = 0.0
        self._last_vz = 0.0
        self._profile.reset(0.0, 0.0)

    def start(self, altitude_m: float, target_m: float, vz_mps: float = 0.0) -> None:
        """Anchor the reference at the measured altitude.

        Parameters
        ----------
        altitude_m : float
            Measured altitude, m.
        target_m : float
            Target altitude, m.
        vz_mps : float
            Measured vertical speed, seeds the reference profile so a climb
            already under way is not braked to zero first.

        Raises
        ------
        ValueError
            If ``altitude_m`` or ``target_m`` is not finite.
        """
        if not (math.isfinite(altitude_m) and math.isfinite(target_m)):
            raise ValueError(f"altitude and target must be finite, got {altitude_m!r}, {target_m!r}")
        seed = vz_mps if math.isfinite(vz_mps) else 0.0
        if (target_m - altitude_m) * seed < 0.0:
            seed = 0.0
        self._target = float(target_m)
        self._reference = float(altitude_m)
        self._profile.reset(seed, 0.0)
        self._last_vz = self._profile.velocity

    def update(self, altitude_m: float, vz_measured_mps: float, dt: float) -> AcquisitionCommand:
        """Advance one control cycle.

        Parameters
        ----------
        altitude_m : float
            Measured altitude, m. NaN holds the vertical axis at rest.
        vz_measured_mps : float
            Measured vertical speed, m/s. NaN disables the lead this cycle.
        dt : float
            Elapsed time since the previous cycle, s.

        Returns
        -------
        AcquisitionCommand
            The command; ``vz_mps == 0.0`` and ``finished is False`` before
            :meth:`start` or with an invalid altitude or interval.
        """
        limits = self._limits
        target = self._target
        if target is None or not math.isfinite(altitude_m) or not math.isfinite(dt) or dt <= 0.0:
            self._last_vz = 0.0
            return AcquisitionCommand(0.0, self._reference, math.nan, math.nan, False)

        to_go = target - self._reference
        envelope = braking_velocity(
            abs(to_go), limits.decel_mps2, cruise_velocity=limits.max_velocity_mps
        )
        v_ref = self._profile.step(math.copysign(envelope, to_go), dt)
        advanced = self._reference + v_ref * dt
        if (target - advanced) * to_go <= 0.0:
            advanced = target
            self._profile.reset(0.0, 0.0)
            v_ref = 0.0
        self._reference = advanced

        lead = limits.lag_sec * vz_measured_mps if math.isfinite(vz_measured_mps) else 0.0
        predicted = altitude_m + lead
        raw = v_ref + limits.position_kp * (self._reference - predicted)
        raw = max(-limits.max_velocity_mps, min(limits.max_velocity_mps, raw))
        slew = limits.max_accel_mps2 * dt
        vz = max(self._last_vz - slew, min(self._last_vz + slew, raw))
        self._last_vz = vz

        error = target - altitude_m
        finished = self._reference == target and abs(error) <= limits.deadband_m
        return AcquisitionCommand(vz, self._reference, predicted, error, finished)
