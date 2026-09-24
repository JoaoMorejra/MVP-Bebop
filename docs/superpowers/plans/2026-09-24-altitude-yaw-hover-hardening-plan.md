# Altitude, Yaw and Hover Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six diagnosed gaps in `mvp_mission_bebop`'s vertical-axis and
hover behaviour — sonar false-climb rejection, a true positional freeze during
nadir inspection, a translational-lift feedforward trim, and a structural
hardening of the yaw invariant — without regressing anything already passing.

**Architecture:** Every fix is additive and gated by a default that reproduces
current behaviour until deliberately tuned: a new hysteretic plausibility
filter sits between the real odometry callback and the state the mission
reads; the existing settlement detector gains an optional vertical-speed
criterion; the existing altitude governor gains an optional feedforward term
wired from a zero default; the actuator proxy gains an unconditional yaw
clamp. No new control loop, no new ROS node, no change to the message
contracts the GCS depends on.

**Tech Stack:** Python 3.12, ROS 2 Jazzy, pytest. No new third-party
dependencies.

**Spec:** [docs/superpowers/specs/2026-09-24-altitude-yaw-hover-hardening-design.md](../specs/2026-09-24-altitude-yaw-hover-hardening-design.md)

## Global Constraints

- Do not modify anything under `nectar-sdk` or `ros2_bebop_driver` (Golden
  Rule: read as the source of truth, never patched from this repo).
- Every new tunable defaults to a value that reproduces current behaviour
  (`feedforward_gain=0.0`, plausibility filter changes nothing when a signal
  never violates its physical envelope) until a field measurement justifies
  changing it.
- No emojis, no redundant comments, NumPy/SciPy-style docstrings on every new
  public class and method, strict typing (`Optional`, `Union` where needed).
- **Do not run `git commit` at any point in this work.** Leave every change
  in the working tree, uncommitted, for the user's own review. Do not run
  `git add` either — leave staging to the user.
- Run the full suite from the package root
  (`/home/jv/ros2_ws/src/mvp_mission_bebop`) after every task:
  `python3 -m pytest test/ -q`. A task is not done until this is 100% green.

## Review Focus

- **A non-finite or zero-`dt` sample reaching the new plausibility filter** —
  a stalled clock or a repeated timestamp must hold the last trusted value,
  not divide by zero or accept the sample unconditionally. Covered in Task 1.
- **The plausibility filter latching forever against a genuine altitude
  change** (e.g., the drone actually landing, or a deliberate mid-mission
  re-calibration) — a sustained run of "implausible" samples must eventually
  be accepted as a new regime. Covered in Task 1.
- **`SettlementCriteria.max_vertical_speed` left at its default (`None`)** —
  every existing caller of `SettlementDetector` (the takeoff gate, the RTL
  touchdown gate, if any) must keep working unmodified, since the new field is
  optional. Covered in Task 3.
- **`AltitudeHoldGovernor.compute_vz` called positionally by existing code**
  (`compute_vz(altitude, dt)`, no keyword) after the signature gains a third
  parameter — the new parameter must be strictly additive and keyword-safe.
  Covered in Task 5.
- **A caller that never passes `vyaw=0.0` explicitly** (relies on the
  parameter default) still reaching `BenchtopDroneProxy.move_velocity` — the
  hardening clamp must not depend on the caller having passed anything for
  the assertion path to be exercised. Covered in Task 7.

---

## Task 1: Altitude plausibility filter (new module)

**Files:**
- Create: `mvp_mission_bebop/estimation/altitude_plausibility.py`
- Test: `test/test_altitude_plausibility.py`

**Interfaces:**
- Produces: `PlausibilityLimits(max_speed_mps: float, max_accel_mps2: float, reject_streak: int = 3)`,
  `AltitudePlausibilityFilter(limits: PlausibilityLimits, initial_altitude: float = 0.0)`
  with `.update(measured_altitude: float, dt: float) -> float`, `.reset(altitude: float = 0.0) -> None`,
  and `.last_trusted -> float`.

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the sonar false-climb plausibility gate."""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.estimation.altitude_plausibility import (
    AltitudePlausibilityFilter,
    PlausibilityLimits,
)

LIMITS = PlausibilityLimits(max_speed_mps=0.40, max_accel_mps2=0.40, reject_streak=3)
DT = 1.0 / 15.0


def test_rejects_invalid_limits():
    with pytest.raises(ValueError):
        PlausibilityLimits(max_speed_mps=0.0, max_accel_mps2=0.4)
    with pytest.raises(ValueError):
        PlausibilityLimits(max_speed_mps=0.4, max_accel_mps2=0.0)
    with pytest.raises(ValueError):
        PlausibilityLimits(max_speed_mps=0.4, max_accel_mps2=0.4, reject_streak=0)


def test_a_smooth_climb_passes_through_unchanged():
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.0)
    altitude = 1.0
    for _ in range(30):
        altitude += 0.05 * DT  # 5 cm/s, well inside the envelope
        trusted = filt.update(altitude, DT)
        assert trusted == pytest.approx(altitude)


def test_a_sonar_dropout_spike_is_held_at_the_last_trusted_value():
    """The failure this filter exists for: one obstacle-shortened range sample."""
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    trusted = filt.update(1.55, DT)
    assert trusted == pytest.approx(1.55)

    # A jump of 1.2 m in one 1/15 s cycle is not achievable at 0.40 m/s^2.
    spike = filt.update(2.75, DT)
    assert spike == pytest.approx(1.55), "a single-cycle spike must be rejected"


def test_a_single_spike_does_not_start_a_permanent_rejection():
    """One outlier only. The next plausible sample is trusted normally."""
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    filt.update(2.75, DT)
    recovered = filt.update(1.56, DT)
    assert recovered == pytest.approx(1.56)


def test_a_sustained_new_regime_is_eventually_accepted():
    """The drone actually descending onto a landing pad, not a sonar artefact."""
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    trusted = 1.55
    for _ in range(LIMITS.reject_streak):
        trusted = filt.update(0.05, DT)
    assert trusted == pytest.approx(0.05), "a sustained run must be accepted as real"


def test_non_finite_sample_is_ignored():
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    assert filt.update(math.nan, DT) == pytest.approx(1.55)
    assert filt.last_trusted == pytest.approx(1.55)


def test_zero_or_negative_dt_is_ignored():
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    assert filt.update(1.90, 0.0) == pytest.approx(1.55)
    assert filt.update(1.90, -0.01) == pytest.approx(1.55)


def test_reset_reseeds_the_trusted_value():
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    filt.update(2.75, DT)
    filt.reset(0.10)
    assert filt.last_trusted == pytest.approx(0.10)
    assert filt.update(0.11, DT) == pytest.approx(0.11)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_altitude_plausibility.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mvp_mission_bebop.estimation.altitude_plausibility'`

- [ ] **Step 3: Write the module**

```python
"""Slew-rate plausibility gating for the fused altitude estimate.

