"""Inference device selection: CUDA (MX110), integrated GPU (HD 620) and CPU.

Decision D3: the three devices are enabled; the MX110 is preferred when the
benchmark says it is the best, otherwise the integrated GPU, then the CPU, with
a runtime fallback down that chain. These tests cover the rule, the runtime
fallback and the cached choice, with stand-in detectors.
"""

from __future__ import annotations

import json

import pytest

from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.perception import inference_device as idev


# ------------------------------------------------------------ normalization


@pytest.mark.parametrize("value, expected", [("AUTO", "AUTO"), ("auto", "AUTO"), (" cuda ", "CUDA"), ("iGPU", "IGPU"), ("cpu", "CPU")])
def test_every_documented_device_is_accepted(value, expected):
    assert idev.normalize_inference_device(value) == expected


@pytest.mark.parametrize("value", ["gpu", "tpu", "", "cuda:0"])
def test_an_unknown_device_is_refused(value):
    with pytest.raises(ValueError):
        idev.normalize_inference_device(value)


@pytest.mark.parametrize("value", [None, 3, True, ["CUDA"]])
def test_a_non_string_device_is_refused(value):
    with pytest.raises(TypeError):
        idev.normalize_inference_device(value)


def test_the_parameter_exists_defaults_to_auto_and_round_trips():
    params = MissionParameters()
    assert params.vision.inference_device == "AUTO"
    assert params.vision.inference_min_period_sec == pytest.approx(0.10)
    params.update_from_dict({"vision": {"inference_device": "IGPU"}})
    assert params.vision.inference_device == "IGPU"
    assert params.to_dict()["vision"]["inference_device"] == "IGPU"


def test_the_openvino_model_sits_beside_the_weights():
    assert idev.openvino_model_path("/m/yolov8n.pt") == "/m/yolov8n_openvino_model"
    assert idev.openvino_model_path("/m/yolov8n_openvino_model") == "/m/yolov8n_openvino_model"


# ------------------------------------------------------------ decision rule


def test_the_lowest_p95_wins_outside_the_tie_band():
    stats = {"CUDA": (47.0, 48.0), "IGPU": (40.0, 42.0), "CPU": (60.0, 70.0)}
    assert idev.choose_device(stats) == "IGPU"


def test_a_technical_tie_prefers_cuda_then_igpu_then_cpu():
    assert idev.choose_device({"CUDA": (44.0, 46.0), "IGPU": (40.0, 42.0)}) == "CUDA"
    assert idev.choose_device({"IGPU": (40.0, 42.0), "CPU": (39.0, 41.0)}) == "IGPU"
    assert idev.choose_device({"CUDA": (45.0, 46.2), "IGPU": (40.0, 42.0)}) == "CUDA"


def test_a_device_just_outside_the_band_loses():
    assert idev.choose_device({"CUDA": (46.0, 46.3), "IGPU": (40.0, 42.0)}) == "IGPU"


def test_no_measurement_is_refused():
    with pytest.raises(ValueError):
        idev.choose_device({})


# ------------------------------------------------------------ runtime fallback


class FakeDetector:
    def __init__(self, name, fail_after=None, error=None):
        self.name = name
        self.calls = 0
        self.fail_after = fail_after
        self.error = error or RuntimeError("CUDA error: an illegal memory access was encountered")
        self.loaded = False

    def load(self):
        self.loaded = True
        return True

    def detect(self, frame, **kwargs):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise self.error
        return f"{self.name}-result"

    def draw_detections(self, frame, result):
        return f"{self.name}-drawn"


def test_a_failing_device_falls_back_down_the_chain_without_losing_the_frame():
    import logging

    records = []
    handler = logging.Handler(level=logging.WARNING)
    handler.emit = records.append
    logging.getLogger("InferenceDevice").addHandler(handler)
    built = []

    def build(device):
        detector = FakeDetector(device, fail_after=1 if device == "CUDA" else None)
        built.append(detector)
        return detector

    chain = idev.FallbackDetector(["CUDA", "IGPU", "CPU"], build)
    chain.load()
    assert chain.detect("frame") == "CUDA-result"
    try:
        assert chain.detect("frame") == "IGPU-result"
    finally:
        logging.getLogger("InferenceDevice").removeHandler(handler)
    assert chain.device == "IGPU"
    assert any("CUDA" in record.getMessage() and "IGPU" in record.getMessage() for record in records)
    assert chain.draw_detections("frame", None) == "IGPU-drawn"


