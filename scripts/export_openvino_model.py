#!/usr/bin/env python3
"""Offline export of the mission detector to OpenVINO IR for CPU inference.

The ground station has no GPU, and YOLOv8n through PyTorch costs about 80 ms per
frame on its i5-7200U at the native 640 px input. This script produces an
OpenVINO IR once, on the ground; the flight pipeline only ever *loads* an
artefact that already exists and never exports, converts or downloads anything
during a mission.

The export is performed by Ultralytics' native exporter
(``YOLO(model).export(format="openvino", half=True, dynamic=True)``), which
writes a ``<stem>_openvino_model/`` directory beside the source weights.
Nectar's ``Detector`` loads that directory unchanged, because
``UltralyticsModel`` hands any existing path straight to ``YOLO(path)``.

The input shape is exported *dynamic* by default, and that is the choice that
makes the export worth doing. The Bebop frame is 856x480; PyTorch letterboxes it
to a rectangular 640x384 tensor, whereas a static IR forces a square 640x640 --
67 % more pixels -- and ends up slower than the PyTorch model it replaces.
Bench, one 856x480 frame, median of 30 on the i5-7200U:

=================  =========  =========
model              640 px     480 px
=================  =========  =========
PyTorch ``.pt``    82 ms      47 ms
OpenVINO static    103 ms     59 ms
OpenVINO dynamic   65 ms      39 ms
=================  =========  =========

FP16 and FP32 IRs ran within 1 ms of each other: this CPU has no native FP16
arithmetic, so ``half`` only halves the file. It is kept as the default because
it costs nothing here and pays off on hardware that has it.

Prerequisite::

    source /home/jv/ros2_ws/bin/nectar-activate
    pip install openvino

Usage examples::

    # Export the model named in mission_config.json (dynamic shape, FP16)
    python3 export_openvino_model.py

    # Export, then benchmark it on a benchtop frame at a reduced input size
    python3 export_openvino_model.py --imgsz 480 --verify

Pointing the mission at the exported model
------------------------------------------
Set both fields in ``mvp_mission_bebop/mission_config.json`` (or in the GCS
parameter sheet), using the absolute directory the script prints::

    "vision": {
      "model_path": "/home/jv/ros2_ws/src/mvp_mission_bebop/mvp_mission_bebop/yolov8n_openvino_model",
      "inference_imgsz": 480,
      ...
    }

The directory name must keep its ``_openvino_model`` suffix: Ultralytics
selects the inference backend from it, and a renamed directory is rejected as
"not a supported model format". With the default dynamic export,
``vision.inference_imgsz`` may be any multiple of 32 (``null`` for 640). A
``--static`` export fixes the shape, and ``inference_imgsz`` must then equal
the ``--imgsz`` used here. To revert, set ``model_path`` back to the ``.pt``
file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Final, Optional

_PACKAGE_DIR: Final[str] = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mvp_mission_bebop")
)
_DEFAULT_CONFIG: Final[str] = os.path.join(_PACKAGE_DIR, "mission_config.json")
_DEFAULT_MODEL: Final[str] = "yolov8n.pt"
_NATIVE_IMGSZ: Final[int] = 640
_IMGSZ_STRIDE: Final[int] = 32
_BENCHTOP_FRAME: Final[str] = os.path.join(_PACKAGE_DIR, "accident_raw_20260903_033713.png")


def load_vision_config(config_path: str) -> Dict[str, Any]:
    """Return the ``vision`` section of a mission config, or ``{}``.

    Parameters
    ----------
    config_path : str
        Path to ``mission_config.json``.

    Returns
    -------
    Dict[str, Any]
        The section as stored. A missing or unreadable file yields an empty
        mapping, matching ``MissionParameters.load_from_file``, which falls
        back to the defaults in the same situation.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            section = json.load(handle).get("vision", {})
    except (OSError, ValueError, AttributeError):
        return {}
    return section if isinstance(section, dict) else {}


def resolve_model_path(model: str, config_path: str) -> str:
    """Resolve a configured model path the way the mission does.

    The GCS launches ``mission.py`` from the package directory, which is also
    where ``mission_config.json`` lives, so a relative ``model_path`` is taken
    relative to the config file rather than to this script's working directory.

    Raises
    ------
    FileNotFoundError
        If the weights do not exist. Ultralytics would otherwise treat a bare
        name such as ``yolov8n.pt`` as a download request, and this tool must
        not fetch anything.
    ValueError
        If the path does not name ``.pt`` weights.
    """
    candidate = os.path.expanduser(model)
    if not os.path.isabs(candidate):
        candidate = os.path.join(os.path.dirname(os.path.abspath(config_path)), candidate)
    candidate = os.path.normpath(candidate)
    if not candidate.endswith(".pt"):
        raise ValueError(f"expected PyTorch weights ending in .pt, got {candidate}")
    if not os.path.isfile(candidate):
        raise FileNotFoundError(f"weights not found: {candidate}")
    return candidate