The Bebop holds height with a downward ultrasonic rangefinder. Flying low over
an obstacle -- a bench, a parked vehicle, a person -- shortens the measured
range, the firmware reads that as lost height, and climbs to correct it. No
raw range is exposed on this driver to filter directly: ``bebop_driver_node.cpp``
integrates the fused vertical speed estimate into ``/bebop/odom.z``, and the
one other altitude topic the driver exposes (``/bebop/states/altitude``) is
plausibly the same class of fused estimate, not an independent one. The only
lever available from this package is bounding how fast the trusted reading is
allowed to move.

A true climb or sink is bounded by the airframe's own dynamics. An
obstacle-induced range-shortening event is a near-instantaneous jump nothing
in the airframe's real trajectory can produce. This filter holds the last
trusted altitude across any sample that violates that physical envelope, and
requires a run of consecutive outliers -- not a single one -- before accepting
a new regime, so a genuine step change (the drone actually landing) is not
rejected forever.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PlausibilityLimits:
    """Physical envelope a genuine altitude change must respect."""

    #: Ceiling on the airframe's real vertical speed, in metres per second.
    max_speed_mps: float
    #: Ceiling on the airframe's real vertical acceleration, in metres per
    #: second squared. Sized from the same bound the altitude governor already
    #: profiles its own commands against
    #: (``AltitudeGovernorConfig.max_accel_mps2``), since a controller that
    #: cannot command more than this cannot have produced more than this
    #: either.
    max_accel_mps2: float
    #: Consecutive out-of-envelope samples required before the envelope is
    #: abandoned in favour of the new reading, rather than held forever.
    reject_streak: int = 3

    def __post_init__(self) -> None:
        if self.max_speed_mps <= 0.0:
            raise ValueError(f"max_speed_mps must be positive, got {self.max_speed_mps!r}")
        if self.max_accel_mps2 <= 0.0:
            raise ValueError(f"max_accel_mps2 must be positive, got {self.max_accel_mps2!r}")
        if self.reject_streak < 1:
            raise ValueError(f"reject_streak must be at least 1, got {self.reject_streak!r}")


class AltitudePlausibilityFilter:
    """Holds the last trusted altitude across a sample the airframe could not
    have produced.

    Not a Kalman filter: there is no documented noise model for the Bebop's
    fused altitude estimate to fit one against, and the failure this exists to
    reject is a step discontinuity, not zero-mean noise. A bounded slew-rate
    gate with accept/reject hysteresis is the simplest model that rejects the
    failure without needing a tuned covariance.
    """

    __slots__ = ("_limits", "_last_trusted", "_reject_run")

    def __init__(self, limits: PlausibilityLimits, initial_altitude: float = 0.0) -> None:
        self._limits = limits
        self._last_trusted = initial_altitude
        self._reject_run = 0

    @property
    def last_trusted(self) -> float:
        """Most recently accepted altitude, in metres."""
        return self._last_trusted

    def reset(self, altitude: float = 0.0) -> None:
        """Re-anchor the filter, discarding any pending rejection streak."""
        self._last_trusted = altitude
        self._reject_run = 0

    def update(self, measured_altitude: float, dt: float) -> float:
        """Filter one altitude sample against the physical envelope.

        Parameters
        ----------
        measured_altitude : float
            The raw fused reading for this cycle, in metres.
        dt : float
            Elapsed interval since the previous sample, in seconds. A
            non-positive value is treated as an absent sample: the last
            trusted altitude is returned unchanged, since no meaningful
            envelope can be computed against it.

        Returns
        -------
        float
            ``measured_altitude`` when it falls inside the envelope reachable
            from the last trusted value in ``dt`` seconds, otherwise the last
            trusted value.
        """
        if not math.isfinite(measured_altitude) or dt <= 0.0:
            return self._last_trusted

        limits = self._limits
        max_excursion = limits.max_speed_mps * dt + 0.5 * limits.max_accel_mps2 * dt * dt
        excursion = measured_altitude - self._last_trusted

        if abs(excursion) <= max_excursion:
            self._reject_run = 0
            self._last_trusted = measured_altitude
            return self._last_trusted

        self._reject_run += 1
        if self._reject_run >= limits.reject_streak:
            # A run this long is no longer noise: accept the new regime rather
            # than latch onto a stale reading indefinitely.
            self._reject_run = 0
            self._last_trusted = measured_altitude
            return self._last_trusted

        return self._last_trusted
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_altitude_plausibility.py -v`
Expected: 8 passed

---

## Task 2: Wire the plausibility filter into real odometry ingestion

**Files:**
- Modify: `mvp_mission_bebop/telemetry/odometry.py` (`OdometrySupervisor.__init__`, `odometry_callback`)
- Modify: `mvp_mission_bebop/parameters.py` (`CalibrationConfig`)
- Test: `test/test_odometry_plausibility.py`

**Interfaces:**
- Consumes: `AltitudePlausibilityFilter`, `PlausibilityLimits` from Task 1.
- Produces: `OdometrySupervisor.current_raw_altitude` now reflects the
  filtered altitude for samples arriving through `odometry_callback`.
  `inject_synthetic_sample` (the benchtop/simulator path) is deliberately
  **not** filtered — the simulator is a controlled physical model, not a
  sonar, and existing tests construct it with instantaneous state changes
  that must keep working.

**Why only `odometry_callback` is filtered:** the plausibility filter exists
to reject a hardware artefact of the real ultrasonic rangefinder. The
benchtop kinematic simulator (`actuators/simulator.py`) never produces that
artefact by construction, and several existing tests drive
`inject_synthetic_sample` with deliberate step changes to set up scenarios
(ceiling breach, calibration). Filtering that path would either break those
tests or require them to be rewritten for a hazard that does not apply to
simulated flight — out of scope for this fix.

- [ ] **Step 1: Write the failing test**

```python
"""The sonar-spike gate applied to real odometry ingestion.

`inject_synthetic_sample` (the benchtop path) is deliberately exempt -- see
`OdometrySupervisor.odometry_callback`'s docstring for why.
"""

