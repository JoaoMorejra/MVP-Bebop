#!/usr/bin/env python3
"""AprilTag / ArUco detection test utility.

Quick validation tool for the landing-pad marker. Runs against a static image,
a live webcam, or the Bebop camera topic and prints detection telemetry to the
terminal.

Usage examples::

    # Test on the real photo of the pad
    python3 test_aruco_tag.py --sample

    # Test on any image file
    python3 test_aruco_tag.py --image /path/to/photo.png

    # Live webcam (device 0)
    python3 test_aruco_tag.py --webcam 0

    # Save annotated output
    python3 test_aruco_tag.py --sample --output /tmp/detected.png
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional, Union

import cv2
import cv2.aruco as aruco_mod
import numpy as np

# ---------------------------------------------------------------------------
# Default path for sample landing-pad validation image.
# ---------------------------------------------------------------------------
_SAMPLE_IMAGE = os.environ.get(
    "ARUCO_SAMPLE_IMAGE",
    os.path.join(os.path.dirname(__file__), "..", "resource", "sample_tag.png"),
)

# ---------------------------------------------------------------------------
# Defaults matching the mission configuration.
# ---------------------------------------------------------------------------
_DEFAULT_DICT = "DICT_APRILTAG_36h11"
_DEFAULT_ID = 8
_DEFAULT_SIZE = 0.20  # metres


def resolve_dict(marker_dict: Union[int, str]) -> int:
    """Resolve a dictionary name/order/enum to an OpenCV constant."""
    # Try importing from the SDK first.
    try:
        from nectar.vision.algorithms.markers.aruco import resolve_aruco_dict
        return resolve_aruco_dict(marker_dict)
    except ImportError:
        pass

    # Fallback: direct attribute lookup.
    if isinstance(marker_dict, str):
        if hasattr(aruco_mod, marker_dict):
            return getattr(aruco_mod, marker_dict)
        prefixed = f"DICT_{marker_dict.upper()}"
        if hasattr(aruco_mod, prefixed):
            return getattr(aruco_mod, prefixed)
    if isinstance(marker_dict, int):
        if marker_dict in {4, 5, 6, 7}:
            return getattr(aruco_mod, f"DICT_{marker_dict}X{marker_dict}_1000")
        return marker_dict
    raise ValueError(f"Cannot resolve dictionary: {marker_dict!r}")


def detect_and_annotate(
    img: np.ndarray,
    dict_key: int,
    target_id: int,
    tag_size: float,
    camera_matrix: Optional[np.ndarray] = None,
    camera_distortion: Optional[np.ndarray] = None,
) -> dict:
    """Run detection + pose on a single frame and annotate it in place."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dictionary = aruco_mod.getPredefinedDictionary(dict_key)
    params = aruco_mod.DetectorParameters()
    detector = aruco_mod.ArucoDetector(dictionary, params)
    corners, ids, _ = detector.detectMarkers(gray)

    result = {"detected": False, "id": None, "match": False,
              "tvec": None, "distance": None, "yaw": None}

    if ids is None or len(ids) == 0:
        return result

    marker_id = int(ids[0][0])
    result["detected"] = True
    result["id"] = marker_id
    result["match"] = marker_id == target_id

    aruco_mod.drawDetectedMarkers(img, corners, ids)

    # Pose estimation
    if camera_matrix is not None and camera_distortion is not None:
        half = tag_size / 2.0
        obj_pts = np.array(
            [[-half, half, 0], [half, half, 0],
             [half, -half, 0], [-half, -half, 0]], dtype=np.float32)
        img_pts = np.asarray(corners[0], dtype=np.float32).reshape(4, 2)
        solved, rvec, tvec = cv2.solvePnP(
            obj_pts, img_pts,
            camera_matrix.astype(np.float64),
            camera_distortion.astype(np.float64),
            flags=cv2.SOLVEPNP_IPPE_SQUARE,
        )
        if solved:
            tvec = tvec.ravel()
            result["tvec"] = tvec
            result["distance"] = float(np.linalg.norm(tvec))

            # Yaw from corners
            tl, tr = corners[0][0][0], corners[0][0][1]
            yaw = float(np.degrees(np.arctan2(tr[1] - tl[1], tr[0] - tl[0])))
            if yaw < 0:
                yaw += 360.0
            result["yaw"] = yaw

            cv2.drawFrameAxes(
                img,
                camera_matrix.astype(np.float64),
                camera_distortion.astype(np.float64),
                rvec, tvec.reshape(3, 1), tag_size,
            )

    # HUD overlay
    h, w = img.shape[:2]
    status = "MATCH" if result["match"] else "MISMATCH"
    line1 = f"ID: {result['id']} [{status}] target={target_id}"
    if result["tvec"] is not None:
        t = result["tvec"]
        line2 = (f"DIST: {result['distance']:.3f}m  "
                 f"X:{t[0]:.3f} Y:{t[1]:.3f} Z:{t[2]:.3f}  "
                 f"YAW:{result['yaw']:.1f} deg")
    else:
        line2 = "POSE: no calibration"

    cv2.putText(img, line1, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (0, 255, 0) if result["match"] else (0, 0, 255), 2)
    cv2.putText(img, line2, (10, 60), cv2.FONT_HERSHEY_SIMPLEX,
                0.6, (255, 255, 255), 2)

    return result


