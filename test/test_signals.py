"""Unit tests for operator-interrupt handling."""

import threading

import pytest

from mvp_mission_bebop.engine.signals import DEFAULT_BUDGET_SEC, FORCED_EXIT_CODE, EmergencyHandler


class Recorder:
    def __init__(self):
        self.landings = []
        self.finalizations = 0

    def land(self, budget):
        self.landings.append(budget)

    def finalize(self):
        self.finalizations += 1


def build(**kwargs):
    recorder = Recorder()
    handler = EmergencyHandler(
        emergency_event=threading.Event(),
        land_sequence=recorder.land,
        finalizer=recorder.finalize,
        **kwargs,
    )
    return handler, recorder


def test_rejects_a_non_positive_budget():
    with pytest.raises(ValueError):
        build(budget_sec=0.0)


def test_budget_fits_inside_the_window_the_gcs_waits():
    """electron/main.cjs:759 sends SIGINT and waits 800 ms before moving on.

    The previous handler spent roughly five seconds inside the signal handler,
    so most of its work happened after the ground station had stopped watching.
    """
    assert DEFAULT_BUDGET_SEC < 0.8


def test_starts_untriggered_and_unfinalized():
    handler, recorder = build()
    assert not handler.triggered
    assert not handler.finalized
    assert recorder.finalizations == 0


def test_finalize_runs_exactly_once():
    """The defect: cleanup ran three times on every Ctrl-C.

    ``sys.exit`` raised ``SystemExit``, a ``BaseException``, which escaped the
    pipeline's ``except Exception`` and unwound through two further ``finally``
    blocks, each repeating the teardown.
    """
    handler, recorder = build()
    handler.finalize_once()
    handler.finalize_once()
    handler.finalize_once()
    assert recorder.finalizations == 1
    assert handler.finalized


def test_finalizer_exceptions_do_not_propagate():
    """Teardown must not be able to prevent the process from exiting."""

    def explode():
        raise RuntimeError("cleanup failed")

    handler = EmergencyHandler(
        emergency_event=threading.Event(),
        land_sequence=lambda budget: None,
        finalizer=explode,
    )
    handler.finalize_once()
    assert handler.finalized


def test_signal_sets_the_event_lands_and_exits():
    handler, recorder = build()

    with pytest.raises(SystemExit) as exit_info:
        handler._on_signal(2, None)

    assert exit_info.value.code == 0
    assert handler.triggered
    assert handler._event.is_set()
    assert len(recorder.landings) == 1
    assert recorder.landings[0] <= DEFAULT_BUDGET_SEC
    assert recorder.finalizations == 1


def test_landing_failure_still_finalizes_and_exits():
    def explode(budget):
        raise RuntimeError("radio down")

    handler = EmergencyHandler(
        emergency_event=threading.Event(),
        land_sequence=explode,
        finalizer=lambda: None,
    )
    with pytest.raises(SystemExit):
        handler._on_signal(15, None)
    assert handler.finalized


def test_normal_shutdown_and_interrupt_do_not_both_clean_up():
    handler, recorder = build()
    handler.finalize_once()

    with pytest.raises(SystemExit):
        handler._on_signal(2, None)

    assert recorder.finalizations == 1


def test_install_requires_the_main_thread():
    """Registration is explicit so a runner can be built off-thread in a test."""
    handler, _ = build()
    failure = {}

    def attempt():
        try:
            handler.install()
        except RuntimeError as exc:
            failure["error"] = exc

    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join()

    assert isinstance(failure.get("error"), RuntimeError)
    assert not handler.installed


def test_install_and_uninstall_round_trip():
    import signal

    handler, _ = build()
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)
    try:
        handler.install()
        assert handler.installed
        assert signal.getsignal(signal.SIGINT) is not previous_int

        handler.uninstall()
        assert not handler.installed
        assert signal.getsignal(signal.SIGINT) is previous_int
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def test_install_is_idempotent():
    import signal

    handler, _ = build()
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)
    try:
        handler.install()
        handler.install()
        handler.uninstall()
        assert signal.getsignal(signal.SIGINT) is previous_int
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


# ------------------------------------------------- repeated operator interrupt


class ForcedExit(BaseException):
    """Stands in for ``os._exit`` so a test can observe it without dying."""


@pytest.fixture
def forced_exits(monkeypatch):
    codes = []

    def fake_exit(code):
        codes.append(code)
        raise ForcedExit(code)

    monkeypatch.setattr("mvp_mission_bebop.engine.signals.os._exit", fake_exit)
    return codes


def test_second_interrupt_during_the_burst_does_not_cut_the_landing(forced_exits):
    """Double-clicking Abort sent a second SIGINT that ran ``os._exit(130)``.

    The nested handler interrupted the first one between two land commands, so
    the burst meant to survive a lossy link was truncated to whatever had gone
    out before the second click. The burst must complete in full.
    """
    sent = []
    holder = {}

    def burst(_budget):
        for index in range(5):
            sent.append(index)
            if index == 1:
                holder["handler"]._on_signal(2, None)

    handler = EmergencyHandler(
        emergency_event=threading.Event(), land_sequence=burst, finalizer=lambda: None
    )
    holder["handler"] = handler

    with pytest.raises(SystemExit):
        handler._on_signal(2, None)

    assert sent == [0, 1, 2, 3, 4]
    assert forced_exits == []
    assert handler.landing_transmitted