from __future__ import annotations

from mvp_mission_bebop.parameters import CalibrationConfig, FlightKinematicsConfig, TimeoutsConfig
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor


class FakeOdometryMsg:
    def __init__(self, z: float):
        self.pose = type("P", (), {"pose": type("PP", (), {
            "position": type("Pos", (), {"x": 0.0, "y": 0.0, "z": z})(),
            "orientation": type("Ori", (), {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0})(),
        })()})()
        self.twist = type("T", (), {"twist": type("TT", (), {
            "linear": type("Lin", (), {"x": 0.0, "y": 0.0, "z": 0.0})(),
        })()})()


def make_supervisor(**overrides):
    calib = CalibrationConfig(
        plausibility_max_speed_mps=0.40,
        plausibility_max_accel_mps2=0.40,
        plausibility_reject_streak=3,
        **overrides,
    )
    return OdometrySupervisor(FlightKinematicsConfig(), TimeoutsConfig(), calib)


def feed(supervisor, z, dt, clock):
    clock.advance(dt)
    supervisor.odometry_callback(FakeOdometryMsg(z))


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_a_single_cycle_sonar_spike_does_not_reach_current_raw_altitude():
    supervisor = make_supervisor()
    supervisor.odometry_callback(FakeOdometryMsg(1.55))
    assert supervisor.current_raw_altitude == 1.55

    supervisor.odometry_callback(FakeOdometryMsg(2.75))
    assert supervisor.current_raw_altitude == 1.55, "a false-climb spike reached raw altitude"


def test_a_sustained_new_regime_is_accepted():
    supervisor = make_supervisor()
    supervisor.odometry_callback(FakeOdometryMsg(1.55))
    for _ in range(3):
        supervisor.odometry_callback(FakeOdometryMsg(0.05))
    assert supervisor.current_raw_altitude == 0.05


def test_plausibility_can_be_disabled():
    supervisor = make_supervisor(plausibility_enabled=False)
    supervisor.odometry_callback(FakeOdometryMsg(1.55))
    supervisor.odometry_callback(FakeOdometryMsg(2.75))
    assert supervisor.current_raw_altitude == 2.75


def test_synthetic_injection_bypasses_the_filter():
    supervisor = make_supervisor()
    supervisor.inject_synthetic_sample(x=0.0, y=0.0, z=1.55)
    supervisor.inject_synthetic_sample(x=0.0, y=0.0, z=2.75)
    assert supervisor.current_raw_altitude == 2.75
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest test/test_odometry_plausibility.py -v`
Expected: FAIL with `TypeError: CalibrationConfig.__init__() got an unexpected keyword argument 'plausibility_max_speed_mps'`

- [ ] **Step 3: Extend `CalibrationConfig`**

In `mvp_mission_bebop/parameters.py`, inside `class CalibrationConfig` (after
`sample_buffer_size: int = 50`), add:

```python
    # -------------------------------------------------- altitude plausibility

    #: Master switch for the sonar false-climb plausibility gate applied to
    #: live odometry. With this false the raw altitude is trusted exactly as
    #: before this fix, which is the escape hatch for a field session where
    #: the gate itself misbehaves.
    plausibility_enabled: bool = True
    #: Ceiling on the airframe's real vertical speed the gate will accept in
    #: one cycle, in metres per second.
    plausibility_max_speed_mps: float = 1.00
    #: Ceiling on the airframe's real vertical acceleration, in metres per
    #: second squared. Matches the altitude governor's own command profile
    #: limit (``AltitudeGovernorConfig.max_accel_mps2``): a controller that
    #: cannot command more than this cannot have produced more than this.
    plausibility_max_accel_mps2: float = 0.40
    #: Consecutive out-of-envelope samples required before a new regime is
    #: accepted rather than held.
    plausibility_reject_streak: int = 3
```

- [ ] **Step 4: Wire the filter into `OdometrySupervisor`**

In `mvp_mission_bebop/telemetry/odometry.py`, add the import:

```python
from mvp_mission_bebop.estimation.altitude_plausibility import (
    AltitudePlausibilityFilter,
    PlausibilityLimits,
)
```

In `OdometrySupervisor.__init__`, after `self.current_raw_altitude: float = 0.0`, add:

```python
        self._altitude_filter = AltitudePlausibilityFilter(
            PlausibilityLimits(
                max_speed_mps=self.calibration_cfg.plausibility_max_speed_mps,
                max_accel_mps2=self.calibration_cfg.plausibility_max_accel_mps2,
                reject_streak=self.calibration_cfg.plausibility_reject_streak,
            )
        )
        self._last_filter_timestamp: Optional[float] = None
```

Replace the body of `odometry_callback` where it calls `self._store_sample`
so the real-hardware path filters `z` first while the synthetic path
(`inject_synthetic_sample`) remains untouched. Change:

```python
        self._store_sample(
            x=sample[0],
            y=sample[1],
            z=sample[2],
            vx=sample[3],
            vy=sample[4],
            vz=sample[5],
            yaw=sample[6],
        )
```

to:

```python
        z = sample[2]
        if self.calibration_cfg.plausibility_enabled:
            now = time.monotonic()
            dt = 0.0 if self._last_filter_timestamp is None else now - self._last_filter_timestamp
            self._last_filter_timestamp = now
            if dt <= 0.0:
                # First sample, or two callbacks landing on one clock tick:
                # nothing to gate against yet. Seed the filter rather than
                # reject, since a fresh filter has no trusted value to fall
                # back on.
                self._altitude_filter.reset(z)
            else:
                z = self._altitude_filter.update(z, dt)

        self._store_sample(
            x=sample[0],
            y=sample[1],
            z=z,
            vx=sample[3],
            vy=sample[4],
            vz=sample[5],
            yaw=sample[6],
        )
