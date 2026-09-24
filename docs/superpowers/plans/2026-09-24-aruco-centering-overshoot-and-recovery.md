# ArUco Centering Overshoot Elimination, Bounded Reacquisition and Battery-Aware Landing Gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the Stage 5 (RTL) frontal overshoot that loses the ArUco pad under the narrow `-80 deg` rear field of view, give the centering law a bounded reverse-creep reacquisition instead of a blind hover when the marker drops out mid-approach, decouple the landing-authorization gate from the millimetric convergence tolerance so the aircraft commits to touchdown as soon as it is safely over the pad, and lock in with a regression test that Stage 2/3's existing alpha-beta coast-through-dropout already preserves tracking progress across brief occlusions (glint, shadow, detector jitter).

**Architecture:** All three fixes live inside `ArucoCenteringController` (`controllers/rtl_guidance.py`), the step-level sequencing in `steps/rtl.py` untouched, and new tunables added to `ReturnToLaunchConfig` (`parameters.py`). The overshoot fix reuses `controllers.profiling.braking_velocity` -- the same stopping-distance feedforward ceiling `RTLGuidanceController._compute_longitudinal` already flies -- applied to the centering law's longitudinal (body-x) channel only, since the lateral channel is read off the camera's unprojected x-axis and is not starved by the tilt. The reacquisition fix extends `_coast()` with a bounded, direction-agnostic aft creep gated on the pre-loss speed and range, mirroring the sweep/creep pattern `VisualServoingController.note_target_lost()` already uses for Stage 2/3. The landing gate is decoupled by adding a second, looser radius (`landing_radius_m`) that the settle counter is judged against, while `centering_tolerance_m` is kept as the internal deadband/rest-mode threshold it already is. Stage 2/3's target loss handling (Point A) is validated as already correct via `estimation/target_tracker.ConstantVelocityTracker` and locked in with one new regression test rather than new production code.

**Tech Stack:** Python 3.12, pytest, no ROS/hardware dependency in the controllers or their tests (pure kinematic simulation harnesses already established in `test/test_aruco_rtl.py` and `test/test_stage3_integration.py`).

**Spec:** This plan's spec is the task brief itself (no separate spec document); the governing technical narrative is reproduced in Global Constraints and each task's docstring-quality rationale below, grounded in direct reads of `controllers/rtl_guidance.py`, `steps/rtl.py`, `parameters.py`, `controllers/visual_servoing.py`, `estimation/target_tracker.py`, `steps/tracking.py`, and the existing suites `test/test_aruco_rtl.py`, `test/test_recovery_and_touchdown.py`, `test/test_safety_invariants.py`, `test/test_stage3_integration.py`.

## Global Constraints

- No automatic git commits or pushes. Do not run `git commit` or `git push` at any point; the user reviews and commits manually.
- `vyaw` stays absent/zero everywhere in Stage 5 and Stage 2/3 -- rotation corrupts the optical-flow odometry other subsystems still read. Never add a yaw output.
- `steps/rtl.py::ClosedLoopRTLStep._touchdown` is explicitly documented as "the most thoroughly exercised code in this package" and must not be modified; only the phase-3 entry into it (the `settled` gate) may fire earlier.
- Every new/changed control-law behavior lives in `controllers/rtl_guidance.py` or `parameters.py`; `steps/rtl.py` stays sequencing-only, per the module's own docstring contract.
- All velocity ceilings on the resultant (not per-axis) must be preserved: `_saturate_pair` scales, never clips, so commanded heading is never rotated away from the marker.
- A landing must never be authorized on a cycle the marker was not seen (`settled` requires a live, in-tolerance-band observation this cycle) -- this existing invariant from `test_a_landing_is_never_authorized_on_a_cycle_the_marker_was_not_seen` must keep holding after the gate is decoupled.
- Every existing test in `test/test_aruco_rtl.py`, `test/test_recovery_and_touchdown.py`, `test/test_safety_invariants.py`, `test/test_centering_recovery.py`, `test/test_stage3_integration.py`, `test/test_rtl_guidance.py` must pass at the end of the branch, with any intentional-behavior-change updates to specific assertions called out explicitly in that task (never silently weakened).
- `python3 -m pytest test/` must be run from `/home/jv/ros2_ws/src/mvp_mission_bebop` inside the `nectar-activate` environment (`source /home/jv/ros2_ws/bin/nectar-activate`) before considering any task done.
- NumPy/SciPy-style docstrings, strict type hints, no emojis, no redundant comments -- match the existing style in `rtl_guidance.py` exactly (it is unusually well-documented; new code must read as if the same author wrote it).

## Review Focus

- A marker that is lost exactly when the drone is already parked and centered (radial ~0, speed ~0) must not trigger the new reverse creep -- creeping off a perfectly good position to "reacquire" a marker that was simply occluded by the drone's own shadow at rest would be worse than holding station. (Task 3)
- A marker lost during a genuine far-range loss (search-phase-adjacent, large radial error) must not creep either -- creep is a close-in, high-speed-approach remedy only, not a general loss policy. (Task 3)
- Raising `landing_radius_m` above `centering_tolerance_m` must never let a fast crossing-the-center transit satisfy the settle gate -- the existing residual-speed requirement (`settle_max_speed_mps`) is what prevents that, and it must still be checked against the *new* radius, not silently bypassed. (Task 2)
- The braking ceiling must never invert sign or introduce a discontinuity that fights the jerk-limited profile -- a ceiling of `0.0` at `ex_body_m == tolerance_m` must blend smoothly with the existing "within tolerance -> drive to rest" branch rather than fighting it for one cycle. (Task 1)
- A config file saved before these new fields existed must still load (mirrors the existing `test_a_config_written_before_the_aruco_fields_existed_still_loads` contract) -- new `ReturnToLaunchConfig` fields must have defaults and must not become required. (Task 4)