def test_a_device_that_cannot_even_load_is_skipped():
    def build(device):
        if device == "CUDA":
            raise RuntimeError("no CUDA GPUs are available")
        return FakeDetector(device)

    chain = idev.FallbackDetector(["CUDA", "IGPU", "CPU"], build)
    chain.load()
    assert chain.device == "IGPU"
    assert chain.detect("frame") == "IGPU-result"


def test_an_exhausted_chain_raises_the_last_error():
    chain = idev.FallbackDetector(["CPU"], lambda device: FakeDetector(device, fail_after=0, error=ValueError("bad")))
    chain.load()
    with pytest.raises(ValueError):
        chain.detect("frame")


# ------------------------------------------------------------ selection and cache


class TimedDetector(FakeDetector):
    """Reports a fixed per-call latency through the injected clock."""

    def __init__(self, name, latency_ms, clock):
        super().__init__(name)
        self.latency_ms = latency_ms
        self.clock = clock

    def detect(self, frame, **kwargs):
        self.clock.advance(self.latency_ms / 1000.0)
        return super().detect(frame, **kwargs)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_auto_benchmarks_every_available_device_and_caches_the_choice(tmp_path):
    clock = FakeClock()
    latencies = {"CUDA": 48.0, "IGPU": 41.0, "CPU": 70.0}
    built = []

    def build(device):
        detector = TimedDetector(device, latencies[device], clock)
        built.append(detector)
        return detector

    cache = tmp_path / "inference_device.json"
    chain, report = idev.select_detector(
        "AUTO", ["CUDA", "IGPU", "CPU"], build, frame="frame", cache_path=cache, cache_key="k", clock=clock
    )
    assert chain.device == "IGPU"
    assert report["device"] == "IGPU"
    assert set(report["candidates"]) == {"CUDA", "IGPU", "CPU"}
    stored = json.loads(cache.read_text())
    assert stored["k"]["device"] == "IGPU"

    built.clear()
    chain, report = idev.select_detector(
        "AUTO", ["CUDA", "IGPU", "CPU"], build, frame="frame", cache_path=cache, cache_key="k", clock=clock
    )
    assert chain.device == "IGPU"
    assert report["cached"] is True
    assert [d.name for d in built] == ["IGPU"]
    assert built[0].calls <= idev.VALIDATION_SAMPLES + idev.WARMUP_SAMPLES


def test_the_benchmark_stays_within_its_budget(tmp_path):
    clock = FakeClock()

    def build(device):
        return TimedDetector(device, 900.0, clock)

    _chain, report = idev.select_detector(
        "AUTO", ["CUDA", "IGPU", "CPU"], build, frame="frame", cache_path=tmp_path / "c.json", cache_key="k", clock=clock
    )
    warmups = 3 * idev.WARMUP_SAMPLES * 0.9
    assert clock() - warmups <= idev.BENCH_BUDGET_SEC + 1.0


def test_slow_first_inferences_do_not_starve_the_later_devices(tmp_path):
    """Measured: CUDA's 1.9 s first call used the budget and it won unmeasured rivals."""
    clock = FakeClock()

    class ColdStart(TimedDetector):
        def detect(self, frame, **kwargs):
            if self.calls == 0:
                self.clock.advance(2.0)
            return super().detect(frame, **kwargs)

    latencies = {"CUDA": 48.0, "IGPU": 41.0, "CPU": 70.0}
    chain, report = idev.select_detector(
        "AUTO",
        ["CUDA", "IGPU", "CPU"],
        lambda d: ColdStart(d, latencies[d], clock),
        frame="frame",
        cache_path=tmp_path / "c.json",
        cache_key="k",
        clock=clock,
    )
    assert set(report["candidates"]) == {"CUDA", "IGPU", "CPU"}
    assert chain.device == "IGPU"