```

Add a docstring note to `odometry_callback` (in its existing docstring,
appended as a new paragraph) explaining the exemption for
`inject_synthetic_sample`:

```python
        This is the only ingestion path gated by the altitude plausibility
        filter (``estimation.altitude_plausibility``). ``inject_synthetic_sample``,
        used by the benchtop kinematic simulator, is deliberately exempt: the
        simulator is a controlled physical model that cannot produce the
        sonar-dropout artefact the filter exists to reject, and several tests
        drive it with intentional step changes that must keep working.
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest test/test_odometry_plausibility.py -v`
Expected: 4 passed

- [ ] **Step 6: Run the full odometry test file to check for regressions**

Run: `python3 -m pytest test/test_stage_altitude_wiring.py -v`
Expected: all passed (this file exercises `OdometrySupervisor` from other
angles; a regression here means the filter's zero-`dt`/first-sample handling
is wrong)

---

## Task 3: Extend `SettlementCriteria`/`SettlementDetector` with a vertical-speed gate

**Files:**
- Modify: `mvp_mission_bebop/estimation/convergence.py`
- Test: `test/test_convergence.py` (append)

**Interfaces:**
- Produces: `SettlementCriteria(..., max_vertical_speed: Optional[float] = None)`,
  `SettlementDetector.update(..., vz: float = 0.0, ...)`. Both are additive
  and default to the exact current behaviour when omitted.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_convergence.py`:

```python
def test_vertical_speed_criterion_is_optional_and_defaults_off():
    """Existing callers that never pass `vz` are unaffected."""
    criteria = SettlementCriteria(
        window_sec=1.0, min_samples=5, max_speed=0.05, max_position_sigma=0.06
    )
    report = drive(SettlementDetector(criteria), lambda i: (0.01, -0.005, 0.01, 0.012))
    assert report.settled, report.reason


def test_a_governor_still_actively_climbing_blocks_settlement():
    """The Ponto 3 defect: horizontally still, vertically still correcting."""
    criteria = SettlementCriteria(
        window_sec=1.0,
        min_samples=5,
        max_speed=0.05,
        max_position_sigma=0.06,
        max_vertical_speed=0.02,
    )
    detector = SettlementDetector(criteria)
    report = None
    timestamp = 0.0
    for _ in range(40):
        report = detector.update(
            x=0.01, y=-0.005, speed=0.01, vz=0.06, timestamp=timestamp
        )
        timestamp += STEP
    assert not report.settled
    assert "vertical" in report.reason


def test_settlement_resumes_once_the_governor_stops_correcting():
    criteria = SettlementCriteria(
        window_sec=1.0,
        min_samples=5,
        max_speed=0.05,
        max_position_sigma=0.06,
        max_vertical_speed=0.02,
    )
    detector = SettlementDetector(criteria)
    timestamp = 0.0
    for _ in range(20):
        detector.update(x=0.01, y=-0.005, speed=0.01, vz=0.06, timestamp=timestamp)
        timestamp += STEP
    report = None
    for _ in range(20):
        report = detector.update(x=0.01, y=-0.005, speed=0.01, vz=0.0, timestamp=timestamp)
        timestamp += STEP
    assert report.settled, report.reason
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_convergence.py -v`
Expected: FAIL with `TypeError: SettlementCriteria.__init__() got an unexpected keyword argument 'max_vertical_speed'`

- [ ] **Step 3: Extend the criteria, report, and detector**

In `mvp_mission_bebop/estimation/convergence.py`, add to `SettlementCriteria`
(after `max_distance: Optional[float] = None`):

```python
    #: Optional ceiling on the mean absolute *commanded* vertical speed across
    #: the window, in normalized units (the same units
    #: ``AltitudeHoldGovernor.compute_vz`` returns). ``None`` disables the
    #: check, reproducing the detector's behaviour before this field existed.
    #:
    #: This is deliberately the commanded value, not a measured vertical
    #: speed: the firmware's own autonomous hover mode (``do_hover``) engages
    #: only when the commanded ``gaz_speed`` is within 0.001 of zero
    #: (``bebop.cpp::Bebop::move``), so what determines whether the airframe
    #: can actually freeze is what is being asked of it, not what the noisy
    #: altitude estimate says happened.
    max_vertical_speed: Optional[float] = None
```

Add `mean_vertical_speed: float` to `SettlementReport` (after `max_distance: float`):

```python
    mean_vertical_speed: float
```

and extend its `__str__`:

```python
    def __str__(self) -> str:
        return (
            f"{'SETTLED' if self.settled else 'unsettled'} ({self.reason}): "
            f"n={self.samples} span={self.span_sec:.2f}s "
            f"v_mean={self.mean_speed:.3f} sigma_pos={self.position_sigma:.3f} "
            f"d_max={self.max_distance:.3f} vz_mean={self.mean_vertical_speed:.3f}"
        )
```

Change the sample tuple comment and storage in `SettlementDetector.__init__`:

```python
        # (timestamp, x, y, speed, distance, vz)
        self._samples: Deque[Tuple[float, float, float, float, float, float]] = deque()
```

Change `update`'s signature and append call:

```python
    def update(
        self,
        *,
        x: float,
        y: float,
        speed: float,
        distance: float = 0.0,
        vz: float = 0.0,
        timestamp: Optional[float] = None,
    ) -> SettlementReport:
```

```python
        now = self._clock() if timestamp is None else timestamp
        self._samples.append((now, x, y, speed, distance, vz))
```

Add the `vz` parameter's docstring entry alongside `distance`'s:

```python
        vz : float
            Commanded vertical speed for this observation, in normalized
            units. Compared against ``criteria.max_vertical_speed`` when that
            is configured.
```

In `_evaluate`, extract the new column and fold it into the verdict:

```python
        distances = [s[4] for s in self._samples]
        vertical_speeds = [s[5] for s in self._samples]

        mean_speed = statistics.fmean(speeds)
        sigma = math.sqrt(statistics.pvariance(xs) + statistics.pvariance(ys))
        furthest = max(distances)
        mean_vz = statistics.fmean(abs(v) for v in vertical_speeds)

        reason = "converged"
        settled = True

        if span < criteria.window_sec:
            settled = False
            reason = f"span {span:.2f}s < {criteria.window_sec:.2f}s"
        elif criteria.max_distance is not None and furthest > criteria.max_distance:
            settled = False
            reason = f"excursion {furthest:.3f}m > {criteria.max_distance:.3f}m"
        elif sigma > criteria.max_position_sigma:
            settled = False
            reason = f"position sigma {sigma:.3f}m > {criteria.max_position_sigma:.3f}m"
        elif mean_speed > criteria.max_speed:
            settled = False
            reason = f"mean speed {mean_speed:.3f} > {criteria.max_speed:.3f} m/s"
        elif criteria.max_vertical_speed is not None and mean_vz > criteria.max_vertical_speed:
            settled = False
            reason = f"vertical speed {mean_vz:.3f} > {criteria.max_vertical_speed:.3f}"

        return SettlementReport(
            settled=settled,
            reason=reason,
            samples=count,
            span_sec=span,
            mean_speed=mean_speed,
            position_sigma=sigma,
            max_distance=furthest,
            mean_vertical_speed=mean_vz,
        )
```