---

### Task 1: Braking-ceiling damping on the longitudinal centering channel

**Files:**
- Modify: `mvp_mission_bebop/mvp_mission_bebop/controllers/rtl_guidance.py:952-997` (the non-`within` branch of `ArucoCenteringController.update`)
- Test: `mvp_mission_bebop/test/test_aruco_rtl.py` (new tests appended after `test_the_deadband_stays_strictly_inside_the_convergence_gate`, around line 529)

**Interfaces:**
- Consumes: `controllers.profiling.braking_velocity(distance_to_go, max_decel, *, cruise_velocity, arrival_tolerance=0.0) -> float`, already imported at module scope in `rtl_guidance.py` (used by `RTLGuidanceController._compute_longitudinal`).
- Consumes: `self.rtl_cfg.max_accel_mps2` (existing field, `ReturnToLaunchConfig.max_accel_mps2: float = 0.25`), `self._tolerance_m`, `self._max_speed_mps` (both already computed in `__init__`).
- Produces: no new public symbols; `ArucoCenteringController.update()` keeps its existing signature and `CenteringCommand` fields.

- [ ] **Step 1: Write the failing invariant test**

Append to `test/test_aruco_rtl.py`, in the "the centering law" section (after `test_the_deadband_stays_strictly_inside_the_convergence_gate`, before "the landing gate" section):

```python
def test_the_longitudinal_channel_never_commands_more_speed_than_it_can_arrest():
    """The overshoot invariant, checked every cycle rather than at one distance.

    A commanded speed that cannot be brought to rest within the remaining
    body-x distance is, by definition, a speed that will carry the airframe
    across the marker -- and under the -80 deg tilt, across the edge of the
    rear field of view that would otherwise have kept it in frame. The bound
    is the same feedforward physics RTLGuidanceController already flies:
    v <= sqrt(2 * max_accel * (distance - tolerance)), saturated at the
    centering speed ceiling.
    """
    controller = law()
    params = MissionParameters()
    from mvp_mission_bebop.controllers.profiling import braking_velocity

    ex, ey = 0.55, -0.05
    for _ in range(300):
        command = controller.update(sighting(ex, ey), DT)
        ceiling = braking_velocity(
            abs(command.ex_body_m),
            params.rtl.max_accel_mps2,
            cruise_velocity=controller.max_speed_mps,
            arrival_tolerance=controller.tolerance_m,
        )
        # A small margin absorbs the sigma-delta shaper's dither and the two
        # independent profiles' transient combination that _reconcile already
        # bounds onto max_speed_mps -- the same margin test_the_speed_ceiling_
        # bounds_the_resultant_and_not_the_axes uses for the same reason.
        margin = 1.5 * params.rtl.quantization_step * math.sqrt(2.0)
        assert command.commanded_speed_mps <= ceiling + margin, (
            f"commanded {command.commanded_speed_mps:.4f} m/s at "
            f"{command.ex_body_m:.4f} m out, which arrests in more distance "
            f"than remains: overshoot is possible"
        )
        ex -= command.vx * DT
        ey -= command.vy * DT
        if command.settled:
            break
    assert command.settled, "never converged under the new ceiling"


def test_a_close_in_high_kp_configuration_still_cannot_overshoot():
    """The ceiling is the safety net for exactly the case tuning drift creates.

    centering_kp_x doubled -- the kind of change a future tuning pass makes
    without re-deriving the stopping-distance budget -- must still be caught by
    the physical ceiling rather than by hoping the gain stays conservative.
    """
    controller = law(centering_kp_x=0.50, centering_kd_x=0.01)
    ex = 0.12
    peak_speed_at_arrival = 0.0
    for _ in range(200):
        command = controller.update(sighting(ex, 0.0), DT)
        if abs(command.ex_body_m) <= 0.02:
            peak_speed_at_arrival = max(peak_speed_at_arrival, command.commanded_speed_mps)
        ex -= command.vx * DT
        if command.settled:
            break
    # At 2 cm out, arresting under 0.25 m/s^2 from the tolerance edge bounds
    # the speed to sqrt(2 * 0.25 * 0.02) =~ 0.10 m/s; the ceiling caps it
    # tighter still once inside the tolerance band itself.
    assert peak_speed_at_arrival <= 0.11
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
source /home/jv/ros2_ws/bin/nectar-activate
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/test_aruco_rtl.py -k "never_commands_more_speed or high_kp_configuration_still_cannot" -v
```

Expected: both FAIL (or the second one is flaky-passing by luck of low default `kp`) -- the ceiling is not applied yet, so at the very least `test_a_close_in_high_kp_configuration_still_cannot_overshoot` must fail against the doubled gain.

- [ ] **Step 3: Apply the braking ceiling to the longitudinal demand**

In `mvp_mission_bebop/mvp_mission_bebop/controllers/rtl_guidance.py`, inside `ArucoCenteringController.update()`, replace the `else` branch (currently lines 969-987):

```python
        else:
            # Measurement is the airframe's displacement *from* the marker, which
            # is the negation of the error vector pointing at it, against a zero
            # setpoint. The sign convention is the same one the odometric law
            # above uses, deliberately: one reading of "error" across the module.
            longitudinal_demand = self._longitudinal.update(-ex_body, dt)
            lateral_demand = self._lateral.update(-ey_body, dt)
            # Saturated as a vector, not per axis. Clipping the two channels
            # independently lets the resultant reach sqrt(2) times the ceiling on
            # a diagonal approach -- 0.113 against a configured 0.08 -- and it
            # also rotates the commanded direction away from the marker whenever
            # exactly one axis is railed, so the aircraft crabs in on a dog-leg
            # instead of a straight line. Scaling preserves the heading.
            longitudinal_target, lateral_target = self._saturate_pair(
                longitudinal_demand, lateral_demand
            )
            longitudinal_mps = self._longitudinal_profile.step(longitudinal_target, dt)
            lateral_mps = self._lateral_profile.step(lateral_target, dt)
            note = f"centering: radial {radial:.3f} m, target <= {self._tolerance_m:.3f} m"
```

