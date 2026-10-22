"""Altitude from gated vertical speed, with an optional anchor to the firmware altitude.

``/bebop/odom.pose.position.z`` is not a measurement. The driver integrates the
ARSDK vertical speed into it (``telemetry_state.cpp``, ``current.z += vz * dt``,
``dt`` capped at 0.5 s), so a single spurious ``vz`` sample leaves a permanent
step in ``z``. The legacy gate (:mod:`.altitude_plausibility`) bounds the
*position*: it holds two samples across the step and adopts it on the third,
and from then on the whole mission flies against an offset reference.

This estimator gates the *speed* before integrating it. A rejected sample is
replaced by the last trusted speed, not by zero, and a run of
``vz_reject_streak`` rejections is accepted as a new regime -- which is safe
here, because the integrated state never carried the glitch.

The optional anchor pulls the estimate toward an external altitude (the
firmware's ``states/altitude``) with a first-order time constant, and only for
innovations inside a gate: a disagreement larger than the gate is not
corrected, and is flagged once it has persisted for
:data:`DISAGREEMENT_PERSIST_SEC`. Both quantities must be expressed in the same
frame; the caller converts.

Pure: no ROS, no clock. The caller supplies the stamps.
"""

from __future__ import annotations

import math
from typing import Final, Optional, Tuple

from mvp_mission_bebop.parameters import CalibrationConfig

#: Values ``calibration.altitude_estimator`` accepts.
ALTITUDE_ESTIMATORS: Final[Tuple[str, ...]] = ("legacy", "vz_gated", "terrain_kf")

#: Longest integration step, in seconds; the same cap as the driver's
#: ``OdometryIntegrator(0.5)``.
MAX_STEP_SEC: Final[float] = 0.5

#: Persistence of an out-of-gate anchor innovation before it is flagged, seconds.
DISAGREEMENT_PERSIST_SEC: Final[float] = 3.0


def normalize_altitude_estimator(value: object) -> str:
    """Validate ``calibration.altitude_estimator``.

    Parameters
    ----------
    value : object
        The configured value.

    Returns
    -------
    str
        One of :data:`ALTITUDE_ESTIMATORS`.

    Raises
    ------
    ValueError
        For anything else, including a non-string.
    """
    if not isinstance(value, str) or value not in ALTITUDE_ESTIMATORS:
        raise ValueError(
            f"calibration.altitude_estimator must be one of {ALTITUDE_ESTIMATORS}, got {value!r}"
        )
    return value


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class VerticalStateEstimator:
    """Integrates gated vertical speed into altitude.

    Parameters
    ----------
    cfg : CalibrationConfig
        ``vz_max_speed_mps``, ``vz_max_accel_mps2``, ``vz_reject_streak``,
        ``altitude_anchor_tau_sec`` and ``altitude_anchor_gate_m``, read on
        every update.
    """

    __slots__ = (
        "_cfg",
        "_z",
        "_vz_trusted",
        "_stamp",
        "_reject_run",
        "_anchor_stamp",
        "_disagree_since",
        "_disagreement",
        "_seeded",
    )

    def __init__(self, cfg: CalibrationConfig) -> None:
        if not isinstance(cfg, CalibrationConfig):
            raise TypeError(f"cfg must be a CalibrationConfig, got {type(cfg).__name__}")
        self._cfg = cfg
        self._z = 0.0
        self._vz_trusted = 0.0
        self._stamp: Optional[float] = None
        self._reject_run = 0
        self._anchor_stamp: Optional[float] = None
        self._disagree_since: Optional[float] = None
        self._disagreement = False
        self._seeded = False

    @property
    def z_est(self) -> float:
        """Current altitude estimate, metres."""
        return self._z

    @property
    def seeded(self) -> bool:
        """True once :meth:`reset` has given the integration a starting altitude."""
        return self._seeded

    @property
    def datum_disagreement(self) -> bool:
        """True while the anchor has stayed outside its gate for 3 s or more."""
        return self._disagreement

    def reset(self, z: float) -> None:
        """Seed the integration at ``z`` and clear every gate and anchor state.

        Raises
        ------
        ValueError
            If ``z`` is not finite.
        """
        if not _finite(z):
            raise ValueError(f"z must be finite, got {z!r}")
        self._z = float(z)
        self._vz_trusted = 0.0
        self._stamp = None
        self._reject_run = 0
        self._anchor_stamp = None
        self._disagree_since = None
        self._disagreement = False
        self._seeded = True

    def update_velocity(self, vz_mps: float, stamp_sec: float) -> float:
        """Gate one vertical speed sample and integrate it.

        Parameters
        ----------
        vz_mps : float
            Reported vertical speed, metres per second, positive up.
        stamp_sec : float
            Sample time, seconds. The first sample after :meth:`reset` only
            sets the time base. A stamp that does not advance is an absent
            sample and changes nothing.

        Returns
        -------
        float
            The altitude estimate after this sample.
        """
        if not _finite(vz_mps) or not _finite(stamp_sec):
            return self._z
        cfg = self._cfg
        if self._stamp is None:
            self._stamp = float(stamp_sec)
            if abs(vz_mps) <= cfg.vz_max_speed_mps:
                self._vz_trusted = float(vz_mps)
            return self._z

        dt = float(stamp_sec) - self._stamp
        if dt <= 0.0:
            return self._z
        self._stamp = float(stamp_sec)
        dt = min(dt, MAX_STEP_SEC)

        plausible = (
            abs(vz_mps) <= cfg.vz_max_speed_mps
            and abs(vz_mps - self._vz_trusted) / dt <= cfg.vz_max_accel_mps2
        )
        if plausible:
            self._reject_run = 0
            trusted = float(vz_mps)
        else:
            self._reject_run += 1
            if self._reject_run >= max(1, int(cfg.vz_reject_streak)):
                self._reject_run = 0
                trusted = float(vz_mps)
            else:
                trusted = self._vz_trusted

        self._vz_trusted = trusted
        self._z += trusted * dt
        return self._z

    def update_anchor(self, alt_m: float, stamp_sec: float) -> None:
        """Pull the estimate toward an external altitude, inside the gate.

        Parameters
        ----------
        alt_m : float
            External altitude in the estimator's own frame, metres.
        stamp_sec : float
            Sample time, seconds, on the anchor's own time base. The first
            sample only sets it.
        """
        if not _finite(alt_m) or not _finite(stamp_sec):
            return
        cfg = self._cfg
        previous = self._anchor_stamp
        if previous is not None and float(stamp_sec) <= previous:
            return
        self._anchor_stamp = float(stamp_sec)

        innovation = float(alt_m) - self._z
        if abs(innovation) <= cfg.altitude_anchor_gate_m:
            self._disagree_since = None
            self._disagreement = False
            if previous is None or cfg.altitude_anchor_tau_sec <= 0.0:
                return
            dt = min(float(stamp_sec) - previous, MAX_STEP_SEC)
            self._z += (1.0 - math.exp(-dt / cfg.altitude_anchor_tau_sec)) * innovation
            return

        if self._disagree_since is None:
            self._disagree_since = float(stamp_sec)
        if float(stamp_sec) - self._disagree_since >= DISAGREEMENT_PERSIST_SEC:
            self._disagreement = True