def parse_imgsz(value: Optional[str]) -> int:
    """Parse the export input size, accepting ``"480"`` and ``"480.0"``.

    ``None`` or an empty string selects the native 640 px.

    Raises
    ------
    argparse.ArgumentTypeError
        If the value is not a positive multiple of the YOLOv8 stride.
    """
    if value is None or not str(value).strip():
        return _NATIVE_IMGSZ
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"imgsz must be an integer, got {value!r}") from exc
    if not number.is_integer():
        raise argparse.ArgumentTypeError(f"imgsz must be an integer, got {value!r}")
    size = int(number)
    if size <= 0 or size % _IMGSZ_STRIDE != 0:
        raise argparse.ArgumentTypeError(
            f"imgsz must be a positive multiple of {_IMGSZ_STRIDE}, got {size}"
        )
    return size


def export(weights: str, imgsz: int, *, half: bool, dynamic: bool) -> str:
    """Export ``weights`` to OpenVINO IR and return the output directory."""
    try:
        import openvino  # noqa: F401 - probe only; Ultralytics imports it itself
    except ImportError:
        sys.exit("openvino is not installed in this environment: pip install openvino")

    from ultralytics import YOLO

    output = YOLO(weights).export(format="openvino", imgsz=imgsz, half=half, dynamic=dynamic)
    return os.path.abspath(str(output))


def verify(model_dir: str, imgsz: int, frames: int) -> None:
    """Load the IR through Nectar's ``Detector`` and time it on a benchtop frame."""
    import cv2
    from nectar.ai.detection import Detector

    image = cv2.imread(_BENCHTOP_FRAME)
    if image is None:
        print(f"Verification skipped: benchtop frame not found at {_BENCHTOP_FRAME}")
        return

    detector = Detector(model_dir, device="cpu")
    detector.load()
    detector.detect(image, conf=0.5, imgsz=imgsz)

    started = time.perf_counter()
    for _ in range(frames):
        result = detector.detect(image, conf=0.5, imgsz=imgsz)
    per_frame_ms = (time.perf_counter() - started) / frames * 1000.0
    print(f"Verified: {per_frame_ms:.1f} ms/frame over {frames} frames, {len(result)} detections.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export the mission YOLO model to OpenVINO IR (offline, ground only).",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default=_DEFAULT_CONFIG, help="mission_config.json to read")
    parser.add_argument("--model", help="weights to export (default: vision.model_path)")
    parser.add_argument(
        "--imgsz",
        help="export input size (default: vision.inference_imgsz, else 640)",
    )
    parser.add_argument("--no-half", action="store_true", help="export FP32 instead of FP16")
    parser.add_argument(
        "--static",
        action="store_true",
        help="export a fixed square input shape (slower for the 16:9 Bebop frame)",
    )
    parser.add_argument("--verify", action="store_true", help="benchmark the exported model")
    parser.add_argument("--frames", type=int, default=20, help="frames timed by --verify")
    args = parser.parse_args()

    vision = load_vision_config(args.config)
    configured_size = vision.get("inference_imgsz")
    try:
        weights = resolve_model_path(
            args.model or str(vision.get("model_path") or _DEFAULT_MODEL), args.config
        )
        requested = args.imgsz if args.imgsz is not None else configured_size
        imgsz = parse_imgsz(None if requested is None else str(requested))
    except (FileNotFoundError, ValueError, argparse.ArgumentTypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    precision = "FP32" if args.no_half else "FP16"
    shape = "static" if args.static else "dynamic"
    print(f"Exporting {weights} to OpenVINO IR at {imgsz} px ({precision}, {shape} shape)...")
    output = export(weights, imgsz, half=not args.no_half, dynamic=not args.static)
    print(f"Exported: {output}")

    if args.verify:
        verify(output, imgsz, max(1, args.frames))

    print("\nSet in mission_config.json (or the GCS parameter sheet):")
    print(json.dumps(
        {"vision": {
            "model_path": output,
            "inference_imgsz": None if imgsz == _NATIVE_IMGSZ else imgsz,
        }},
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