with:

```python
        else:
            # Measurement is the airframe's displacement *from* the marker, which
            # is the negation of the error vector pointing at it, against a zero
            # setpoint. The sign convention is the same one the odometric law
            # above uses, deliberately: one reading of "error" across the module.
            longitudinal_demand = self._longitudinal.update(-ex_body, dt)
            lateral_demand = self._lateral.update(-ey_body, dt)
            # The rear field of view at a steep camera_tilt_deg is narrow, so any
            # forward overshoot risks losing the marker outright rather than just
            # costing a correction cycle. A PD term alone does not guarantee the
            # airframe can stop before it crosses the marker -- gain, filtering
            # lag and profile inertia all sit between "demand" and "velocity
            # actually flown". braking_velocity is the same stopping-distance
            # feedforward RTLGuidanceController._compute_longitudinal already
            # applies: the ceiling on how fast the airframe may move given how
            # far it still has to travel and how hard it can decelerate. Bounding
            # only the longitudinal channel is deliberate -- the lateral error is
            # read off the camera's unprojected x-axis and is not starved by the
            # tilt, so it keeps its full authority.
            longitudinal_ceiling = braking_velocity(
                abs(ex_body),
                self.rtl_cfg.max_accel_mps2,
                cruise_velocity=self._max_speed_mps,
                arrival_tolerance=self._tolerance_m,
            )
            longitudinal_target = max(
                -longitudinal_ceiling, min(longitudinal_ceiling, longitudinal_demand)
            )
            # Saturated as a vector, not per axis. Clipping the two channels
            # independently lets the resultant reach sqrt(2) times the ceiling on
            # a diagonal approach -- 0.113 against a configured 0.08 -- and it
            # also rotates the commanded direction away from the marker whenever
            # exactly one axis is railed, so the aircraft crabs in on a dog-leg
            # instead of a straight line. Scaling preserves the heading.
            longitudinal_target, lateral_target = self._saturate_pair(
                longitudinal_target, lateral_demand
            )
            longitudinal_mps = self._longitudinal_profile.step(longitudinal_target, dt)
            lateral_mps = self._lateral_profile.step(lateral_target, dt)
            note = f"centering: radial {radial:.3f} m, target <= {self._tolerance_m:.3f} m"
```

Confirm `braking_velocity` is already imported at the top of the file (it is used by `RTLGuidanceController._compute_longitudinal`); if the existing import is `from .profiling import JerkLimitedProfile, ProfileLimits, braking_velocity` or similar, no import change is needed. If `braking_velocity` is not in that import line, add it.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python3 -m pytest test/test_aruco_rtl.py -k "never_commands_more_speed or high_kp_configuration_still_cannot" -v
```

Expected: PASS.

- [ ] **Step 5: Run the full ArUco/RTL/safety suites to confirm no regression**

```bash
python3 -m pytest test/test_aruco_rtl.py test/test_rtl_guidance.py test/test_safety_invariants.py test/test_recovery_and_touchdown.py -v
```

Expected: all PASS. If `test_the_law_converges_from_a_combined_offset` or the quadrant-parametrized convergence tests slow down enough to approach their `cycles=900` ceiling without settling, that is a signal the ceiling is biting harder than intended at the default gains -- re-check `max_accel_mps2` is being read as a positive value and that `_saturate_pair` still receives the *clamped* longitudinal target, not the raw demand.

---

### Task 2: Decouple the landing-authorization gate from the convergence tolerance

**Files:**
- Modify: `mvp_mission_bebop/mvp_mission_bebop/parameters.py:733-738` (`ReturnToLaunchConfig`, the `centering_tolerance_m`/`centering_settle_cycles` block)
- Modify: `mvp_mission_bebop/mvp_mission_bebop/controllers/rtl_guidance.py:816-829` (`__init__`, tolerance/settle-target computation) and `:955-1006` (`update`, the `within`/settle computation)
- Test: `mvp_mission_bebop/test/test_aruco_rtl.py` (modify three existing tests, add two new ones)

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `ArucoCenteringController.landing_radius_m` (new `@property`, mirrors `tolerance_m`), read by no other module yet but exposed for the executive guide's manual verification step and for `steps/rtl.py`'s existing log line if a future task wants to log it (out of scope here -- do not touch `steps/rtl.py` in this task).

- [ ] **Step 1: Write the failing tests**

In `test/test_aruco_rtl.py`, add after `test_the_deadband_stays_strictly_inside_the_convergence_gate` (before "the landing gate" section, and after Task 1's two new tests if applied in the same branch):

```python
def test_the_landing_gate_is_looser_than_the_convergence_tolerance():
    """The two radii serve different purposes and must not collapse into one.

    centering_tolerance_m is the internal deadband/rest-mode switch: tight,
    because it is what the PD converges the *approach* against. landing_radius_m
    is the operational statement "this is close enough to put a 0.20 m tag on a
    0.50-1.0 m pad down safely" -- looser on purpose, because past it every
    further correction cycle spends battery on precision the touchdown itself
    does not need.
    """
    controller = law()
    assert controller.landing_radius_m >= controller.tolerance_m


def test_settling_is_authorized_before_reaching_the_tight_tolerance():
    """The whole point of the decoupled gate: land sooner, not more precisely."""
    controller = law(landing_radius_m=0.13, centering_tolerance_m=0.04)
    ex, ey, history = fly_to_centre(controller, 0.30, 0.0)

    assert history[-1].settled
    residual = math.hypot(ex, ey)
    assert residual <= controller.landing_radius_m
    # The settlement is not required to have reached the tight internal
    # tolerance; that would defeat the decoupling this task exists to add.