def test_second_interrupt_after_the_burst_forces_exit(forced_exits):
    """Once the landing is on the wire, insisting still leaves immediately."""
    holder = {}

    def slow_finalizer():
        holder["handler"]._on_signal(2, None)

    handler = EmergencyHandler(
        emergency_event=threading.Event(), land_sequence=lambda _b: None, finalizer=slow_finalizer
    )
    holder["handler"] = handler

    with pytest.raises(ForcedExit):
        handler._on_signal(2, None)

    assert forced_exits == [FORCED_EXIT_CODE]


def test_burst_is_marked_transmitted_even_when_it_raises(forced_exits):
    """A failed dispatch must not leave a later interrupt unable to force exit."""

    def explode(_budget):
        raise RuntimeError("radio down")

    handler = EmergencyHandler(
        emergency_event=threading.Event(), land_sequence=explode, finalizer=lambda: None
    )
    with pytest.raises(SystemExit):
        handler._on_signal(2, None)
    assert handler.landing_transmitted


def test_runner_burst_zeroes_velocity_before_each_land_and_ends_on_land():
    """Every land is preceded by a zero Twist, and the last command is a land.

    An airframe still holding a velocity setpoint keeps translating through the
    descent; a Twist sent after the final land would be the last word on the wire.
    """
    import types

    from mvp_mission_bebop.engine.runner import MissionRunner

    calls = []
    drone = types.SimpleNamespace(
        move_velocity=lambda **kw: calls.append(("twist", tuple(sorted(kw.items())))),
        land=lambda: calls.append(("land", ())),
    )
    ctx = types.SimpleNamespace(emergency_event=threading.Event(), drone=drone)
    runner = MissionRunner(ctx, [])

    runner._transmit_emergency_landing(DEFAULT_BUDGET_SEC)

    kinds = [kind for kind, _ in calls]
    assert kinds[-1] == "land"
    assert kinds == ["twist", "land"] * (len(kinds) // 2)
    assert len(kinds) // 2 >= 2
    for kind, args in calls:
        if kind == "twist":
            assert all(value == 0.0 for _, value in args)


def _bare_runner(touchdown_confirmed=None):
    import types

    from mvp_mission_bebop.engine.runner import MissionRunner

    ctx = types.SimpleNamespace(
        emergency_event=threading.Event(),
        blackboard=types.SimpleNamespace(touchdown_confirmed=touchdown_confirmed),
    )
    return MissionRunner(ctx, [])


def test_an_operator_abort_exits_aborted_landed(monkeypatch):
    """The abort used to exit 0 and was announced as a safe landing at base."""
    runner = _bare_runner()
    monkeypatch.setattr(runner, "_transmit_emergency_landing", lambda _budget: None)
    monkeypatch.setattr(runner.emergency, "_finalizer", lambda: None)

    with pytest.raises(SystemExit) as exit_info:
        runner.emergency._on_signal(2, None)

    assert exit_info.value.code == 3
    assert runner.exit_code(False) == 3


def test_an_unconfirmed_touchdown_exits_four_and_a_confirmed_one_zero():
    assert _bare_runner(touchdown_confirmed=False).exit_code(True) == 4
    assert _bare_runner(touchdown_confirmed=True).exit_code(True) == 0
    assert _bare_runner(touchdown_confirmed=None).exit_code(True) == 0
    assert _bare_runner(touchdown_confirmed=None).exit_code(False) == 1


def test_the_runner_records_whether_a_stage_was_entered_by_a_jump():
    """``mission.rtl_start`` must not say "inspection complete" on a station jump."""
    import types

    from mvp_mission_bebop.engine.runner import MissionRunner
    from mvp_mission_bebop.steps.base import StepStatus

    seen = []

    class Probe:
        def __init__(self, name, jump_to=None):
            self.name = name
            self.jump_to = jump_to

        def execute(self, ctx):
            seen.append((self.name, ctx.entered_by_jump))
            if self.jump_to is not None and not ctx.stage_jump_event.is_set() and self.name == "two":
                ctx.requested_stage = self.jump_to
                ctx.stage_jump_event.set()
                return StepStatus.ABORTED
            return StepStatus.SUCCESS

    ctx = types.SimpleNamespace(
        emergency_event=threading.Event(),
        stage_jump_event=threading.Event(),
        requested_stage=None,
        detection_reveal_enabled=True,
        current_stage=None,
    )
    steps = [Probe("one"), Probe("two", jump_to=5), Probe("three"), Probe("five")]
    MissionRunner(ctx, steps, stage_numbers=[1, 2, 3, 5]).run()
    assert seen == [("one", False), ("two", False), ("five", True)]
