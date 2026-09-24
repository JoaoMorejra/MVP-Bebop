# Altitude, Yaw and Hover Hardening — Design Spec

Status: approved (conversational design, 2026-09-24) — pending written-spec review.
Scope: `mvp_mission_bebop` package only. No `nectar-sdk` or `ros2_bebop_driver`
changes (both are out of tree per the Golden Rule; behaviour is worked around,
never patched upstream from this repo).

## 0. Method

Every claim below is labelled:

- **[FATO]** — read directly from the source cited.
- **[SUPOSIÇÃO]** — inferred from behaviour or documented firmware
  characteristics, not directly observed in this codebase.
- **[A CONFIRMAR]** — requires a field test to validate; the design proceeds
  on the stated assumption but flags where it could be wrong.

Golden Rule compliance: `nectar-sdk` (`BebopDrone`, `PIDController`) and the
C++ driver (`bebop.cpp`, `bebop_driver_node.cpp`) were inspected before this
design was drafted. Neither provides altitude source selection, an EKF, or
`do_hover` awareness — every fix below is implemented in `mvp_mission_bebop`,
against the driver's documented contract.

## 1. Ponto 1 — Precisão da subida / eliminação de espera cega

**Status: já resolvido.** No action proposed.

**[FATO]** `steps/takeoff.py::_stabilize` no longer runs an unconditional
`takeoff_stabilize_duration_sec` hover. It now runs a `SettlementDetector`
over the vertical axis (altitude in the `x` slot, `y` held at zero, speed =
`|vz|`) and returns as soon as `FlightKinematicsConfig.takeoff_settle_*`
converges, with the old duration retained only as a ceiling
(`parameters.py:474-494`, `steps/takeoff.py:168-247`). Samples below
`takeoff_settle_min_altitude_m` reset the window so a still-grounded reading
right after `takeoff()` returns cannot masquerade as a settled hover.

This already delivers "transição dinâmica ao setpoint sem espera cega, sem
degrau abrupto": the gate is statistical convergence, not a timer, and the
jerk-limited ascent profile it settles into predates this change. No further
work is scoped under this Ponto.

## 2. Ponto 2 — Rejeição de falso-clímax do sonar sobre obstáculos

**Diagnosis.**

**[FATO]** The Bebop firmware holds altitude from a downward ultrasonic
rangefinder. `/bebop/odom.z` is dead-reckoned in `bebop_driver_node.cpp:377`
by integrating `beb_vz_enu` (`ARDrone3PilotingStateSpeedChanged`), not a raw
range. A separate, currently unused topic `/bebop/states/altitude`
(`ARDrone3PilotingStateAltitudeChanged`, 2 Hz, `Float32`) exists in the driver
(`bebop_driver_node.cpp:171-176,273-279`) but nothing in `mvp_mission_bebop`
subscribes to it.

**[SUPOSIÇÃO]** The rangefinder-over-obstacle failure mode (bench, parked
vehicle, person underneath the flight path shortens the measured range, the
firmware reads that as lost height, and commands a climb) is a firmware-level
behaviour of the ARSDK3 altitude estimator. It cannot be fixed from the ROS
side — there is no raw range topic to filter, and `AltitudeChanged` is
plausibly the same fused estimate `SpeedChanged` integrates from, not an
independent cross-check. **[A CONFIRMAR]**: whether `states/altitude` and the
integrated `odom.z` diverge usefully during an overflight event is unknown
without a field trial (fly over a bench and log both topics side by side).

**Proposed fix — plausibility/slew-rate filter (primary, high confidence).**

Rather than depend on an unconfirmed independent sensor, gate the altitude
signal the mission already trusts (`odom.z`, via `OdometrySupervisor`) against
a physically bounded rate of change before it reaches the governor. A true
climb or sink is bounded by the airframe's own dynamics
(`AltitudeGovernorConfig.max_accel_mps2` / `max_jerk_mps3`, already
~0.40 m/s^2 / 2.00 m/s^3); an obstacle-induced range-shortening event is a
near-instantaneous jump the true trajectory cannot produce.

Model: maintain an expected-altitude envelope
`[a_prev - v_max*dt - 0.5*a_max*dt^2, a_prev + v_max*dt + 0.5*a_max*dt^2]`
each cycle. A sample outside the envelope is flagged implausible and
substituted with the last trusted value (held, not extrapolated further,
since a held reading is what the governor already does for a
non-finite/missing sample per `altitude_hold.py:178-185`). A short run of
consecutive implausible samples (configurable) is required before *accepting*
a new regime, so a genuine step (e.g., the drone actually landing) is not
permanently rejected — only a single-sample jump.

New module: `estimation/altitude_plausibility.py`, mirroring the shape of
`estimation/detection_filter.py::HysteresisConfirmer` (accept-after-N,
reject-after-M) rather than a Kalman filter — consistent with the project's
existing preference for simple, testable, explicit-state filters over
statistical estimators that would need tuning against a firmware whose noise
model is undocumented.