```

Now update the three existing tests whose assertions hard-code the *old* behavior (settlement implies residual inside `tolerance_m`). This is an intentional behavior change, not a defect being reintroduced -- the old assertions encoded "settle == converge to 4 cm", which is exactly the millimetric-precision waste Point B/C ask to remove.

Replace `test_the_law_converges_from_a_combined_offset` (currently asserting `<= controller.tolerance_m`):

```python
def test_the_law_converges_from_a_combined_offset():
    controller = law()
    ex, ey, history = fly_to_centre(controller, 0.60, -0.45)

    assert history[-1].settled, "never authorized the landing"
    assert math.hypot(ex, ey) <= controller.landing_radius_m
```

Replace `test_the_law_converges_from_every_quadrant`:

```python
@pytest.mark.parametrize(
    "ex,ey",
    [(0.80, 0.0), (-0.80, 0.0), (0.0, 0.80), (0.0, -0.80), (0.5, 0.5), (-0.5, -0.5)],
)
def test_the_law_converges_from_every_quadrant(ex, ey):
    """A single wrong sign passes three of these six and fails the rest."""
    controller = law()
    residual_x, residual_y, history = fly_to_centre(controller, ex, ey)

    assert history[-1].settled, f"did not settle from ({ex}, {ey})"
    assert math.hypot(residual_x, residual_y) <= controller.landing_radius_m
```

Replace `test_the_law_converges_under_pose_noise`:

```python
def test_the_law_converges_under_pose_noise():
    """Centimetre-scale pose jitter is the normal condition, not a fault."""
    controller = law()
    noise = 0.01
    ex, ey, history = fly_to_centre(controller, 0.50, 0.35, noise=noise, cycles=1500)

    assert history[-1].settled
    assert math.hypot(ex, ey) <= controller.landing_radius_m + 3.0 * noise
```

- [ ] **Step 2: Run the new/modified tests to verify they fail**

```bash
python3 -m pytest test/test_aruco_rtl.py -k "landing_gate_is_looser or settling_is_authorized_before or converges_from_a_combined_offset or converges_from_every_quadrant or converges_under_pose_noise" -v
```

Expected: `test_the_landing_gate_is_looser_than_the_convergence_tolerance` and `test_settling_is_authorized_before_reaching_the_tight_tolerance` FAIL with `AttributeError: 'ArucoCenteringController' object has no attribute 'landing_radius_m'` (or `TypeError` on the `law(landing_radius_m=...)` override, since the field does not exist on `ReturnToLaunchConfig` yet). The three modified convergence tests should still PASS against the old code coincidentally (old settle gate is tighter than the new bound), which is fine -- they are here to stay correct once the gate changes, not to detect the change themselves.

- [ ] **Step 3: Add the field to `ReturnToLaunchConfig`**

In `mvp_mission_bebop/mvp_mission_bebop/parameters.py`, after line 738 (`centering_settle_cycles: int = 4`), insert:

```python
    #: Radial error inside which the landing is authorized, once at residual
    #: speed for `centering_settle_cycles` consecutive cycles, in metres.
    #:
    #: Deliberately looser than `centering_tolerance_m`. The marker is a 0.20 m
    #: square printed on a landing pad 0.50-1.0 m across, so a touchdown
    #: anywhere inside this radius is still solidly on the pad. Converging
    #: tighter than this before landing buys nothing but battery: every extra
    #: settle cycle spent chasing the tight internal tolerance is a correction
    #: against an error the touchdown itself absorbs. `centering_tolerance_m`
    #: is retained unchanged as the internal deadband/rest-mode switch the PD
    #: converges the approach against; this is the separate, operational
    #: statement of when the approach is done.
    landing_radius_m: float = 0.13
```

Also change `centering_settle_cycles` default from `4` to `2` (still satisfies `test_being_inside_the_tolerance_for_one_cycle_does_not_authorize_landing`, which only requires the target be `>= 2`):

```python
    #: Consecutive cycles inside `landing_radius_m` *at residual speed* required
    #: before the landing is authorized. A single sample inside the radius is
    #: satisfied by a drone flying through it at speed; two consecutive samples
    #: at residual speed is the minimum that distinguishes "passing through"
    #: from "arrived", without paying for cycles beyond what that distinction
    #: needs.
    centering_settle_cycles: int = 2
```

- [ ] **Step 4: Decouple the gate in `ArucoCenteringController`**

In `mvp_mission_bebop/mvp_mission_bebop/controllers/rtl_guidance.py`, in `__init__`, after the existing tolerance block (lines 816-824):

```python
        self._tolerance_m: float = max(
            _MIN_CENTERING_TOLERANCE_M, abs(rtl_cfg.centering_tolerance_m)
        )
        # The deadband suppresses pixel-scale jitter; it must stay strictly
        # inside the convergence gate or the law would stop correcting while
        # still outside the tolerance it is gated on, and the phase would run to
        # its timeout with the aircraft parked just off centre.
        self._deadband_m: float = min(abs(rtl_cfg.deadband_m), 0.5 * self._tolerance_m)
        self._settle_target: int = max(1, int(rtl_cfg.centering_settle_cycles))
```

insert immediately after `self._deadband_m` and before `self._settle_target`:

```python
        # The landing gate is judged against this radius, never against
        # tolerance_m directly: the two answer different questions (the deadband
        # question is "has the PD converged"; this is "is it safe to commit to
        # the ground"), and clamping this to be no tighter than tolerance_m
        # keeps that ordering from silently inverting under a misconfiguration.
        self._landing_radius_m: float = max(
            self._tolerance_m, abs(rtl_cfg.landing_radius_m)
        )