def test_an_explicit_device_skips_the_benchmark_but_keeps_its_fallback(tmp_path):
    built = []

    def build(device):
        detector = FakeDetector(device)
        built.append(detector)
        return detector

    chain, report = idev.select_detector(
        "IGPU", ["CUDA", "IGPU", "CPU"], build, frame="frame", cache_path=tmp_path / "c.json", cache_key="k"
    )
    assert chain.device == "IGPU"
    assert report["candidates"] == {}
    assert chain.chain == ["IGPU", "CPU"]


def test_an_explicit_device_that_is_absent_falls_to_the_next_one(tmp_path):
    chain, _ = idev.select_detector(
        "CUDA", ["IGPU", "CPU"], lambda d: FakeDetector(d), frame="frame", cache_path=tmp_path / "c.json", cache_key="k"
    )
    assert chain.device == "IGPU"


def test_openvino_compilations_are_cached_on_disk(monkeypatch, tmp_path):
    """Measured: the CPU IR loads in 241 ms from the cache against 1148 ms cold."""
    import sys
    import types as _types

    settings = []

    class Core:
        def __init__(self):
            pass

        def set_property(self, properties):
            settings.append(properties)

    fake = _types.SimpleNamespace(Core=Core)
    monkeypatch.setitem(sys.modules, "openvino", fake)
    monkeypatch.setenv("BMG_OV_CACHE_DIR", str(tmp_path / "ov"))
    idev.enable_openvino_compile_cache()
    idev.enable_openvino_compile_cache()
    fake.Core()
    fake.Core()
    assert settings == [{"CACHE_DIR": str(tmp_path / "ov")}] * 2
    assert (tmp_path / "ov").is_dir()


# ------------------------------------------------------------ predictor overrides (4.5g)


class FakeYolo:
    def __init__(self):
        self.calls = []

    def predict(self, *args, **kwargs):
        self.calls.append(kwargs)
        return "predicted"


class FakeSdkDetector:
    def __init__(self):
        self._model = types_namespace(model=FakeYolo())

    def load(self):
        return True

    def detect(self, frame, **kwargs):
        return self._model.model.predict(frame, device="cuda:0", **kwargs)


def types_namespace(**kwargs):
    import types

    return types.SimpleNamespace(**kwargs)


def test_the_cuda_detector_runs_fp16_through_the_sdk():
    """Measured at 480 px, two models interleaved: FP32 47.8/48.9 ms, FP16 44.2/45.2 ms."""
    sdk = FakeSdkDetector()
    detector = idev._PinnedPredict(sdk, {"quantize": idev.CUDA_QUANTIZE})
    assert detector.load() is True
    detector.detect("frame", conf=0.3, imgsz=480)
    assert sdk._model.model.calls[-1]["quantize"] == 16
    assert sdk._model.model.calls[-1]["device"] == "cuda:0"
    assert sdk._model.model.calls[-1]["imgsz"] == 480


def test_the_openvino_device_is_pinned_on_every_call():
    sdk = FakeSdkDetector()
    detector = idev._PinnedPredict(sdk, {"device": "intel:gpu.0"})
    detector.load()
    detector.detect("frame")
    assert sdk._model.model.calls[-1]["device"] == "intel:gpu.0"


def test_an_unexpected_sdk_layout_is_refused():
    class Bare:
        def load(self):
            return True

    with pytest.raises(RuntimeError):
        idev._PinnedPredict(Bare(), {"device": "intel:cpu"}).load()


def test_a_choice_cached_under_another_cuda_precision_is_not_reused(monkeypatch):
    fp16 = idev.model_cache_key("/m/yolov8n.pt", 480)
    monkeypatch.setattr(idev, "CUDA_QUANTIZE", 32)
    assert idev.model_cache_key("/m/yolov8n.pt", 480) != fp16