The early-return branch in `_evaluate` (insufficient sample count) also
constructs a `SettlementReport` — add `mean_vertical_speed=0.0` to that
constructor call too.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_convergence.py -v`
Expected: all passed (existing tests plus the 3 new ones)

- [ ] **Step 5: Check the other consumer of this module for regressions**

Run: `python3 -m pytest test/test_takeoff.py -v`
Expected: all passed (`steps/takeoff.py` also builds a `SettlementReport`
indirectly through `SettlementDetector`; confirms the new field did not break
its construction)

---

## Task 4: Feed the commanded vertical speed into Stage 4's settlement gate

**Files:**
- Modify: `mvp_mission_bebop/parameters.py` (`InspectionConfig`)
- Modify: `mvp_mission_bebop/steps/inspection.py` (`_hold`, `_await_stillness`)
- Test: `test/test_inspection_hover.py` (append)

**Interfaces:**
- Consumes: `SettlementCriteria.max_vertical_speed`, `SettlementDetector.update(..., vz=...)` from Task 3.
- Produces: `NadirInspectionStep._hold(ctx, dt=None) -> float` (previously
  returned `None`; every existing call site discards the return value, so
  this is not a breaking change for any caller in this file).

- [ ] **Step 1: Write the failing test**

Append to `test/test_inspection_hover.py`. This introduces a governor stub
that reports an active correction, and drives `_await_stillness` directly
(a method not otherwise covered by this file's existing tests):

```python
from mvp_mission_bebop.steps.inspection import NadirInspectionStep as _Step


class SinkingGovernor:
    """Reports a sustained descent-side correction, as during a real sink."""

    def compute_vz(self, _altitude, _dt=None):
        return -0.06


def test_settlement_waits_out_an_active_altitude_correction():
    """The Ponto 3 defect: horizontally still, but the governor is still
    fighting a sink, so `do_hover` cannot actually be engaged yet."""
    ctx = Ctx()
    ctx.governor = SinkingGovernor()
    ctx.params.inspection.settle_timeout_sec = 0.1
    ctx.params.kinematics.control_loop_hz = LOOP_HZ

    settled = _Step()._await_stillness(ctx)

    assert not settled, "settled while the governor was still actively correcting vz"


def test_settlement_holds_once_the_governor_is_idle():
    ctx = Ctx()
    ctx.params.kinematics.control_loop_hz = LOOP_HZ
    ctx.params.inspection.settle_window_sec = 0.05
    ctx.params.inspection.settle_min_samples = 2
    ctx.params.inspection.settle_timeout_sec = 0.5

    settled = _Step()._await_stillness(ctx)

    assert settled
```

- [ ] **Step 2: Run the tests to verify the first one fails**

Run: `python3 -m pytest test/test_inspection_hover.py -k settlement -v`
Expected: `test_settlement_waits_out_an_active_altitude_correction` FAILS
(current gate ignores vertical speed entirely, so it settles regardless)

- [ ] **Step 3: Add the config field**

In `mvp_mission_bebop/parameters.py`, inside `class InspectionConfig` (after
`settle_max_position_sigma_m: float = 0.04`), add:

```python
    #: Ceiling on the mean absolute commanded vertical speed tolerated during
    #: the stillness window, in the same normalized units
    #: ``AltitudeHoldGovernor.compute_vz`` returns.
    #:
    #: The horizontal criteria above say nothing about the vertical axis: a
    #: hover can be statistically still in x/y while the altitude governor is
    #: still actively correcting a sink, and the firmware's autonomous
    #: `do_hover` mode will not engage while any nonzero `gaz_speed` is being
    #: commanded (`bebop.cpp::Bebop::move`). This closes that gap.
    settle_max_vertical_speed: float = 0.02
```

- [ ] **Step 4: Wire `_hold` to report the commanded `vz`, and feed it to the detector**

In `mvp_mission_bebop/steps/inspection.py`, change `_hold`:

```python
    @staticmethod
    def _hold(ctx: MissionContext, dt: Optional[float] = None) -> float:
        """Command a stationary hover with the altitude governor engaged.

        Returns the commanded vertical speed (post-clamp), in normalized
        units, so callers that need to know whether the vertical axis is
        still actively correcting -- see :meth:`_await_stillness` -- do not
        have to recompute it.
        """
        vz = ctx.governor.compute_vz(ctx.odom_supervisor.snapshot().relative_altitude, dt)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=safe_vz, vyaw=safe_vyaw)
        return safe_vz
```

Change `_await_stillness`'s criteria construction and loop body:

```python
        cfg = ctx.params.inspection
        detector = SettlementDetector(
            SettlementCriteria(
                window_sec=cfg.settle_window_sec,
                min_samples=cfg.settle_min_samples,
                max_speed=cfg.settle_max_speed_mps,
                max_position_sigma=cfg.settle_max_position_sigma_m,
                max_vertical_speed=cfg.settle_max_vertical_speed,
            )
        )
```

```python
        while deadline.active:
            if ctx.interrupted():
                return False

            commanded_vz = self._hold(ctx)
            snapshot = ctx.odom_supervisor.snapshot()
            dt = rate.tick()
            elapsed += dt

            report = detector.update(
                x=snapshot.x,
                y=snapshot.y,
                speed=snapshot.speed,
                vz=commanded_vz,
                timestamp=elapsed,
            )
