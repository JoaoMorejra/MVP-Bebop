"""Detector warm-up overlapped with the wait for the first frame (7.4).

Measured on the bench: 2.5 s waiting for the first frame, then 3.2 s loading
and warming the detector, in sequence. The preloader loads the devices the
selection will need on a thread while the camera is still opening; the choice
itself is still made on the first real frame.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from mvp_mission_bebop.perception import inference_device as idev


class SlowDetector:
    def __init__(self, name, load_sec=0.2, fail=False):
        self.name = name
        self.load_sec = load_sec
        self.fail = fail
        self.loads = 0
        self.calls = 0

    def load(self):
        time.sleep(self.load_sec)
        if self.fail:
            raise RuntimeError(f"{self.name} cannot load")
        self.loads += 1
        return True

    def detect(self, frame, **kwargs):
        self.calls += 1
        return f"{self.name}-result"


def recording_builder(**overrides):
    built = []

    def build(device):
        detector = SlowDetector(device, **overrides.get(device, {}))
        built.append(detector)
        return detector

    return build, built


def test_start_returns_at_once_and_loads_in_the_background():
    build, built = recording_builder()
    preloader = idev.DetectorPreloader(["CUDA"], build, warm_frame="frame")
    started = time.monotonic()
    preloader.start()
    assert time.monotonic() - started < 0.1
    preloader.join(2.0)
    assert built[0].loads == 1
    assert built[0].calls >= 1, "the preload must run the first, expensive inference"


def test_the_selection_receives_the_preloaded_detector_without_loading_it_again(tmp_path):
    build, built = recording_builder()
    preloader = idev.DetectorPreloader(["IGPU"], build, warm_frame="frame")
    preloader.start()
    chain, report = idev.select_detector(
        "IGPU", ["IGPU", "CPU"], preloader.build, frame="real", cache_path=str(tmp_path / "c.json"), cache_key="k"
    )
    assert chain.device == "IGPU"
    assert len(built) == 1 and built[0].loads == 1


def test_a_device_not_preloaded_is_built_on_demand():
    build, built = recording_builder()
    preloader = idev.DetectorPreloader([], build, warm_frame="frame")
    preloader.start()
    detector = preloader.build("CPU")
    detector.load()
    assert built[0].name == "CPU" and built[0].loads == 1


def test_a_preloaded_detector_is_handed_out_once():
    build, built = recording_builder()
    preloader = idev.DetectorPreloader(["CPU"], build, warm_frame="frame")
    preloader.start()
    first = preloader.build("CPU")
    second = preloader.build("CPU")
    second.load()
    assert first is not second
    assert len(built) == 2


def test_a_failed_preload_is_raised_to_the_selection():
    build, _ = recording_builder(CUDA={"fail": True})
    preloader = idev.DetectorPreloader(["CUDA"], build, warm_frame="frame")
    preloader.start()
    with pytest.raises(RuntimeError, match="cannot load"):
        preloader.build("CUDA")


def test_the_plan_follows_the_selection(tmp_path):
    cache = tmp_path / "c.json"
    present = ["CUDA", "IGPU", "CPU"]
    assert idev.preload_plan("IGPU", present, str(cache), "k") == ["IGPU"]
    assert idev.preload_plan("CUDA", ["IGPU", "CPU"], str(cache), "k") == ["IGPU"]
    assert idev.preload_plan("AUTO", present, str(cache), "k") == ["CUDA", "IGPU", "CPU"]
    cache.write_text(json.dumps({"k": {"device": "IGPU", "p95_ms": 30.0}}))
    assert idev.preload_plan("AUTO", present, str(cache), "k") == ["IGPU"]
    assert idev.preload_plan("AUTO", present, str(cache), "other") == ["CUDA", "IGPU", "CPU"]


def test_a_device_can_be_warmed_more_than_once():
    build, built = recording_builder()
    preloader = idev.DetectorPreloader(["CUDA", "CPU"], build, warm_frame="frame", warm_counts={"CUDA": 10})
    preloader.start()
    preloader.join(5.0)
    by_name = {detector.name: detector for detector in built}
    assert by_name["CUDA"].calls == 10
    assert by_name["CPU"].calls == 1
