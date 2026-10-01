"""The mission does not import the speech synthesizer when the station narrates (7.1).

Measured: ``google.genai`` cost 1.8 s of the mission's 4.6 s boot, pulled in by
``mvp_mission_bebop.telemetry/__init__.py`` through ``telemetry.odometry``,
although under a ground-station session the mission only writes ``[ALERT]``
lines and never synthesizes a word.
"""

from __future__ import annotations

import os
import subprocess
import sys

PROBE = (
    "import sys;"
    "import mvp_mission_bebop.mission;"
    "from mvp_mission_bebop.telemetry.announcer import announce_sync;"
    "announce_sync('Falha de segurança', details={'erro': 'x'}, priority='CRITICAL');"
    "print('GENAI_LOADED=' + str('google.genai' in sys.modules))"
)


def run(env_overrides):
    env = {**os.environ, **env_overrides}
    completed = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=180, env=env)
    assert completed.returncode == 0, completed.stderr[-2000:]
    return completed.stdout


def test_a_station_session_never_imports_the_synthesizer():
    out = run({"BMG_GCS_SESSION": "1"})
    assert "[ALERT " in out
    assert "GENAI_LOADED=False" in out


def test_the_synthesizer_loads_on_demand():
    from mvp_mission_bebop.telemetry import announcer

    genai, types = announcer._load_genai()
    if genai is None:
        return
    assert "google.genai" in sys.modules
    assert announcer._load_genai() == (genai, types)


def test_the_package_exports_stay_reachable():
    import mvp_mission_bebop.telemetry as telemetry

    assert telemetry.FailsafeSupervisor.__name__ == "FailsafeSupervisor"
    assert callable(telemetry.announce_sync)
    assert set(telemetry.__all__) >= {"announce_sync", "OdometrySupervisor", "MissionAudioAnnouncer"}
