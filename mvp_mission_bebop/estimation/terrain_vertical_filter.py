"""Terrain-aware vertical Kalman filter: altitude that stays continuous across the pad edge.

The mission cannot read the sonar. Its only vertical feedback is
``/bebop/odom pose.z``, the driver's integral of ``SpeedChanged.speedZ``, and
the firmware's own estimator answers the pad edge in one of three ways
(section 1.3 of ``docs/PROMPT_IMPLEMENTACAO_MODOS_RTL_TOTEM_ALTITUDE.md``):

* M1, it moves the airframe to keep the sonar range: ``speedZ`` shows real
  motion, which the governor must see to oppose;
* M2, it registers the step as a vertical speed spike: ``speedZ`` lies, and
  the integral keeps a permanent offset (the legacy position gate adopts it);
* M3, nothing happens.

This filter estimates ``x = [z, v, b]``: the height in the odometry frame, the
vertical speed (up) and the bias of ``AltitudeChanged``. As in the EKF2 of
PX4, the airframe height comes from the inertial model and a step of terrain is
absorbed by a separate terrain term, never by ``z``:

* the process is driven by the ``gaz`` actually sent
  (``v+ = v + alpha * (K_u * u - v)``, ``alpha = 1 - exp(-dt / tau_v)``);
* ``speedZ`` is a measurement of ``v``, gated by its normalized innovation
  squared and by the acceleration it implies: a spike (M2) fails the
  acceleration test, a real motion (M1) passes it. Inside the edge guard
  window its noise is inflated and a rejected sample is never taken as a new
  regime; outside, ``vz_reject_streak`` rejections in a row are;
* ``AltitudeChanged`` is used only when its source is known
  (``calibration.firmware_altitude_source``): ``"baro"`` measures ``z + b``,
  ``"sonar_fused"`` measures ``z + b + T`` with ``T`` either 0 (over the pad)
  or the pad height (off it), chosen by the smaller NIS with hysteresis and a
  prior from the platform monitor. Switching ``T`` changes the measurement
  model only, never ``x``: that is what keeps the height continuous at the
  step.

When no ``speedZ`` has been accepted for :data:`COAST_SAMPLES` cycles the
filter is coasting, and the altitude governor holds its integral
(``AltitudeHoldGovernor.compute_vz(hold_integrator=True)``).

Pure: no ROS, no lock, no wall clock. The caller supplies the stamps, on the
time base of ``header.stamp``.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Final, Optional, Tuple

import numpy as np

logger = logging.getLogger("TerrainVerticalFilter")

#: Longest prediction step, seconds; the driver's ``OdometryIntegrator(0.5)``.
MAX_STEP_SEC: Final[float] = 0.5

#: Gate on the normalized innovation squared of one scalar measurement: about
#: three sigma for one degree of freedom.
NIS_GATE: Final[float] = 9.0

#: Margin by which another terrain hypothesis must beat the current one, in NIS.
NIS_HYSTERESIS: Final[float] = 4.0

#: Bonus, in NIS, of the hypothesis the platform monitor's state favours.
MONITOR_PRIOR_NIS: Final[float] = 2.0

#: Cycles without an accepted ``speedZ`` after which the filter is coasting.
COAST_SAMPLES: Final[int] = 2

#: Random walk of the ``AltitudeChanged`` bias, square metres per second, when
#: a source is known; without one the bias variance is held constant.
BIAS_WALK_M2_PER_SEC: Final[float] = 1e-4

#: Persistence of out-of-gate altitude innovations before ``datum_disagreement``, seconds.
DISAGREEMENT_PERSIST_SEC: Final[float] = 3.0

#: Initial standard deviations of ``z``, ``v`` and ``b``.
_INITIAL_SIGMA: Final[Tuple[float, float, float]] = (0.05, 0.1, 0.5)

FIRMWARE_ALTITUDE_SOURCES: Final[Tuple[str, ...]] = ("unknown", "baro", "sonar_fused")


def normalize_firmware_altitude_source(value: object) -> str:
    """Validate ``calibration.firmware_altitude_source``.

    Raises
    ------
    ValueError
        For anything but one of :data:`FIRMWARE_ALTITUDE_SOURCES`.
    """
    if not isinstance(value, str) or value not in FIRMWARE_ALTITUDE_SOURCES:
        raise ValueError(
            f"calibration.firmware_altitude_source must be one of {FIRMWARE_ALTITUDE_SOURCES}, got {value!r}"
        )
    return value


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _bounded(name: str, value: Any, low: float, high: float, *, low_open: bool) -> float:
    if not _finite(value):
        raise ValueError(f"calibration.{name} must be a finite number, got {value!r}")
    number = float(value)
    if (number <= low if low_open else number < low) or number > high:
        bracket = "(" if low_open else "["
        raise ValueError(f"calibration.{name} must lie in {bracket}{low:g}, {high:g}], got {number!r}")
    return number


class TerrainAwareVerticalFilter:
    """Linear Kalman filter over ``[z, v, b]`` with a discrete terrain hypothesis.

    Parameters
    ----------
    cfg : CalibrationConfig
        ``vertical_lag_tau_sec``, ``vertical_accel_noise_mps2``,
        ``vz_measurement_noise_mps``, ``altitude_measurement_noise_m``,
        ``terrain_step_noise_scale``, ``vz_max_accel_mps2``,
        ``vz_reject_streak`` and ``firmware_altitude_source``, read on every
        update.
    vertical_gain_mps : float
        ``K_u``: vertical speed per normalized ``gaz``, m/s
        (``kinematics.vertical_normalized_to_mps``).

    Raises
    ------
    ValueError
        If a configured value is outside its envelope, or ``vertical_gain_mps``
        is not positive.
    """

    def __init__(self, cfg: Any, vertical_gain_mps: float) -> None:
        self._cfg = cfg
        self._check(cfg)
        if not _finite(vertical_gain_mps) or vertical_gain_mps <= 0.0:
            raise ValueError(f"vertical_gain_mps must be a positive number, got {vertical_gain_mps!r}")
        self.vertical_gain_mps = float(vertical_gain_mps)
        self._x = np.zeros(3)
        self._p = np.diag(np.square(_INITIAL_SIGMA))
        self._stamp: Optional[float] = None
        self._command = 0.0
        self._v_trusted = 0.0
        self._reject_run = 0
        self._since_accept = 0
        self._hypothesis: Optional[int] = None
        self._last_nis = 0.0
        self._disagree_since: Optional[float] = None
        self._disagreement = False
        self._seeded = False

    @staticmethod
    def _check(cfg: Any) -> None:
        _bounded("vertical_lag_tau_sec", cfg.vertical_lag_tau_sec, 0.05, 2.0, low_open=True)
        _bounded("vertical_accel_noise_mps2", cfg.vertical_accel_noise_mps2, 0.0, 10.0, low_open=True)
        _bounded("vz_measurement_noise_mps", cfg.vz_measurement_noise_mps, 0.0, 1.0, low_open=True)
        _bounded("altitude_measurement_noise_m", cfg.altitude_measurement_noise_m, 0.0, 1.0, low_open=True)
        _bounded("terrain_step_noise_scale", cfg.terrain_step_noise_scale, 1.0, 1000.0, low_open=False)
        normalize_firmware_altitude_source(cfg.firmware_altitude_source)

    # ------------------------------------------------------------- readouts

    @property
    def seeded(self) -> bool:
        return self._seeded

    @property
    def z_est(self) -> float:
        """Height estimate in the odometry frame, metres."""
        return float(self._x[0])

    @property
    def v_est(self) -> float:
        """Vertical speed estimate, metres per second, positive up."""
        return float(self._x[1])

    @property
    def bias_est(self) -> float:
        """Bias of ``AltitudeChanged``, metres."""
        return float(self._x[2])

    @property
    def coasting(self) -> bool:
        """No ``speedZ`` accepted in the last :data:`COAST_SAMPLES` cycles: the estimate only propagates."""
        return self._since_accept >= COAST_SAMPLES

    @property
    def terrain_hypothesis(self) -> Optional[int]:
        """``0`` (over the pad), ``1`` (off it, ``T = h_p``), or ``None`` before any ``sonar_fused`` update."""
        return self._hypothesis

    @property
    def last_nis(self) -> float:
        """Normalized innovation squared of the last measurement."""
        return self._last_nis

    @property
    def datum_disagreement(self) -> bool:
        """``AltitudeChanged`` has stayed outside its gate for :data:`DISAGREEMENT_PERSIST_SEC` or more."""
        return self._disagreement

    @property
    def covariance(self) -> np.ndarray:
        return self._p.copy()

    # ------------------------------------------------------------ lifecycle

    def reset(self, z: float) -> None:
        """Seed the filter at height ``z`` and at rest.

        Raises
        ------
        ValueError
            If ``z`` is not finite.
        """
        if not _finite(z):
            raise ValueError(f"z must be finite, got {z!r}")
        self._x = np.array([float(z), 0.0, 0.0])
        self._p = np.diag(np.square(_INITIAL_SIGMA))
        self._stamp = None
        self._command = 0.0
        self._v_trusted = 0.0
        self._reject_run = 0
        self._since_accept = 0
        self._hypothesis = None
        self._last_nis = 0.0
        self._disagree_since = None
        self._disagreement = False
        self._seeded = True

    def note_command(self, gaz: float) -> None:
        """The normalized ``gaz`` actually sent, after the failsafe clamp. Non-finite is ignored."""
        if _finite(gaz):
            self._command = max(-1.0, min(1.0, float(gaz)))

    # ----------------------------------------------------------- prediction

    def _predict(self, dt: float) -> None:
        cfg = self._cfg
        if dt <= 0.0:
            return
        alpha = 1.0 - math.exp(-dt / float(cfg.vertical_lag_tau_sec))
        # Semi-implicit Euler: the height advances with the velocity of the
        # step it ends, z_k = z_{k-1} + dt * v_k, as the driver integrates
        # ``speedZ`` (``current.z += vz * dt`` with the new sample). An accepted
        # ``speedZ`` then corrects z through the z-v covariance by about
        # ``dt * (vz - v_prior)``, and the estimate tracks the driver's integral
        # wherever ``speedZ`` is trusted.
        retain = 1.0 - alpha
        drive = alpha * self.vertical_gain_mps * self._command
        f = np.array([[1.0, dt * retain, 0.0], [0.0, retain, 0.0], [0.0, 0.0, 1.0]])
        control = np.array([dt * drive, drive, 0.0])
        sigma_a2 = float(cfg.vertical_accel_noise_mps2) ** 2
        q = sigma_a2 * np.array([[dt**4, dt**3, 0.0], [dt**3, dt**2, 0.0], [0.0, 0.0, 0.0]])
        if cfg.firmware_altitude_source != "unknown":
            q[2, 2] = BIAS_WALK_M2_PER_SEC * dt
        self._x = f @ self._x + control
        self._p = f @ self._p @ f.T + q

    def _advance_to(self, stamp_sec: float) -> Optional[float]:
        """Predict to ``stamp_sec``; the step taken, or ``None`` for a first or a non-advancing stamp."""
        if self._stamp is None:
            self._stamp = float(stamp_sec)
            return None
        dt = float(stamp_sec) - self._stamp
        if dt <= 0.0:
            return None
        self._stamp = float(stamp_sec)
        dt = min(dt, MAX_STEP_SEC)
        self._predict(dt)
        return dt

    def _correct(self, h: np.ndarray, innovation: float, r: float) -> None:
        s = float(h @ self._p @ h) + r
        gain = (self._p @ h) / s
        self._x = self._x + gain * innovation
        identity = np.eye(3)
        kh = np.outer(gain, h)
        self._p = (identity - kh) @ self._p @ (identity - kh).T + r * np.outer(gain, gain)

    # ---------------------------------------------------------- measurements

    def update_velocity(self, vz_mps: float, stamp_sec: float, *, guard_window: bool = False) -> float:
        """Predict to ``stamp_sec`` and gate one ``speedZ`` sample.

        Parameters
        ----------
        vz_mps : float
            Reported vertical speed, metres per second, positive up.
        stamp_sec : float
            Sample time, seconds (``header.stamp`` time base). The first sample
            after :meth:`reset` only sets the time base; a stamp that does not
            advance changes nothing.
        guard_window : bool
            The platform edge guard window is open (``guard_active``, or the
            monitor in ``LEAVING``/``ENTERING``): the ``speedZ`` noise is
            multiplied by ``terrain_step_noise_scale`` and a rejected sample is
            never taken as a new regime.

        Returns
        -------
        float
            The height estimate after this sample.
        """
        if not self._seeded or not _finite(vz_mps) or not _finite(stamp_sec):
            return self.z_est
        cfg = self._cfg
        dt = self._advance_to(stamp_sec)
        if dt is None:
            return self.z_est

        r = float(cfg.vz_measurement_noise_mps) ** 2
        if guard_window:
            r *= float(cfg.terrain_step_noise_scale)
        h = np.array([0.0, 1.0, 0.0])
        innovation = float(vz_mps) - self.v_est
        s = float(h @ self._p @ h) + r
        nis = innovation * innovation / s
        self._last_nis = nis
        plausible = abs(float(vz_mps) - self._v_trusted) / dt <= float(cfg.vz_max_accel_mps2)
        accepted = nis <= NIS_GATE and plausible

        if not accepted and not guard_window:
            self._reject_run += 1
            if self._reject_run >= max(1, int(cfg.vz_reject_streak)):
                accepted = True
        if accepted:
            self._reject_run = 0
            self._since_accept = 0
            self._v_trusted = float(vz_mps)
            self._correct(h, innovation, r)
        else:
            self._since_accept += 1
            logger.info(
                "[VERT_KF] nis=%.2f accepted=0 coasting=%d T=%s z=%.3f v=%.3f",
                nis,
                int(self.coasting),
                self._hypothesis,
                self.z_est,
                self.v_est,
            )
        return self.z_est

    def update_altitude(
        self,
        altitude_m: float,
        stamp_sec: float,
        *,
        pad_height_m: float = 0.0,
        off_pad_prior: Optional[bool] = None,
    ) -> None:
        """Use one ``AltitudeChanged`` sample, as ``calibration.firmware_altitude_source`` says it measures.

        Parameters
        ----------
        altitude_m : float
            The firmware altitude in the odometry frame, metres (the caller adds
            the ground reference).
        stamp_sec : float
            Arrival time, seconds, for the disagreement persistence only; the
            update is applied at the current state.
        pad_height_m : float
            Height of the pad the flight took off from, ``h_p``; ``0`` from the
            floor, when only ``T = 0`` exists.
        off_pad_prior : bool or None
            The platform monitor's view: ``True`` off the pad (favours
            ``T = h_p``), ``False`` over it, ``None`` unknown.
        """
        if not self._seeded or not _finite(altitude_m) or not _finite(stamp_sec):
            return
        cfg = self._cfg
        source = cfg.firmware_altitude_source
        if source == "unknown":
            return
        r = float(cfg.altitude_measurement_noise_m) ** 2
        h = np.array([1.0, 0.0, 1.0])
        s = float(h @ self._p @ h) + r
        predicted = self.z_est + self.bias_est

        terrain = 0.0
        if source == "sonar_fused" and _finite(pad_height_m) and pad_height_m > 0.0:
            scores = []
            for index, offset in enumerate((0.0, float(pad_height_m))):
                nis = (float(altitude_m) - predicted - offset) ** 2 / s
                if off_pad_prior is not None and bool(index) == off_pad_prior:
                    nis -= MONITOR_PRIOR_NIS
                scores.append(nis)
            best = int(np.argmin(scores))
            if self._hypothesis is None:
                self._hypothesis = best
            elif best != self._hypothesis and scores[best] + NIS_HYSTERESIS < scores[self._hypothesis]:
                previous = self._hypothesis
                self._hypothesis = best
                logger.info(
                    "[VERT_KF] nis=%.2f accepted=1 coasting=%d T=%s z=%.3f v=%.3f (terrain %s -> %s)",
                    scores[best],
                    int(self.coasting),
                    self._hypothesis,
                    self.z_est,
                    self.v_est,
                    previous,
                    best,
                )
            terrain = float(pad_height_m) if self._hypothesis == 1 else 0.0
        elif source == "sonar_fused":
            self._hypothesis = 0

        innovation = float(altitude_m) - predicted - terrain
        nis = innovation * innovation / s
        self._last_nis = nis
        if nis <= NIS_GATE:
            self._disagree_since = None
            self._disagreement = False
            self._correct(h, innovation, r)
            return
        logger.info(
            "[VERT_KF] nis=%.2f accepted=0 coasting=%d T=%s z=%.3f v=%.3f (altitude)",
            nis,
            int(self.coasting),
            self._hypothesis,
            self.z_est,
            self.v_est,
        )
        if self._disagree_since is None:
            self._disagree_since = float(stamp_sec)
        if float(stamp_sec) - self._disagree_since >= DISAGREEMENT_PERSIST_SEC:
            self._disagreement = True
