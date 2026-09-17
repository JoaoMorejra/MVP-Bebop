"""The parameter sheet must govern the Stage 3 arrival decision.

Both numbers exercised here used to be literals inside
`mvp_mission_bebop.steps.tracking`: a `2.0` multiplier on the centering
tolerance and a module-level `_ALIGNMENT_DWELL_CYCLES = 4`. An operator
widening or tightening the approach in the GCS changed neither, which is the
concrete shape of "the parameters do not govern the flight".
"""

from __future__ import annotations

from types import SimpleNamespace

from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps.tracking import VisualServoingStep


def build_context(params: MissionParameters, tilt_deg: float) -> SimpleNamespace:
    """The narrowest context `_nadir_dwell_earned` actually reads."""
    return SimpleNamespace(params=params, current_tilt_deg=tilt_deg)


def build_command(pixel_error: float) -> SimpleNamespace:
    """A nadir-phase command that has not committed and is not yet aligned."""
    return SimpleNamespace(
        nadir_committed=False,
        nadir_frozen=False,
        nadir_aligned=False,
        pixel_error=pixel_error,
    )


def at_nadir_attitude(params: MissionParameters) -> float:
    """A tilt that satisfies the attitude half of the gate."""
    return params.gimbal.nadir_tilt_deg


def test_arrival_tolerance_follows_the_configured_factor():
    params = MissionParameters()
    params.vision.optical_center_tolerance_px = 30.0
    tilt = at_nadir_attitude(params)

    # 66 px is outside the default 2.0x window (60 px) and inside a 2.5x one.
    command = build_command(pixel_error=66.0)

    params.vision.nadir_arrival_tolerance_factor = 2.0
    assert not VisualServoingStep._nadir_dwell_earned(build_context(params, tilt), command)

    params.vision.nadir_arrival_tolerance_factor = 2.5
    assert VisualServoingStep._nadir_dwell_earned(build_context(params, tilt), command)


def test_a_tighter_factor_rejects_an_error_the_default_would_accept():
    params = MissionParameters()
    params.vision.optical_center_tolerance_px = 30.0
    tilt = at_nadir_attitude(params)
    command = build_command(pixel_error=50.0)

    assert VisualServoingStep._nadir_dwell_earned(build_context(params, tilt), command)

    params.vision.nadir_arrival_tolerance_factor = 1.0
    assert not VisualServoingStep._nadir_dwell_earned(build_context(params, tilt), command)


def test_a_committed_freeze_still_short_circuits_the_gate():
    """The factor governs the pre-commitment evidence, nothing after it."""
    params = MissionParameters()
    params.vision.nadir_arrival_tolerance_factor = 1.0
    context = build_context(params, at_nadir_attitude(params))

    committed = SimpleNamespace(
        nadir_committed=True, nadir_frozen=True, nadir_aligned=False, pixel_error=9999.0
    )
    assert VisualServoingStep._nadir_dwell_earned(context, committed)


def test_the_dwell_requirement_is_read_from_configuration():
    """`_servo` must take its dwell from `vision.alignment_dwell_cycles`."""
    import inspect

    source = inspect.getsource(VisualServoingStep._servo)
    assert "vision_cfg.alignment_dwell_cycles" in source
    assert "_ALIGNMENT_DWELL_CYCLES" not in source
    # The configured value is clamped to at least one cycle, so a zero in the
    # config cannot make the stage exit on its first nadir frame.
    assert "max(1, int(vision_cfg.alignment_dwell_cycles))" in source
