"""The platform edge guard: more vertical authority where the sonar is about to lie.

The guard is applied without touching any step, through the three places every
vertical command already passes: the altitude governor, the failsafe clamp and
the odometric ceiling. Outside the guard window, or with no pad edge configured,
all three behave exactly as before.

The closed-loop cases at the bottom drive a deliberately simple plant: an
integrator in altitude, plus the firmware response modelled in section 1.5 of
the specification (outcome M1) -- a 1.0 m sink at 0.3 m/s when the pad leaves
the beam, and a 1.0 m false climb when it returns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pytest

from mvp_mission_bebop.controllers.altitude_hold import AltitudeHoldGovernor
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.estimation.platform_monitor import PlatformReport, PlatformState
from mvp_mission_bebop.parameters import AltitudeGovernorConfig, MissionParameters, PlatformConfig
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

DT = 1.0 / 15.0
TARGET = 0.8


def report(state: PlatformState, seconds: Optional[float] = 0.0) -> PlatformReport:
    return PlatformReport(
        state=state,
        guard_active=state in (PlatformState.LEAVING, PlatformState.ENTERING),
        along_track_m=1.0,
        edge_step_m=1.29,
        direction=1 if state is PlatformState.LEAVING else -1,
        seconds_since_transition=seconds,
    )


@dataclass
class GuardSource:
    current: PlatformReport = field(default_factory=lambda: report(PlatformState.ON))
    raises: bool = False

    def __call__(self) -> PlatformReport:
        if self.raises:
            raise RuntimeError("guard source fault")
        return self.current


def pad_config(**overrides) -> PlatformConfig:
    cfg = PlatformConfig(height_m=1.0, edge_ahead_m=0.25)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def governor(source=None, platform=None, calibration=None, config=None) -> AltitudeHoldGovernor:
    return AltitudeHoldGovernor(
        TARGET,
        config or AltitudeGovernorConfig(),
        guard_source=source,
        platform_cfg=platform,
        calibration=calibration,
    )


def altitude_profile() -> List[float]:
    """Low, then high, then on target: every branch of the law."""
    return [0.55] * 40 + [0.95] * 40 + [0.80] * 30 + [0.70] * 30


def series(gov: AltitudeHoldGovernor, altitudes: List[float]) -> List[float]:
    return [gov.compute_vz(a, DT) for a in altitudes]


# ------------------------------------------------------------- inert by default


def test_without_a_guard_source_the_governor_is_unchanged():
    reference = AltitudeHoldGovernor(TARGET, AltitudeGovernorConfig())
    assert series(governor(), altitude_profile()) == series(reference, altitude_profile())


def test_without_an_edge_distance_the_guard_is_inert_even_if_reported_active():
    reference = AltitudeHoldGovernor(TARGET, AltitudeGovernorConfig())
    source = GuardSource(report(PlatformState.LEAVING))
    guarded = governor(source, pad_config(edge_ahead_m=None))
    assert series(guarded, altitude_profile()) == series(reference, altitude_profile())


def test_outside_the_window_the_guarded_governor_is_unchanged():
    reference = AltitudeHoldGovernor(TARGET, AltitudeGovernorConfig())
    guarded = governor(GuardSource(report(PlatformState.OFF)), pad_config())
    assert series(guarded, altitude_profile()) == series(reference, altitude_profile())


# ------------------------------------------------------------- inside the guard


def test_inside_the_guard_limits_gain_and_deadband_are_swapped_and_restored():
    cfg = pad_config()
    base = AltitudeGovernorConfig()
    source = GuardSource(report(PlatformState.LEAVING))
    gov = governor(source, cfg)

    gov.compute_vz(0.5, DT)
    assert gov.guard_engaged is True
    assert gov.gains.kp == pytest.approx(base.kp * cfg.guard_kp_scale)
    assert gov.gains.output_limits == pytest.approx((-cfg.guard_descent_cap, cfg.guard_climb_authority))
    assert gov.deadbands == pytest.approx((cfg.guard_deadband_m, cfg.guard_deadband_m))

    source.current = report(PlatformState.OFF)
    gov.compute_vz(0.5, DT)
    assert gov.guard_engaged is False
    assert gov.gains.kp == pytest.approx(base.kp)
    assert gov.gains.output_limits == pytest.approx((-base.max_descent_speed, base.max_climb_speed))
    assert gov.deadbands == pytest.approx((base.deadband_m, base.climb_deadband_m))


def test_a_fault_inside_the_guard_restores_the_global_values(monkeypatch):
    base = AltitudeGovernorConfig()
    gov = governor(GuardSource(report(PlatformState.LEAVING)), pad_config())
    gov.compute_vz(0.5, DT)

    class BrokenProfile:
        def step(self, *_args, **_kwargs):
            raise RuntimeError("profile fault")

    monkeypatch.setattr(gov, "_profile", BrokenProfile())
    with pytest.raises(RuntimeError):
        gov.compute_vz(0.5, DT)

    assert gov.guard_engaged is False
    assert gov.gains.kp == pytest.approx(base.kp)
    assert gov.gains.output_limits == pytest.approx((-base.max_descent_speed, base.max_climb_speed))


def test_a_faulty_guard_source_reads_as_no_guard():
    reference = AltitudeHoldGovernor(TARGET, AltitudeGovernorConfig())
    guarded = governor(GuardSource(raises=True), pad_config())
    assert series(guarded, altitude_profile()) == series(reference, altitude_profile())


def test_the_guard_keeps_the_integrator_across_the_retune():
    gov = governor(GuardSource(report(PlatformState.OFF)), pad_config())
    for _ in range(30):
        gov.compute_vz(0.6, DT)
    banked = gov._pid.components["integral"]
    assert banked > 0.0

    gov._guard_source.current = report(PlatformState.LEAVING)
    gov.compute_vz(0.6, DT)
    assert gov._pid.components["integral"] >= banked


@pytest.mark.parametrize("altitude", [0.3, 1.3])
def test_activation_and_release_never_step_the_command(altitude):
    cfg = pad_config(guard_vertical_shaper=False)
    base = AltitudeGovernorConfig()
    source = GuardSource(report(PlatformState.OFF))
    gov = governor(source, cfg)
    commands = []
    for k in range(240):
        source.current = report(PlatformState.LEAVING if 60 <= k < 150 else PlatformState.OFF)
        commands.append(gov.compute_vz(altitude, DT))

    steps = [abs(b - a) for a, b in zip(commands, commands[1:])]
    assert max(steps) <= base.max_accel_mps2 * DT + 1e-9
    assert max(abs(c) for c in commands) > max(base.max_climb_speed, base.max_descent_speed)


def test_translation_stops_above_the_stop_error_and_resumes_below_the_resume_error():
    cfg = pad_config()
    gov = governor(GuardSource(report(PlatformState.LEAVING)), cfg)

    gov.compute_vz(TARGET - 0.10, DT)
    assert gov.horizontal_scale() > 0.0
    gov.compute_vz(TARGET - 0.16, DT)
    assert gov.horizontal_scale() == 0.0
    gov.compute_vz(TARGET - 0.10, DT)
    assert gov.horizontal_scale() == 0.0, "resumed above guard_resume_error_m"
    gov.compute_vz(TARGET - 0.07, DT)
    assert gov.horizontal_scale() > 0.0


def test_the_stop_latch_does_not_outlive_the_guard():
    gov = governor(GuardSource(report(PlatformState.LEAVING)), pad_config())
    gov.compute_vz(TARGET - 0.30, DT)
    assert gov.horizontal_scale() == 0.0
    gov._guard_source.current = report(PlatformState.OFF)
    gov.compute_vz(TARGET - 0.30, DT)
    assert gov.horizontal_scale() == pytest.approx(AltitudeGovernorConfig().min_horizontal_scale)


def test_the_vertical_shaper_turns_a_sub_quantum_demand_into_pulses():
    cfg = pad_config()
    config = AltitudeGovernorConfig(ki=0.0, kd=0.0)
    gov = governor(GuardSource(report(PlatformState.LEAVING)), cfg, config=config)
    kp = config.kp * cfg.guard_kp_scale
    altitude = TARGET - cfg.guard_deadband_m - 0.004 / kp

    commands = [gov.compute_vz(altitude, DT) for _ in range(300)][60:]

    pulses = [c for c in commands if c != 0.0]
    assert pulses, "a sub-quantum demand was transmitted as gaz == 0 throughout"
    assert all(c == pytest.approx(0.01, abs=1e-5) for c in pulses)
    assert sum(commands) / len(commands) == pytest.approx(0.004, abs=0.001)


def test_without_the_shaper_the_sub_quantum_demand_is_passed_through():
    cfg = pad_config(guard_vertical_shaper=False)
    config = AltitudeGovernorConfig(ki=0.0, kd=0.0)
    gov = governor(GuardSource(report(PlatformState.LEAVING)), cfg, config=config)
    kp = config.kp * cfg.guard_kp_scale
    altitude = TARGET - cfg.guard_deadband_m - 0.004 / kp
    commands = [gov.compute_vz(altitude, DT) for _ in range(120)]
    assert commands[-1] == pytest.approx(0.004, abs=1e-4)


@pytest.mark.parametrize(
    "state, field_name, sign",
    [(PlatformState.LEAVING, "sag_feedforward_mps", 1.0), (PlatformState.ENTERING, "rise_feedforward_mps", -1.0)],
)
def test_the_feedforward_runs_only_for_its_duration(state, field_name, sign):
    cfg = pad_config(guard_vertical_shaper=False, **{field_name: 0.10})
    calibration = SpeedCalibration(1.0, vertical_to_mps=2.5)
    plain = governor(GuardSource(report(state, 0.0)), pad_config(guard_vertical_shaper=False), calibration)
    fed = governor(GuardSource(report(state, 0.0)), cfg, calibration)

    early_plain = [plain.compute_vz(TARGET, DT) for _ in range(40)][-1]
    early_fed = [fed.compute_vz(TARGET, DT) for _ in range(40)][-1]
    assert early_fed - early_plain == pytest.approx(sign * 0.10 / 2.5, abs=1e-6)

    for source in (plain._guard_source, fed._guard_source):
        source.current = report(state, cfg.feedforward_sec + 0.1)
    late_plain = [plain.compute_vz(TARGET, DT) for _ in range(40)][-1]
    late_fed = [fed.compute_vz(TARGET, DT) for _ in range(40)][-1]
    assert late_fed == pytest.approx(late_plain, abs=1e-9)


def test_a_zero_feedforward_adds_nothing():
    cfg = pad_config(guard_vertical_shaper=False)
    a = governor(GuardSource(report(PlatformState.LEAVING)), cfg)
    b = governor(GuardSource(report(PlatformState.LEAVING)), pad_config(guard_vertical_shaper=False))
    assert series(a, altitude_profile()) == series(b, altitude_profile())


# ---------------------------------------------------------------- failsafe and ceiling


class _Drone:
    def __init__(self) -> None:
        self.commands: List[Tuple[float, float, float, float]] = []

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.commands.append((vx, vy, vz, vyaw))

    def land(self):
        return True


class _Odometry:
    def __init__(self, cfg: PlatformConfig, state: PlatformState) -> None:
        self.platform_cfg = cfg
        self.current = report(state)

    def platform_report(self) -> PlatformReport:
        return self.current


def test_no_guarded_command_reaches_the_wire_above_the_guard_authority():
    cfg = pad_config()
    params = MissionParameters()
    odom = _Odometry(cfg, PlatformState.LEAVING)
    drone = _Drone()
    failsafe = FailsafeSupervisor(drone, odom, params.timeouts, params.kinematics)
    gov = governor(odom.platform_report, cfg)

    with failsafe.altitude_hold_window(params.governor.climb_authority):
        for _ in range(200):
            vz, vyaw = failsafe.clamp_kinematics(gov.compute_vz(0.2, DT), 0.0)
            drone.move_velocity(vz=vz, vyaw=vyaw)

    sent = [c[2] for c in drone.commands]
    assert max(sent) <= cfg.guard_climb_authority + 1e-9
    assert max(sent) > params.governor.climb_authority
    assert all(c[3] == 0.0 for c in drone.commands)


def test_without_a_hold_window_the_guard_grants_no_climb():
    cfg = pad_config()
    params = MissionParameters()
    failsafe = FailsafeSupervisor(_Drone(), _Odometry(cfg, PlatformState.LEAVING), params.timeouts)
    assert failsafe.guard_hold_boost() == 0.0
    assert failsafe.clamp_kinematics(0.2, 0.0)[0] == 0.0


# ---------------------------------------------------------------- closed loop, outcome M1


VERTICAL_GAIN = 1.0
CRUISE = 0.05
STEP_M = 1.0
STEP_RATE = 0.3


@dataclass
class EdgeRun:
    min_altitude: float
    max_altitude: float
    breached: bool
    stopped_while_off_target: bool
    ceiling_at_peak: float


def fly_across_the_edge(guarded: bool, inbound: bool) -> EdgeRun:
    """Cross ``s*`` once at cruise; the firmware moves the airframe by ``STEP_M``."""
    params = MissionParameters()
    params.kinematics.target_altitude_m = 1.8
    params.kinematics.altitude_ceiling_margin_m = 0.6
    params.platform.height_m = 1.0
    params.platform.edge_ahead_m = 0.25 if guarded else None
    params.platform.ceiling_allowance_m = 0.4
    params.platform.venue_ceiling_agl_m = 3.0
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)
    for _ in range(params.calibration.min_ground_samples):
        odom.inject_synthetic_sample(x=0.0, y=0.0, z=0.0)
    assert odom.calibrate_ground_reference()
    odom.inject_synthetic_sample(x=0.0, y=0.0, z=TARGET)
    odom.freeze_hover_takeoff_origin()

    failsafe = FailsafeSupervisor(_Drone(), odom, params.timeouts, params.kinematics)
    gov = AltitudeHoldGovernor(
        params.relative_target_altitude_m,
        params.governor,
        guard_source=odom.platform_report,
        platform_cfg=params.platform,
    )
    s_star = 0.25 + TARGET * math.tan(math.radians(params.platform.sonar_half_angle_deg))
    y = s_star + 2.0 if inbound else 0.0
    heading = -1.0 if inbound else 1.0
    z = TARGET
    disturbance_left = 0.0
    crossed = False
    lowest, highest, peak_ceiling = z, z, odom.altitude_ceiling
    breached = False
    stopped = True

    if inbound:
        # Arrive from the far side so the monitor has seen the airframe off the pad.
        for k in range(40):
            odom.inject_synthetic_sample(x=0.0, y=y + 0.01 * (40 - k), z=z)

    with failsafe.altitude_hold_window(params.governor.climb_authority):
        for _ in range(int(60.0 / DT)):
            odom.inject_synthetic_sample(x=0.0, y=y, z=z)
            vz_cmd = gov.compute_vz(odom.snapshot().relative_altitude, DT)
            vz_cmd, _ = failsafe.clamp_kinematics(vz_cmd, 0.0)
            scale = gov.horizontal_scale()
            if abs(gov.altitude_error_m) > params.platform.guard_stop_error_m and guarded and scale != 0.0:
                stopped = False
            vy = CRUISE * scale * heading

            if not crossed and ((y >= s_star) if not inbound else (y <= s_star)):
                crossed = True
                disturbance_left = STEP_M
            firmware = 0.0
            if disturbance_left > 0.0:
                firmware = (STEP_RATE if inbound else -STEP_RATE)
                disturbance_left -= STEP_RATE * DT
            z += (vz_cmd * VERTICAL_GAIN + firmware) * DT
            y += vy * DT

            lowest = min(lowest, z)
            if z > highest:
                highest, peak_ceiling = z, odom.altitude_ceiling
            breached = breached or odom.is_ceiling_breached()
            if crossed and disturbance_left <= 0.0 and abs(z - TARGET) < 0.02 and abs(y - s_star) > 1.5:
                break
    return EdgeRun(lowest, highest, breached, stopped, peak_ceiling)


def test_the_guard_reduces_the_sink_on_leaving_the_pad():
    unguarded = fly_across_the_edge(guarded=False, inbound=False)
    guarded = fly_across_the_edge(guarded=True, inbound=False)

    assert TARGET - guarded.min_altitude < TARGET - unguarded.min_altitude
    assert guarded.stopped_while_off_target, "translated with more than 0.15 m of vertical error"


def test_the_false_climb_on_re_entry_is_bounded_by_the_fixed_pad_top_ceiling_without_the_guard():
    """Risk 1.5-M1: the unguarded re-entry still climbs well off target.

    It used to breach the 1.40 m ceiling of ``target + margin`` and land in the
    air over the pad edge. The pad-top ceiling is now fixed at
    ``max(target, native height) + 0.60`` (D2): the climb stays an altitude
    error the guard exists to remove, under an absolute envelope.
    """
    unguarded = fly_across_the_edge(guarded=False, inbound=True)
    assert unguarded.max_altitude > 1.4
    assert unguarded.max_altitude < unguarded.ceiling_at_peak == pytest.approx(1.6)


def test_the_guard_keeps_the_false_climb_under_the_effective_ceiling():
    guarded = fly_across_the_edge(guarded=True, inbound=True)
    assert guarded.max_altitude < guarded.ceiling_at_peak
    assert not guarded.breached