```

Add the corresponding property next to `tolerance_m` (after line 886-887):

```python
    @property
    def landing_radius_m(self) -> float:
        """Radial error inside which the landing is authorized."""
        return self._landing_radius_m
```

In `update()`, change the settle computation. Currently (around lines 955-956 and 999-1006):

```python
        radial = math.hypot(ex_body, ey_body)
        within = radial <= self._tolerance_m
```

and:

```python
        speed_mps = math.hypot(longitudinal_mps, lateral_mps)
        # Both conditions, every cycle. Inside the tolerance but still moving is
        # a drone crossing the centre, not a drone over it.
        if within and speed_mps <= self._settle_speed_mps:
            self._settle_cycles += 1
        else:
            self._settle_cycles = 0
        settled = self._settle_cycles >= self._settle_target
```

Change to:

```python
        radial = math.hypot(ex_body, ey_body)
        within = radial <= self._tolerance_m
        within_landing_gate = radial <= self._landing_radius_m
```

and:

```python
        speed_mps = math.hypot(longitudinal_mps, lateral_mps)
        # Both conditions, every cycle. Inside the landing radius but still
        # moving is a drone crossing the pad, not a drone settled over it. The
        # gate is judged against landing_radius_m, deliberately looser than the
        # tolerance_m the PD's own rest-mode switches on above.
        if within_landing_gate and speed_mps <= self._settle_speed_mps:
            self._settle_cycles += 1
        else:
            self._settle_cycles = 0
        settled = self._settle_cycles >= self._settle_target
```

`within` (tied to `tolerance_m`) still gates the `if within: ... else: ...` branch earlier in `update()` that switches between "drive profile to zero" and "active PD correction" -- that branch is unchanged by this task. Only the settle/`settled` computation moves to `within_landing_gate`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
python3 -m pytest test/test_aruco_rtl.py -v
```

Expected: all PASS, including the two new tests and the three updated ones.

- [ ] **Step 6: Run the full RTL/safety suites to confirm no regression**

```bash
python3 -m pytest test/test_aruco_rtl.py test/test_rtl_guidance.py test/test_safety_invariants.py test/test_recovery_and_touchdown.py -v
```

Expected: all PASS. Pay particular attention to `test_the_gate_requires_consecutive_cycles`, `test_crossing_the_centre_at_speed_does_not_authorize_landing`, and `test_leaving_the_tolerance_clears_an_almost_complete_settlement`, which exercise the settle-cycle bookkeeping this task touches most directly.

---

### Task 3: Bounded aft creep on marker loss, instead of a blind hover

**Files:**
- Modify: `mvp_mission_bebop/mvp_mission_bebop/parameters.py` (after the `landing_radius_m` field added in Task 2, or after `centering_settle_cycles` if Task 3 lands independently)
- Modify: `mvp_mission_bebop/mvp_mission_bebop/controllers/rtl_guidance.py` (`ArucoCenteringController.__init__`, `.reset()`, `.update()`, `._coast()`)
- Test: `mvp_mission_bebop/test/test_aruco_rtl.py` (new tests in "losing sight of the pad" section, after `test_a_lost_marker_brakes_rather_than_leaving_the_last_command_latched`)

**Interfaces:**
- Consumes: `self.rtl_cfg.max_accel_mps2`/`max_jerk_mps3` (existing, via the already-constructed `self._longitudinal_profile`).
- Consumes: `self._settle_speed_mps` (existing, computed in `__init__`).
- Produces: no new public methods; `_coast()` keeps its `(self, dt: float) -> CenteringCommand` signature.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_aruco_rtl.py`, after `test_a_lost_marker_brakes_rather_than_leaving_the_last_command_latched` (before `test_the_law_has_no_rotational_output_at_all`):

```python
def test_a_loss_mid_approach_creeps_aft_to_reframe_the_marker():
    """The flight failure: overshoot loses the tag, then the drone just hovers.

    The narrow rear field of view at a steep camera_tilt_deg means a marker
    lost while the airframe was still closing in -- not already parked over it
    -- is very often a marker that has just slipped out of frame ahead or
    below, exactly the direction a small aft creep re-frames.
    """
    controller = law()
    for _ in range(20):
        moving = controller.update(sighting(0.20, 0.0), DT)
    assert moving.commanded_speed_mps > controller.rtl_cfg.settle_max_speed_mps, (
        "the approach never built up real speed; the test does not exercise the case"
    )

    creep = controller.update(None, DT)
    assert creep.vx < 0.0, "the recovery must actually creep aft"
    assert creep.vx >= -abs(
        controller.calibration.to_normalized(
            controller.rtl_cfg.reacquire_creep_speed
        )
    ) - 1e-6


def test_the_creep_is_bounded_in_time_and_then_holds():
    controller = law()
    for _ in range(20):
        controller.update(sighting(0.20, 0.0), DT)

    cfg = MissionParameters().rtl
    elapsed = 0.0
    creeping_seen = False
    while elapsed < cfg.reacquire_creep_sec + 1.0:
        command = controller.update(None, DT)
        if command.vx < 0.0:
            creeping_seen = True
        elapsed += DT

    assert creeping_seen, "the creep never engaged at all"
    assert command.vx == pytest.approx(0.0, abs=1e-6), (
        "the creep must end and the airframe come to rest, not creep indefinitely"
    )


def test_a_loss_while_already_parked_over_the_marker_does_not_creep():
    """Creeping off a good position to chase a marker occluded at rest is the
    opposite of the fix: the airframe was not overshooting anything, so there
    is nothing for a directional creep to correct.
    """
    controller = law()
    for _ in range(5):
        controller.update(sighting(0.0, 0.0), DT)

    for _ in range(10):
        command = controller.update(None, DT)
        assert command.vx == 0.0, "an at-rest loss must hold station, not creep"