**Secondary, optional: subscribe to `/bebop/states/altitude`.** If a field
trial confirms it moves independently of `odom.z` during an overflight event,
add it as a second, lower-rate input to the same plausibility gate (an
implausible jump in *both* signals simultaneously is more likely real; a jump
in one alone is more likely noise). This is additive and does not block the
primary fix — flagged **[A CONFIRMAR]** and explicitly optional in the
implementation plan.

## 3. Ponto 3 — Congelamento posicional total durante a inspeção nadir

**Diagnosis.**

**[FATO]** `bebop.cpp:196-214` (`Bebop::move`): the firmware's autonomous
`do_hover` mode engages only when roll, pitch, yaw_speed, **and** gaz_speed
are all within 0.001 of zero; otherwise raw PCMD is sent on all four axes.
`bebop_driver_node.cpp:415-421` (`cmdVelCallback`) maps `Twist.linear.z`
directly to `gaz_speed` with no deadband. Therefore any nonzero `vz` from the
altitude governor — which is exactly what is active while Stage 4's hover
sinks off the target height — structurally prevents `do_hover` from ever
engaging during inspection.

**[FATO]** `steps/inspection.py::_await_stillness` gates on
`SettlementCriteria(max_speed=settle_max_speed_mps,
max_position_sigma=settle_max_position_sigma_m)` fed with `snapshot.x`,
`snapshot.y`, `snapshot.speed` — horizontal position and horizontal speed
only (`inspection.py:114-124`). The vertical axis is not part of the
convergence gate at all: the hover can be declared "settled" while the
altitude governor is still actively correcting a sink, i.e. while `vz != 0`
is still being commanded — which is precisely the condition that keeps
`do_hover` from engaging and the airframe translating (however slightly)
under active vertical correction during the capture.

**Proposed fix.**

Extend `estimation/convergence.py::SettlementCriteria` with an optional
`max_vertical_speed: Optional[float] = None` field (mirrors the existing
`max_distance: Optional[float]` optional-field pattern already in the class).
When set, `SettlementDetector.update` takes an additional `vz` sample and the
settled condition also requires `|vz| <= max_vertical_speed`— sourced from
`ctx.governor`'s last commanded correction (already computed every cycle in
`_hold`), not a second odometry read, since the vertical speed that matters
for `do_hover` eligibility is the *commanded* one, not the estimated one.

`steps/inspection.py::_await_stillness` then constructs the criteria with
`max_vertical_speed=cfg.settle_max_vertical_speed_mps` (new
`InspectionConfig` field, same order of magnitude as
`settle_max_speed_mps`), so the hover is not declared settled while the
governor is still actively pinning the altitude.

This closes the loop between "statistically still" and "the firmware's own
hover mode is actually active": once both the horizontal and vertical
convergence criteria hold simultaneously, the commanded `vz` from
`_hold`/`compute_vz` is at (or within noise of) zero, which is the condition
`do_hover` requires. No change to `_hold`'s command path is needed — the fix
is entirely in what the settlement gate is allowed to call "settled".

## 4. Ponto 4 — Perda de altitude durante translação horizontal

**Status: majoritariamente já resolvido**, via feedback rather than
feedforward. Scoped addition: a bounded feedforward trim.

**[FATO]** `controllers/altitude_hold.py::AltitudeHoldGovernor` already
replaced the one-sided anti-climb governor with a two-sided PI + jerk-limited
regulator (`parameters.py::AltitudeGovernorConfig`, `ki=0.25`,
`integral_leak_sec=4.0`, asymmetric deadbands). The integral term is the
mechanism that rejects the *sustained* disturbance translation creates
(proportional action alone would leave a standing low-altitude offset, which
is the exact failure the docstring at `altitude_hold.py:12-20` documents from
a prior flight). `horizontal_scale()` additionally throttles the commanded
cruise speed itself when the altitude error grows, and every translating step
(`steps/search.py::_cruise`, presumably `steps/tracking.py`,
`steps/rtl.py`) already reads it before profiling its own demand
(`search.py:242-244`).

**Remaining gap.** The PI response is reactive: it only accumulates authority
once the altitude has already sagged. A literal feedforward on commanded
pitch angle is not implementable — the mission commands normalized velocity,
not attitude, and the firmware's own attitude controller/wind response sits
between the two in a way this repo cannot model (**[SUPOSIÇÃO]**: no pitch
telemetry is exposed to close that loop; `ctx.current_tilt_deg` is the
*gimbal* tilt, unrelated to airframe pitch).

**Proposed fix — empirical velocity-based feedforward, additive to the PI
term.** Add a static, bounded feedforward term to `compute_vz`:
`vz_ff = k_ff * vx_commanded`, where `vx_commanded` is the horizontal speed
demand already being profiled this cycle (passed in by the caller, since the
governor does not otherwise see it) and `k_ff` is a single empirical gain
(**[A CONFIRMAR]** in a field trial: command a known cruise speed in still
hover-hold-disabled flight, measure the steady-state sink rate, solve for
`k_ff`). This anticipates the sink at the instant translation begins rather
than waiting for the integral to accumulate against it, while the existing PI
term remains the safety net for whatever the feedforward gets wrong. `k_ff`
defaults to `0.0` (feedforward inert) until the field measurement exists,
so this ships as a no-op until tuned — consistent with the project's pattern
of new tunables landing at a value that reproduces current behaviour
(compare `takeoff_settle_*` ceiling behaviour in Ponto 1).