def load_calibration():
    """Try loading Nectar camera calibration; return (matrix, dist) or Nones."""
    try:
        from nectar.vision.camera.calibration import CameraCalibration
        return CameraCalibration.load_calibration()
    except Exception:
        return None, None


def print_result(result: dict) -> None:
    """Pretty-print detection result to terminal."""
    if not result["detected"]:
        print("  [NO MARKER] No marker detected")
        return
    status = "[MATCH]" if result["match"] else "[MISMATCH]"
    print(f"  {status}  ID={result['id']}")
    if result["tvec"] is not None:
        t = result["tvec"]
        print(f"  Distance : {result['distance']:.3f} m")
        print(f"  Position : X={t[0]:.4f}  Y={t[1]:.4f}  Z={t[2]:.4f} m")
        print(f"  Yaw      : {result['yaw']:.1f}°")
    else:
        print("  Pose     : unavailable (no calibration)")


def run_image(path: str, args) -> None:
    """Process a single image file."""
    img = cv2.imread(path)
    if img is None:
        print(f"ERROR: cannot read image {path!r}", file=sys.stderr)
        sys.exit(1)

    dict_key = resolve_dict(args.dict)
    cm, cd = load_calibration()

    print(f"\n{'='*60}")
    print(f"  Image : {path}")
    print(f"  Dict  : {args.dict} (enum {dict_key})")
    print(f"  Target: ID {args.id}, size {args.size} m")
    print(f"{'='*60}")

    result = detect_and_annotate(img, dict_key, args.id, args.size, cm, cd)
    print_result(result)

    if args.output:
        cv2.imwrite(args.output, img)
        print(f"\n  Annotated image saved to: {args.output}")

    # Show if we have a display
    if os.environ.get("DISPLAY"):
        cv2.imshow("AprilTag Test", img)
        print("\n  Press any key to close...")
        cv2.waitKey(0)
        cv2.destroyAllWindows()


def run_webcam(device: int, args) -> None:
    """Live detection loop from a webcam."""
    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        print(f"ERROR: cannot open webcam {device}", file=sys.stderr)
        sys.exit(1)

    dict_key = resolve_dict(args.dict)
    cm, cd = load_calibration()

    print(f"\n  Webcam {device} — Dict: {args.dict} — Target ID: {args.id}")
    print("  Press 'q' to quit, 's' to save a snapshot\n")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        result = detect_and_annotate(frame, dict_key, args.id, args.size, cm, cd)

        cv2.imshow("AprilTag Live", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("s") and args.output:
            cv2.imwrite(args.output, frame)
            print(f"  Snapshot saved to {args.output}")

    cap.release()
    cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(
        description="AprilTag / ArUco marker detection test utility.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--image", type=str, help="Path to a static image file.")
    group.add_argument("--sample", action="store_true",
                       help="Use the sample landing-pad photo.")
    group.add_argument("--webcam", type=int, nargs="?", const=0, default=None,
                       help="Webcam device index (default: 0).")

    parser.add_argument("--dict", type=str, default=_DEFAULT_DICT,
                        help=f"Dictionary name or enum (default: {_DEFAULT_DICT}).")
    parser.add_argument("--id", type=int, default=_DEFAULT_ID,
                        help=f"Target marker ID (default: {_DEFAULT_ID}).")
    parser.add_argument("--size", type=float, default=_DEFAULT_SIZE,
                        help=f"Marker size in metres (default: {_DEFAULT_SIZE}).")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Path to save the annotated image.")

    args = parser.parse_args()

    if args.sample:
        if not os.path.isfile(_SAMPLE_IMAGE):
            print(f"ERROR: sample image not found at {_SAMPLE_IMAGE}", file=sys.stderr)
            sys.exit(1)
        run_image(_SAMPLE_IMAGE, args)
    elif args.image:
        run_image(args.image, args)
    elif args.webcam is not None:
        run_webcam(args.webcam, args)


if __name__ == "__main__":
    main()
