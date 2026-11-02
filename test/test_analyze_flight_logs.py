"""Flight log analyzer: the numeric baseline the refinement tasks are accepted against."""

from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "analyze_flight_logs.py")
_SAMPLE = os.path.join(_ROOT, "test", "fixtures", "flight_log_sample.log")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("analyze_flight_logs", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["analyze_flight_logs"] = module
    spec.loader.exec_module(module)
    return module


analyzer = _load()


def test_ascent_is_parsed_with_its_overshoot() -> None:
    record = analyzer.parse_log(_SAMPLE)
    ascent = record.ascents[0]
    assert ascent.deficit_m == 0.20
    assert ascent.target_m == 1.15
    assert ascent.final_m == 1.33
    assert ascent.overshoot_m == pytest.approx(0.18, abs=0.005)
    assert ascent.converged is False


def test_landing_gate_and_touchdown() -> None:
    record = analyzer.parse_log(_SAMPLE)
    assert record.landing_gate_radial_m == [0.110]
    assert record.touchdown_z_rel == [0.35]


def test_altitude_divergence_per_probe() -> None:
    record = analyzer.parse_log(_SAMPLE)
    assert record.probes
    assert len(record.alt_divergence_m) == len(record.probes)
    first = record.probes[0]
    assert record.alt_divergence_m[0] == pytest.approx(abs(first.z_rel - first.fw_alt))
    assert max(record.alt_divergence_m) == pytest.approx(0.353, abs=1e-6)


def test_cadence_reports_are_collected() -> None:
    record = analyzer.parse_log(_SAMPLE)
    assert any(name.startswith("Search") for name, _, _ in record.cadence)


def test_new_structured_lines_are_parsed(tmp_path: "os.PathLike[str]") -> None:
    path = os.path.join(str(tmp_path), "mission_x.log")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "2026-10-27 10:00:00 [INFO] [Step1Takeoff] [ASCENT_REPORT target=1.500 peak=1.530 "
            "overshoot=0.030 t_converge=5.20 err_final=0.010 converged=1]\n"
            "2026-10-27 10:00:01 [INFO] [ActuatorProxy] [CMD t=1.000 vx=0.000 vy=0.100 vz=0.000 "
            "vyaw=0.000 dt_ms=66.7 p95_ms=70.1 max_ms=80.0]\n"
            "2026-10-27 10:00:02 [INFO] [Step5RTL] [DESCENT t=3.0 phase=COMMIT h=0.400 "
            "err=(0.010,-0.020) vz=0.000 commit=1]\n"
        )
    record = analyzer.parse_log(path)
    assert record.ascents[0].overshoot_m == pytest.approx(0.03)
    assert record.ascents[0].converged is True
    assert record.cmd_p95_ms == [70.1]
    assert record.commit_error_m == [pytest.approx((0.01 ** 2 + 0.02 ** 2) ** 0.5)]


def test_summary_and_csv(tmp_path: "os.PathLike[str]") -> None:
    record = analyzer.parse_log(_SAMPLE)
    table = analyzer.format_table([record])
    assert "flight_log_sample" in table
    out = os.path.join(str(tmp_path), "out.csv")
    analyzer.write_csv([record], out)
    with open(out, encoding="utf-8") as handle:
        assert handle.readline().startswith("log,")