```

Update the log line above the loop to mention the new criterion:

```python
        logger.info(
            "Verifying motionlessness: speed <= %.3f m/s, position sigma <= %.3f m, "
            "commanded vz <= %.3f, sustained for %.1f s.",
            cfg.settle_max_speed_mps,
            cfg.settle_max_position_sigma_m,
            cfg.settle_max_vertical_speed,
            cfg.settle_window_sec,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_inspection_hover.py -v`
Expected: all passed, including the two new ones

---

## Task 5: Feedforward trim in `AltitudeHoldGovernor`

**Files:**
- Modify: `mvp_mission_bebop/parameters.py` (`AltitudeGovernorConfig`)
- Modify: `mvp_mission_bebop/controllers/altitude_hold.py`
- Test: `test/test_altitude_hold.py` (append)

**Interfaces:**
- Produces: `AltitudeHoldGovernor.compute_vz(current_relative_alt, dt=None, vx_commanded=0.0) -> float`.
  The new parameter is strictly additive and keyword-safe: every existing
  positional call (`compute_vz(altitude, dt)`) is unaffected, and
  `feedforward_gain` defaults to `0.0`, so the feedforward term is inert
  until a field measurement sets a nonzero gain.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_altitude_hold.py`:

```python
def test_feedforward_is_inert_at_the_default_gain():
    """The shipped default: no behaviour change until tuned."""
    governor = hold()
    with_ff = governor.compute_vz(TARGET_M, DT, vx_commanded=1.0)
    governor2 = hold()
    without_ff = governor2.compute_vz(TARGET_M, DT, vx_commanded=0.0)
    assert with_ff == pytest.approx(without_ff)


def test_feedforward_anticipates_translation_before_altitude_sags():
    """With a nonzero gain, a horizontal command alone -- no altitude error
    yet -- should already request some climb authority."""
    governor = hold(feedforward_gain=0.05)
    command = governor.compute_vz(TARGET_M, DT, vx_commanded=1.0)
    assert command > 0.0, "feedforward did not anticipate the sink"


def test_feedforward_is_bounded_by_the_climb_ceiling():
    """A large commanded vx must not push the feedforward past max_climb_speed."""
    governor = hold(feedforward_gain=10.0, max_climb_speed=0.10)
    command = None
    for _ in range(200):
        command = governor.compute_vz(TARGET_M, DT, vx_commanded=1.0)
    assert command <= 0.10 + 1e-9


def test_feedforward_respects_positional_calls_without_vx_commanded():
    """Existing call sites that never pass vx_commanded keep working."""
    governor = hold(feedforward_gain=0.05)
    command = governor.compute_vz(TARGET_M, DT)
    assert command == pytest.approx(0.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_altitude_hold.py -k feedforward -v`
Expected: FAIL with `TypeError: AltitudeGovernorConfig.__init__() got an unexpected keyword argument 'feedforward_gain'`

- [ ] **Step 3: Add the config field**

In `mvp_mission_bebop/parameters.py`, inside `class AltitudeGovernorConfig`
(after `max_jerk_mps3: float = 2.00`), add:

```python
    # ------------------------------------------------------ lift feedforward

    #: Anticipatory climb command per unit of commanded horizontal speed
    #: (normalized-to-normalized gain; both axes share the driver's [-1, 1]
    #: command scale). The integral term already rejects the *sustained*
    #: translation-induced sink, but only after the altitude has already
    #: sagged enough to accumulate against it. This term requests climb
    #: authority the instant translation begins, ahead of that lag.
    #:
    #: Defaults to zero -- inert -- until a field measurement of the actual
    #: sink-per-commanded-speed relationship justifies a nonzero value. See
    #: the design spec's "field validation required" section.
    feedforward_gain: float = 0.0
```

- [ ] **Step 4: Implement the feedforward in `compute_vz`**

In `mvp_mission_bebop/controllers/altitude_hold.py`, change the signature and
body of `compute_vz`:

```python
    def compute_vz(
        self,
        current_relative_alt: float,
        dt: Optional[float] = None,
        vx_commanded: float = 0.0,
    ) -> float:
        """Corrective vertical velocity command, in normalized units.

        Parameters
        ----------
        current_relative_alt : float
            Altitude above the calibrated ground reference, in metres.
        dt : Optional[float]
            Elapsed interval. Callers running a paced loop should pass the
            value from ``LoopRate.tick``; when omitted the nominal period is
            assumed.
        vx_commanded : float
            The horizontal velocity command being issued this same cycle,
            normalized to the driver's [-1, 1] scale. Multiplied by
            ``config.feedforward_gain`` to anticipate the sink translation
            induces, ahead of the integral term's reactive correction.
            Defaults to zero, which reproduces this method's behaviour before
            the parameter existed.

        Returns
        -------
        float
            Normalized ``vz``, bounded by ``-max_descent_speed`` below and
            ``+max_climb_speed`` above. Positive values are a *request*: the
            failsafe suppresses them unless an altitude-hold window is open.
        """
        interval = LoopRate.clamp_interval(
            dt if dt is not None else _NOMINAL_PERIOD_SEC
        )
        cfg = self.config
        feedforward = cfg.feedforward_gain * abs(vx_commanded)

        if not math.isfinite(current_relative_alt):
            logger.warning(
                "Non-finite altitude %r; holding the vertical axis at rest.",
                current_relative_alt,
            )
            return self._release(interval, feedforward, reason="altitude unavailable")

        error = self.target_altitude - current_relative_alt
        self._error_m = error

        if error > cfg.climb_deadband_m:
            excursion = error - cfg.climb_deadband_m
        elif error < -cfg.deadband_m:
            excursion = error + cfg.deadband_m
        else:
            return self._release(interval, feedforward)

        if not self._active:
            self._active = True
            logger.info(
                "Altitude hold engaged at %.2f m against a %.2f m target (%+.2f m).",
                current_relative_alt,
                self.target_altitude,
                error,
            )

        demand = self._pid.update(-excursion, interval)
        command = self._profile.step(demand + feedforward, interval)
        command = max(-abs(cfg.max_descent_speed), min(abs(cfg.max_climb_speed), command))

        climbing = command > 0.0
        if climbing and not self._climbing:
            logger.info(
                "Altitude hold is climbing: %.2f m is %.2f m below the %.2f m setpoint. "
                "Translation-induced sink is being corrected.",
                current_relative_alt,
                error,
                self.target_altitude,
            )
        self._climbing = climbing
        return command
```

Change `_release` to accept and apply the feedforward term:

```python
    def _release(self, interval: float, feedforward: float = 0.0, reason: str = "") -> float:
        """Inside the deadband: unwind toward rest, still honouring feedforward."""
        if self._active:
            logger.debug(
                "Altitude hold released at %+.3f m of error%s.",
                self._error_m,
                f" ({reason})" if reason else "",
            )
            self._active = False
            self._climbing = False

        self._pid.update(0.0, interval)

        leak = self.config.integral_leak_sec
        if leak > 0.0:
            self._pid.bleed_integral(math.exp(-interval / leak))

        command = self._profile.step(feedforward, interval)
        cfg = self.config
        return max(-abs(cfg.max_descent_speed), min(abs(cfg.max_climb_speed), command))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_altitude_hold.py -v`
Expected: all passed, including the 4 new ones

- [ ] **Step 6: Run the controller-level regression suite**

Run: `python3 -m pytest test/test_controllers.py -v`
Expected: all passed (this file also exercises `AltitudeHoldGovernor` and
`AltitudeAntiClimbGovernor` from other angles)

---

## Task 6: Thread the commanded horizontal speed to `compute_vz`'s feedforward

**Files:**
- Modify: `mvp_mission_bebop/steps/search.py` (`_command`)
- Modify: `mvp_mission_bebop/steps/tracking.py` (`_apply`)
- Modify: `mvp_mission_bebop/steps/rtl.py` (`_cruise_backward`, `_transmit_centering`)
- Test: `test/test_stage3_integration.py`, `test/test_aruco_rtl.py` (append spy-based checks)

**Interfaces:**
- Consumes: `AltitudeHoldGovernor.compute_vz(altitude, dt, vx_commanded=...)` from Task 5.

**Deliberately not touched:** `steps/rtl.py::_emit` (the legacy odometric
fallback path used only when ArUco detection cannot be constructed) computes
`vz_governor` *before* the guidance law produces its own `vx` — the guidance
law's `vx` is itself a function of the governor's vertical state at
`_emit`'s call site, so there is no commanded horizontal value available yet
to feed forward from. Reordering that coupling is out of scope: this is
already the degraded fallback, not the primary RTL path, and threading
feedforward through it would require restructuring
`RTLGuidanceController.compute`'s interface, which the spec does not call
for. `steps/inspection.py::_hold` is also left alone: it always commands
`vx=0.0` (hovering), so `vx_commanded` defaulting to `0.0` there is already
correct.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_stage3_integration.py` (uses whatever governor spy
pattern the file's existing fixtures already provide — see the file for the
exact `Ctx`/governor construction used by its other tests):

```python
class RecordingGovernor:
    def __init__(self):
        self.calls = []

    def compute_vz(self, altitude, dt=None, vx_commanded=0.0):
        self.calls.append(vx_commanded)
        return 0.0

    def horizontal_scale(self):
        return 1.0


def test_tracking_apply_feeds_the_commanded_vx_to_the_governor():
    """Task 6 wiring: the governor sees what the step is about to command."""
    from mvp_mission_bebop.controllers.visual_servoing import ServoCommand, TrackingPhase

    ctx = make_ctx()  # reuse this file's existing context factory
    ctx.governor = RecordingGovernor()
    command = ServoCommand(
        vx=0.35, vy=0.0, phase=TrackingPhase.APPROACH, tilt_deg=0.0,
        ground_range_m=None, nadir_aligned=False,
    )
    from mvp_mission_bebop.steps.tracking import VisualServoingStep

    VisualServoingStep()._apply(ctx, command, dt=0.05)

    assert ctx.governor.calls == [0.35]
```

Append to `test/test_aruco_rtl.py` (the file already builds a full
`ClosedLoopRTLStep` context; extend its governor stub the same way the file's
existing fixtures already construct it):

```python
def test_cruise_backward_feeds_the_commanded_vx_to_the_governor():
    from mvp_mission_bebop.controllers.profiling import JerkLimitedProfile, ProfileLimits
    from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    ctx = make_ctx()  # reuse this file's existing context factory
    ctx.governor = RecordingGovernor()
    profile = JerkLimitedProfile(ProfileLimits(max_velocity=0.3, max_accel=0.4, max_jerk=2.0))
    shaper = QuantizedCommandShaper(0.06, 0.01)

    ClosedLoopRTLStep()._cruise_backward(ctx, profile, shaper, dt=0.05, cruise_mps=0.2)

    assert len(ctx.governor.calls) == 1
    assert ctx.governor.calls[0] <= 0.0, "the reverse cruise must feed a non-positive vx"
```

If `test_aruco_rtl.py` does not already expose a `RecordingGovernor` or
equivalent, define the same class shown above locally in that file rather
than importing it across test modules.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_stage3_integration.py test/test_aruco_rtl.py -k feeds_the_commanded_vx -v`
Expected: FAIL — `RecordingGovernor.compute_vz` is called with `vx_commanded=0.0` (the current call sites never pass it)

- [ ] **Step 3: Wire `search.py::_command`**

In `mvp_mission_bebop/steps/search.py`, in `_command`, change:

```python
        profiled = profile.step(target_mps, dt)
        vx = shaper.shape(ctx.speed_calibration.to_normalized(profiled), dt)
        vz = ctx.governor.compute_vz(ctx.odom_supervisor.snapshot().relative_altitude, dt)
```

to:

```python
        profiled = profile.step(target_mps, dt)
        vx = shaper.shape(ctx.speed_calibration.to_normalized(profiled), dt)
        vz = ctx.governor.compute_vz(
            ctx.odom_supervisor.snapshot().relative_altitude, dt, vx_commanded=vx
        )
```

- [ ] **Step 4: Wire `tracking.py::_apply`**

In `mvp_mission_bebop/steps/tracking.py`, in `_apply`, change:

```python
        vz = ctx.governor.compute_vz(ctx.odom_supervisor.snapshot().relative_altitude, dt)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(command.vx, command.vy)
```

to:

```python
        vz = ctx.governor.compute_vz(
            ctx.odom_supervisor.snapshot().relative_altitude, dt, vx_commanded=command.vx
        )
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(command.vx, command.vy)
```

- [ ] **Step 5: Wire `rtl.py::_cruise_backward` and `rtl.py::_transmit_centering`**

In `mvp_mission_bebop/steps/rtl.py`, in `_cruise_backward`, change:

```python
        snapshot = ctx.odom_supervisor.snapshot()
        vz = ctx.governor.compute_vz(snapshot.relative_altitude, dt)
```

to:

```python
        snapshot = ctx.odom_supervisor.snapshot()
        vz = ctx.governor.compute_vz(snapshot.relative_altitude, dt, vx_commanded=vx)
```

In `_transmit_centering`, change:

```python
        snapshot = ctx.odom_supervisor.snapshot()
        vz = ctx.governor.compute_vz(snapshot.relative_altitude, dt)
```

to:

```python
        snapshot = ctx.odom_supervisor.snapshot()
        vz = ctx.governor.compute_vz(
            snapshot.relative_altitude, dt, vx_commanded=command.vx
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_stage3_integration.py test/test_aruco_rtl.py -v`
Expected: all passed

- [ ] **Step 7: Run the search-stage regression suite**

Run: `python3 -m pytest test/test_rate.py -k search -v; python3 -m pytest test/ -k search -v`
Expected: all passed

---

## Task 7: Structural yaw-lock hardening in the actuator proxy

**Files:**
- Modify: `mvp_mission_bebop/actuators/proxy.py` (`BenchtopDroneProxy.move_velocity`)
- Test: `test/test_actuator_proxy.py` (new)

**Interfaces:**
- Produces: `BenchtopDroneProxy.move_velocity` now clamps `vyaw` to `0.0`
  unconditionally before it reaches the simulator or the SDK, regardless of
  what the caller passed, and logs at `ERROR` when the clamp actually changed
  something.

- [ ] **Step 1: Write the failing test**

```python
"""Structural hardening of the yaw invariant at the single point every
velocity command in the mission passes through.

No known call site in `mvp_mission_bebop` currently passes a nonzero `vyaw` --
every guidance law and every step was audited for this (see the design spec,
Ponto 5). This is defense in depth: it makes a future regression impossible to
introduce silently, rather than fixing a bug that has been observed.
"""

from __future__ import annotations

import logging

import pytest

from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy


class StubDrone:
    def __init__(self):
        self.calls = []

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.calls.append((vx, vy, vz, vyaw))


def test_yaw_is_clamped_to_zero_regardless_of_caller_intent():
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=False)

    proxy.move_velocity(vx=0.2, vy=0.0, vz=0.0, vyaw=0.5)

    assert drone.calls == [(0.2, 0.0, 0.0, 0.0)]


def test_a_zero_yaw_command_is_unaffected_and_silent(caplog):
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=False)

    with caplog.at_level(logging.ERROR, logger="ActuatorProxy"):
        proxy.move_velocity(vx=0.2, vy=0.0, vz=0.0, vyaw=0.0)

    assert drone.calls == [(0.2, 0.0, 0.0, 0.0)]
    assert not caplog.records


def test_a_nonzero_yaw_command_is_logged_at_error(caplog):
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=False)

    with caplog.at_level(logging.ERROR, logger="ActuatorProxy"):
        proxy.move_velocity(vyaw=0.3)

    assert any("vyaw" in record.message for record in caplog.records)


def test_no_fly_mode_also_clamps_yaw_before_the_simulator():
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=True)

    proxy.move_velocity(vx=0.1, vyaw=-0.4)

    assert drone.calls == [], "no-fly must not reach the real drone at all"
```

- [ ] **Step 2: Run the tests to verify the first three fail**

Run: `python3 -m pytest test/test_actuator_proxy.py -v`
Expected: FAIL — `move_velocity` currently passes `vyaw` through unchanged

- [ ] **Step 3: Implement the clamp**

In `mvp_mission_bebop/actuators/proxy.py`, change `move_velocity`:

```python
    def move_velocity(
        self,
        vx: float = 0.0,
        vy: float = 0.0,
        vz: float = 0.0,
        vyaw: float = 0.0,
        duration: Optional[float] = None,
    ) -> None:
        """Command a normalized body-frame velocity.

        Components are normalized to [-1, 1], not metres per second: the driver
        clamps and publishes them as a Twist that the firmware interprets as a
        throttle fraction. The command is latched until another arrives.

        ``vyaw`` is forced to zero here unconditionally. The mission's vertical
        invariant is that the airframe never rotates -- the Bebop derives its
        odometry from optical flow, and any yaw rate corrupts the horizontal
        position estimate every guidance law in this package depends on. Every
        guidance law was audited and none currently emits a nonzero ``vyaw``;
        this clamp is defense in depth against a future regression, applied at
        the single point every velocity command in the mission passes through,
        rather than trusted to hold at every call site independently.
        """
        if vyaw != 0.0:
            logger.error(
                "Rejected a nonzero vyaw=%.4f from a velocity command; the yaw "
                "invariant forbids rotation for the whole mission. Forcing to 0.0.",
                vyaw,
            )
            vyaw = 0.0

        if self.motion_tracker is not None:
            self.motion_tracker.command(vx=vx, vy=vy, vz=vz, vyaw=vyaw, duration=duration)

        if self.no_fly:
            if self.simulator is not None:
                self.simulator.command(vx, vy, vz, vyaw)
            logger.debug(
                "[NO-FLY] move_velocity: vx=%.3f, vy=%.3f, vz=%.3f, vyaw=%.3f", vx, vy, vz, vyaw
            )
            return
        self.drone.move_velocity(vx=vx, vy=vy, vz=vz, vyaw=vyaw, duration=duration)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_actuator_proxy.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the dead-reckoning regression suite**

Run: `python3 -m pytest test/test_dead_reckoning.py -v`
Expected: all passed (this file drives `BenchtopDroneProxy.move_velocity`
directly through its own `StubDrone`; confirms the clamp does not disturb the
tracker aggregation logic)

---

## Task 8: Full-suite regression validation

**Files:** none (validation only).

- [ ] **Step 1: Run the complete suite**

Run: `python3 -m pytest test/ -q`
Expected: 100% pass, zero failures, zero errors. If anything fails, stop and
fix it before proceeding — do not proceed to Step 2 with a red suite.

- [ ] **Step 2: Confirm nothing was committed**

Run: `git status`
Expected: every file this plan touched appears under "Changes not staged for
commit" or "Untracked files". No new commits on the current branch beyond
whatever existed before this work started. Do not run `git add` or
`git commit` — leave this exactly as is for the user's own review.

---

## Self-Review Notes

**Spec coverage:** Ponto 1 (already resolved, no task) and Ponto 6 (no
independent gap found beyond what Tasks 1-7 already close) are intentionally
task-less, matching the spec. Pontos 2 (Tasks 1-2), 3 (Tasks 3-4), 4 (Tasks
5-6), and 5 (Task 7) each have a task. Task 8 is the holistic
"no remaining dead time / nothing broke" check the spec's Ponto 6 asked for.

**Placeholder scan:** No TBD/TODO; every step carries literal code or an
exact command.

**Type consistency:** `compute_vz`'s new `vx_commanded` parameter name and
default (`0.0`) are identical across Tasks 5 and 6. `SettlementCriteria`'s
`max_vertical_speed` and `SettlementDetector.update`'s `vz` parameter names
are identical across Tasks 3 and 4. `AltitudePlausibilityFilter` and
`PlausibilityLimits` names and constructor signatures are identical across
Tasks 1 and 2.

**Review Focus:** covered inline in the Global Constraints section above;
each of the five items names the task whose tests exercise it.