def test_a_loss_from_far_out_does_not_creep():
    """Creep is a close-in remedy; a marker lost well outside landing range is
    not the overshoot case this recovery exists for.
    """
    controller = law()
    for _ in range(60):
        controller.update(sighting(2.0, 1.5), DT)

    command = controller.update(None, DT)
    assert command.vx <= 0.0
    # Far-range loss must decay toward zero under the same profile as before,
    # not toward -reacquire_creep_speed; bounding just confirms it is not
    # railed at the (much larger) creep authority.
    assert command.vx >= -abs(
        controller.calibration.to_normalized(controller.rtl_cfg.reacquire_creep_speed)
    ) - 1e-6
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python3 -m pytest test/test_aruco_rtl.py -k "creep" -v
```

Expected: `test_a_loss_mid_approach_creeps_aft_to_reframe_the_marker` and `test_the_creep_is_bounded_in_time_and_then_holds` FAIL (either `AttributeError` on `reacquire_creep_speed`/`reacquire_creep_sec` not existing on `ReturnToLaunchConfig`, or `creep.vx` being `0.0` since `_coast()` does not creep yet). The two "does not creep" tests should PASS against the unmodified code (it never creeps at all yet) -- they exist to stay green once creep is added, guarding the two failure modes in Review Focus.

- [ ] **Step 3: Add the creep parameters to `ReturnToLaunchConfig`**

In `mvp_mission_bebop/mvp_mission_bebop/parameters.py`, after the `landing_radius_m` field (or after `centering_settle_cycles` if applied independently of Task 2), insert:

```python
    #: Bounded reverse creep commanded while the marker is lost, normalized.
    #:
    #: The camera_tilt_deg rear field of view is narrow by construction (see
    #: that field's docstring), so a marker lost while the airframe was still
    #: actively closing on it -- not already at rest over it -- is very often a
    #: marker that has just slipped out of frame ahead or below. Holding
    #: station and waiting out lost_frames_tolerance treats that the same as
    #: any other dropout; creeping aft treats it as what it almost always is.
    #: Bounded well below reverse_cruise_velocity: this is a re-framing nudge,
    #: not a second search leg.
    reacquire_creep_speed: float = 0.03
    #: Duration the aft creep runs before yielding to a plain hold, in seconds.
    reacquire_creep_sec: float = 1.0
    #: Radial error, at the last valid sighting, below which a subsequent loss
    #: is treated as a close-in overshoot rather than a general dropout. Losses
    #: from well outside this range are not the narrow-FOV failure the creep
    #: exists for, and creeping there would be aimless.
    reacquire_creep_range_m: float = 0.30
```

- [ ] **Step 4: Track the pre-loss state and extend `_coast()`**

In `mvp_mission_bebop/mvp_mission_bebop/controllers/rtl_guidance.py`, `ArucoCenteringController.__init__`, after the existing state block (`self._settle_cycles`, `self._lost_frames`, `self._elapsed` -- lines 872-874), add:

```python
        # State the creep decision reads: the last valid sighting's range and
        # the speed the law was commanding when it was lost. Both None until
        # the first observation, so the very first cycle can never creep on a
        # loss it has no prior evidence about.
        self._last_radial_m: Optional[float] = None
        self._last_speed_mps: Optional[float] = None
        self._lost_elapsed_sec: float = 0.0
        self._creep_speed_mps: float = abs(
            self.calibration.to_mps(rtl_cfg.reacquire_creep_speed)
        )
```

In `reset()`, alongside the existing resets, add:

```python
        self._last_radial_m = None
        self._last_speed_mps = None
        self._lost_elapsed_sec = 0.0