## 5. Ponto 5 — Proibição absoluta de rotação em yaw

**Status: já robusto.** Proposed: one structural hardening, defense in depth.

**[FATO]** Every guidance law was audited: `controllers/rtl_guidance.py`
(`RTLGuidanceController` and `ArucoCenteringController`) has no yaw output in
its return type at all; `steps/search.py::_command` and
`steps/inspection.py::_hold` both call
`ctx.failsafe.clamp_kinematics(vz, 0.0)` — the second argument, `vyaw`, is a
literal `0.0` at every call site found. No command-emission point in
`mvp_mission_bebop` computes a nonzero yaw rate.

**Proposed hardening.** Add a hard assertion/clamp at the single lowest
point all velocity commands pass through before reaching the SDK —
`BenchtopDroneProxy.move_velocity` (actuators layer) — that clamps `vyaw` to
`0.0` unconditionally regardless of what is passed in, and logs at `ERROR` if
a caller ever passes a nonzero value. This is pure defense-in-depth: no
currently-reachable code path is known to violate the invariant, so the
change is not fixing a bug, it is making the invariant impossible to
regress silently in future work on any of the five steps.

## 6. Ponto 6 — Revisão holística das 5 etapas

**[FATO]** Full read of `steps/takeoff.py`, `steps/search.py`,
`steps/tracking.py` (IBVS approach), `steps/inspection.py`, and `steps/rtl.py`
in this session and the session before compaction. Findings:

- Stage 5 (RTL) is materially more mature than the original briefing assumed:
  closed-loop ArUco centering is already the primary path
  (`ClosedLoopRTLStep`), with a structurally-enforced `vx<=0` reverse search
  and odometric guidance retained only as a degraded fallback.
- The one blocking-call latency defect previously identified
  (`ImageHandler.take_photo` always returning a cached frame due to missing
  `ROSCam` attributes, making the video-loss failsafe unreachable) is already
  fixed in `context.py::grab_frame`.
- `steps/search.py` and `steps/inspection.py` both already run inside
  `ctx.failsafe.altitude_hold_window`, confirming Ponto 2's plausibility
  filter and Ponto 3's settlement extension are the only vertical-axis gaps
  left to close in the stages that matter most (cruise and hover).
- No further blind waits or dead-time defects were found beyond what Ponto 1
  already resolved (takeoff) and what Ponto 3 addresses (inspection
  settlement). This Ponto closes with no additional file-touch beyond the
  ones already listed under Pontos 2-5.

## Summary of files to touch

| File | Ponto | Nature of change |
|---|---|---|
| `estimation/altitude_plausibility.py` (new) | 2 | New hysteretic plausibility/slew-rate filter |
| `telemetry/odometry.py` | 2 (optional) | Optional second input: subscribe to `/bebop/states/altitude` |
| `estimation/convergence.py` | 3 | Add optional `max_vertical_speed` to `SettlementCriteria`/`SettlementDetector` |
| `steps/inspection.py` (`_await_stillness`) | 3 | Feed governor's commanded `vz` into the extended settlement gate |
| `parameters.py` (`InspectionConfig`) | 3 | New `settle_max_vertical_speed_mps` field |
| `controllers/altitude_hold.py` (`compute_vz`) | 4 | Additive `k_ff * vx_commanded` feedforward term |
| `parameters.py` (`AltitudeGovernorConfig`) | 4 | New `feedforward_gain` field, default `0.0` |
| `actuators/proxy.py` (`move_velocity`) | 5 | Hard `vyaw=0.0` clamp + `ERROR` log on violation |

No changes are scoped for Ponto 1 (already resolved) or Ponto 6 beyond the
above (no independent defect found).

## Out of scope

- Any change inside `nectar-sdk` or `ros2_bebop_driver` (Golden Rule: those
  are read, never patched, from this repo).
- A literal attitude-based feedforward for Ponto 4 (infeasible — no pitch
  telemetry is exposed to this package).
- Kalman/EKF-based altitude fusion for Ponto 2 (no independent raw sensor
  input exists to fuse against; the plausibility filter is the fix that
  matches what is actually observable).

## Field validation required before closing this work

- **[A CONFIRMAR]** Ponto 2: log `/bebop/states/altitude` alongside `odom.z`
  during a deliberate low overflight of an obstacle, to decide whether the
  optional secondary input is worth adding.
- **[A CONFIRMAR]** Ponto 4: measure steady-state sink rate at the mission's
  cruise speed with hold enabled, to fit `feedforward_gain`.
- **[A CONFIRMAR]** Ponto 3: confirm in flight that the extended settlement
  gate correlates with an actual `do_hover` engagement (no telemetry exposes
  `do_hover`'s internal state directly — the correlation is inferred from the
  documented firmware condition in `bebop.cpp`, not observed).
