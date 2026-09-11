"""Unit tests for operator-interrupt handling."""

import threading

import pytest

from mvp_mission_bebop.engine.signals import DEFAULT_BUDGET_SEC, EmergencyHandler


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