```

In `update()`, right before the `return CenteringCommand(...)` at the end of the method (where `radial` and `speed_mps` are both in scope), record the state a future loss will read:

```python
        self._last_radial_m = radial
        self._last_speed_mps = speed_mps
        self._lost_elapsed_sec = 0.0

        return CenteringCommand(
```

Now replace `_coast()` (lines 1029-1074):

```python
    def _coast(self, dt: float) -> CenteringCommand:
        """Hold station through a cycle that carried no usable observation.

        The airframe is brought to rest rather than left on its last command,
        because the Bebop latches the last Twist it received indefinitely: doing
        nothing here is not "hold position", it is "keep flying the correction
        computed for a marker position nobody can currently see".
        """
        self._lost_frames += 1
        longitudinal_mps = self._longitudinal_profile.step(0.0, dt)
        lateral_mps = self._lateral_profile.step(0.0, dt)

        tolerated = self._lost_frames <= self._lost_tolerance
        if not tolerated:
            # Past the tolerance the PD state describes a world the camera is no
            # longer confirming. Clearing it stops a stale derivative from firing
            # into the first frame that comes back.
            self._settle_cycles = 0
            self._longitudinal.reset()
            self._lateral.reset()
            note = f"marker lost for {self._lost_frames} frames: holding station"
        else:
            note = f"marker absent ({self._lost_frames}/{self._lost_tolerance}): coasting"

        vx = self._shaper_x.shape(self.calibration.to_normalized(longitudinal_mps), dt)
        vy = self._shaper_y.shape(self.calibration.to_normalized(lateral_mps), dt)
```

with:

```python
    def _coast(self, dt: float) -> CenteringCommand:
        """Hold station -- or, close-in and mid-approach, creep aft -- through a
        cycle that carried no usable observation.

        A marker lost while the airframe was still actively closing on it, from
        inside reacquire_creep_range_m, is very often a marker that has just
        slipped out of the narrow rear field of view a steep camera_tilt_deg
        produces: the classic overshoot-loses-the-tag failure. A brief, bounded
        aft creep re-frames it directly rather than waiting out
        lost_frames_tolerance on the chance the airframe drifts back into view
        on its own. A marker lost while the airframe was already at rest and
        centred is a different event entirely -- an occlusion, not an
        overshoot -- and creeping off a good position would be exactly wrong,
        so the creep is gated on the speed the law was commanding at the last
        sighting, not merely on range.

        Otherwise the airframe is brought to rest rather than left on its last
        command, because the Bebop latches the last Twist it received
        indefinitely: doing nothing here is not "hold position", it is "keep
        flying the correction computed for a marker position nobody can
        currently see".
        """
        self._lost_frames += 1
        self._lost_elapsed_sec += max(0.0, dt)

        tolerated = self._lost_frames <= self._lost_tolerance
        creeping = (
            tolerated
            and self._last_radial_m is not None
            and self._last_speed_mps is not None
            and self._last_radial_m <= self.rtl_cfg.reacquire_creep_range_m
            and self._last_speed_mps > self._settle_speed_mps
            and self._lost_elapsed_sec <= self.rtl_cfg.reacquire_creep_sec
        )
        longitudinal_target = -self._creep_speed_mps if creeping else 0.0
        longitudinal_mps = self._longitudinal_profile.step(longitudinal_target, dt)
        lateral_mps = self._lateral_profile.step(0.0, dt)

        if not tolerated:
            # Past the tolerance the PD state describes a world the camera is no
            # longer confirming. Clearing it stops a stale derivative from firing
            # into the first frame that comes back.
            self._settle_cycles = 0
            self._longitudinal.reset()
            self._lateral.reset()
            note = f"marker lost for {self._lost_frames} frames: holding station"
        elif creeping:
            note = (
                f"marker lost {self._lost_frames} frames: creeping aft to reframe "
                f"({self._lost_elapsed_sec:.2f}/{self.rtl_cfg.reacquire_creep_sec:.2f} s)"
            )
        else:
            note = f"marker absent ({self._lost_frames}/{self._lost_tolerance}): coasting"

        vx = self._shaper_x.shape(self.calibration.to_normalized(longitudinal_mps), dt)
        vy = self._shaper_y.shape(self.calibration.to_normalized(lateral_mps), dt)
```

The remainder of `_coast()` (the `return CenteringCommand(...)` block) is unchanged.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
python3 -m pytest test/test_aruco_rtl.py -k "creep" -v
```

Expected: all four PASS.

- [ ] **Step 6: Run the full RTL/safety suites to confirm no regression**

```bash
python3 -m pytest test/test_aruco_rtl.py test/test_rtl_guidance.py test/test_safety_invariants.py test/test_recovery_and_touchdown.py -v
```

Expected: all PASS. `test_a_dropped_frame_inside_the_tolerance_does_not_restart_the_settlement` and `test_a_sustained_loss_clears_the_settlement_and_holds_station` are the two most likely to interact with this change (both lose the marker from a `sighting(0.0, 0.0)` at-rest state); they must keep passing exactly because that scenario has `_last_speed_mps` near zero and therefore never creeps, per the gating in Step 4.

---

### Task 4: Config round-trip coverage for the new fields

**Files:**
- Modify: `mvp_mission_bebop/test/test_aruco_rtl.py:1233-1270` (`test_the_aruco_block_round_trips_through_mission_config`, `test_a_config_written_before_the_aruco_fields_existed_still_loads`)

**Interfaces:**
- Consumes: `landing_radius_m`, `reacquire_creep_speed`, `reacquire_creep_sec`, `reacquire_creep_range_m`, `centering_settle_cycles` from Tasks 2 and 3.
- Produces: nothing new; this task only extends existing test coverage.

- [ ] **Step 1: Write the failing test extension**

In `test/test_aruco_rtl.py`, extend `test_the_aruco_block_round_trips_through_mission_config` (currently lines 1233-1258) to also cover the new fields:

```python
def test_the_aruco_block_round_trips_through_mission_config():
    """The GCS reads and rewrites this document; new fields must survive it."""
    import json
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "mission_config.json")
        params = MissionParameters()
        params.rtl.target_aruco_id = 21
        params.rtl.marker_dict = 6
        params.rtl.tag_size = 0.35
        params.rtl.camera_tilt_deg = -85.0
        params.rtl.centering_kp_y = 0.44
        params.rtl.landing_radius_m = 0.15
        params.rtl.reacquire_creep_speed = 0.04
        params.rtl.reacquire_creep_sec = 1.5
        params.rtl.reacquire_creep_range_m = 0.40
        params.save_to_file(path)

        with open(path, "r", encoding="utf-8") as stream:
            on_disk = json.load(stream)
        assert on_disk["rtl"]["target_aruco_id"] == 21
        assert on_disk["rtl"]["tag_size"] == 0.35
        assert on_disk["rtl"]["landing_radius_m"] == 0.15

        reloaded = MissionParameters.load_from_file(path)
        assert reloaded.rtl.target_aruco_id == 21
        assert reloaded.rtl.marker_dict == 6
        assert reloaded.rtl.camera_tilt_deg == -85.0
        assert reloaded.rtl.centering_kp_y == 0.44
        assert reloaded.rtl.landing_radius_m == 0.15
        assert reloaded.rtl.reacquire_creep_speed == 0.04
        assert reloaded.rtl.reacquire_creep_sec == 1.5
        assert reloaded.rtl.reacquire_creep_range_m == 0.40
```

And extend `test_a_config_written_before_the_aruco_fields_existed_still_loads` (currently lines 1261-1270) with an explicit assertion that the new fields fall back to their defaults from an old-shaped document:

```python
def test_a_config_written_before_the_aruco_fields_existed_still_loads():
    """Backward compatibility is not decoration: a field station's config file
    predates every deployment, and an unknown-key failure there is a mission
    that does not launch."""
    params = MissionParameters()
    params.update_from_dict({"rtl": {"arrival_radius_m": 0.18, "max_speed": 0.07}})

    assert params.rtl.arrival_radius_m == 0.18
    assert params.rtl.target_aruco_id == MissionParameters().rtl.target_aruco_id
    assert params.rtl.tag_size == MissionParameters().rtl.tag_size
    assert params.rtl.landing_radius_m == MissionParameters().rtl.landing_radius_m
    assert params.rtl.reacquire_creep_speed == MissionParameters().rtl.reacquire_creep_speed
```

- [ ] **Step 2: Run to verify failure, then pass**

```bash
python3 -m pytest test/test_aruco_rtl.py -k "round_trips_through_mission_config or written_before_the_aruco_fields" -v
```

Expected: FAIL first if this task runs before Tasks 2/3 land (the fields do not exist yet). After Tasks 2 and 3's field additions: PASS.

- [ ] **Step 3: Commit note**

No commit is made automatically per the Global Constraints -- leave the working tree for the user to review and commit.

---

### Task 5: Lock in Stage 2/3's existing brief-occlusion recovery with a regression test

**Files:**
- Test: `mvp_mission_bebop/test/test_stage3_integration.py` (new test after `test_the_step_recovers_from_a_nadir_dropout_instead_of_ending`, around line 422)

**Interfaces:**
- Consumes: `Ctx`, `Scene`, `flight_params`, `inspection_standoff`, `simulated_time` fixture -- all already defined in `test_stage3_integration.py`.
- Produces: nothing; this task adds test coverage for existing, already-correct production behavior (`estimation/target_tracker.ConstantVelocityTracker` coasting through short dropouts, wired into `steps/tracking.py::VisualServoingStep._resolve_target`). No source changes.

This task exists because Point A of the brief ("a target lost for a few frames must not lose progress or fall back to a blind search") is already implemented -- `ConstantVelocityTracker` with `max_coast_sec=1.5` already extrapolates through short dropouts, and `test_the_step_recovers_from_a_nadir_dropout_instead_of_ending`'s own docstring says so explicitly ("an alpha-beta extrapolation covers short dropouts on its own, and that layer is working as intended"). No test currently asserts that *positively*; this task adds the one that does, so the property is pinned rather than merely believed.

- [ ] **Step 1: Write the test**

```python
def test_a_brief_occlusion_does_not_trigger_reacquisition_or_lose_progress(simulated_time):
    """The alpha-beta coast is supposed to absorb this on its own, silently.

    Ten frames at 16 Hz is well inside ConstantVelocityTracker's 1.5 s coast
    horizon -- a glint, the airframe's own shadow, or a single bad detection,
    not a real loss. The approach must finish exactly as if the dropout had
    never happened: no REACQUIRING phase, no discarded progress.
    """
    ctx = Ctx(flight_params(), Scene(ground_range_m=8.0))
    ctx.blackout = range(60, 70)

    status = VisualServoingStep().execute(ctx)

    standoff = inspection_standoff(ctx.params)
    assert status.name == "SUCCESS"
    assert ctx.blackboard.approach_finished
    assert not any("REACQUIRING" in line for line in ctx.overlays), (
        "a ten-frame dropout escalated to active reacquisition; the coast layer "
        "did not absorb it"
    )
    assert ctx.scene.ground_range_m <= standoff + 0.10
```

Insert this immediately after `test_the_step_recovers_from_a_nadir_dropout_instead_of_ending` and before `test_a_permanent_loss_still_terminates`.

- [ ] **Step 2: Run to verify it passes against the current, unmodified code**

```bash
python3 -m pytest test/test_stage3_integration.py -k "brief_occlusion" -v
```

Expected: PASS immediately -- this is a regression lock, not a bug fix. If it fails, that is a real defect distinct from this plan's three points and must be investigated before proceeding (do not weaken the assertion to force a pass).

- [ ] **Step 3: Run the full Stage 2/3 suites to confirm no regression**

```bash
python3 -m pytest test/test_stage3_integration.py test/test_centering_recovery.py test/test_stage3_parameter_authority.py -v
```

Expected: all PASS.

---

### Task 6: Full-suite verification and manual parameter sanity check

**Files:** none modified; verification only.

- [ ] **Step 1: Run the complete test suite**

```bash
source /home/jv/ros2_ws/bin/nectar-activate
cd /home/jv/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/ -v
```

Expected: 100% pass, zero skips beyond the pre-existing hardware/SDK-conditional skips in `test_aruco_rtl.py`'s "against the real SDK and the real OpenCV" section (those are environment-dependent and unrelated to this change).

- [ ] **Step 2: Build the ROS 2 package to confirm nothing else imports the changed symbols incorrectly**

```bash
cd /home/jv/ros2_ws
colcon build --symlink-install --packages-select mvp_mission_bebop
```

Expected: build succeeds with no new warnings from `mvp_mission_bebop`.

- [ ] **Step 3: Manually sanity-check the new defaults against the physical scenario in the brief**

Confirm, by inspection of `ReturnToLaunchConfig` after the edits:
- `landing_radius_m = 0.13` sits inside the user's requested 0.12-0.15 m band and is comfortably inside a 0.20 m tag on a 0.50-1.0 m pad.
- `centering_settle_cycles = 2` still requires at least two consecutive qualifying cycles (never authorizes on a single sample crossing the gate at speed).
- `reacquire_creep_speed = 0.03` is well below `reverse_cruise_velocity = -0.10` and below `max_centering_speed = 0.08` -- a nudge, not a second search leg.
- `max_accel_mps2 = 0.25` (unchanged) is confirmed by Task 1's tests to bound the longitudinal channel's commanded speed to what it can arrest before crossing the marker at every radial distance, not merely at the far field.

No commit is made per the Global Constraints; leave the verified, passing working tree for the user.
