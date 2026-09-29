"""Bebop 2 camera intrinsics for metric marker pose estimation.

``nectar.vision.Aruco`` loads ``camera_matrix.txt`` and
``camera_distortion.txt`` from the Nectar calibration package at construction
and offers no way to point it elsewhere. That package ships the sample output of
``CameraCalibration`` for a different camera: its principal point sits near
(298.6, 308.5), 129 px left of and 68 px below the centre of the 856x480 Bebop
stream. A pose solved with it is smooth and confident and carries a fixed
angular bias of roughly 0.24 m lateral per metre of range, which is larger than
the whole landing tolerance.

The airframe's own calibration is installed by the driver package, which loads
it into ``camera_info`` through ``camera_info_manager``
(``ros2_bebop_driver/config/bebop2_camera_calib.yaml``, ``plumb_bob``, captured
at 856x480). This module reads that file in the ``camera_info`` YAML schema and
applies it to an already constructed detector, so the SDK class is used as-is
and only its intrinsics are replaced.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Final, Optional

import numpy as np

logger = logging.getLogger("CameraIntrinsics")

#: Package and relative path under its share directory, as declared by the
#: driver node's ``camera_calibration_file`` parameter default.
DRIVER_PACKAGE: Final[str] = "ros2_bebop_driver"
DRIVER_CALIBRATION_RELPATH: Final[str] = os.path.join("config", "bebop2_camera_calib.yaml")


@dataclass(frozen=True)
class CameraInfoCalibration:
    """Intrinsics read from a ``camera_info`` calibration file.

    Parameters
    ----------
    camera_matrix : np.ndarray
        3x3 pinhole matrix ``K`` in pixels.
    distortion : np.ndarray
        Distortion coefficients in OpenCV order (``plumb_bob``: k1, k2, p1, p2, k3).
    width : int
        Image width, in pixels, the calibration was captured at.
    height : int
        Image height, in pixels, the calibration was captured at.
    source : str
        Path of the file the values were read from, for the flight log.
    """

    camera_matrix: np.ndarray
    distortion: np.ndarray
    width: int
    height: int
    source: str


def _matrix_data(document: Any, key: str, rows: int, cols: Optional[int]) -> np.ndarray:
    """Extract ``document[key].data`` as a float64 array of the expected size.

    Raises
    ------
    ValueError
        If the entry is absent, not numeric, or of the wrong size.
    """
    entry = document.get(key) if isinstance(document, dict) else None
    data = entry.get("data") if isinstance(entry, dict) else None
    if not isinstance(data, (list, tuple)):
        raise ValueError(f"camera_info field {key!r} has no data list")
    try:
        values = np.asarray([float(v) for v in data], dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"camera_info field {key!r} is not numeric: {exc}") from exc
    if cols is not None and values.size != rows * cols:
        raise ValueError(f"camera_info field {key!r} holds {values.size} values, expected {rows * cols}")
    if not np.all(np.isfinite(values)):
        raise ValueError(f"camera_info field {key!r} contains non-finite values")
    return values.reshape(rows, cols) if cols is not None else values


def load_camera_info_yaml(path: str) -> CameraInfoCalibration:
    """Read a ``camera_info`` calibration YAML.

    Parameters
    ----------
    path : str
        File in the schema written by ``camera_calibration`` and read by
        ``camera_info_manager``.

    Returns
    -------
    CameraInfoCalibration
        Matrix, distortion and capture size.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    ValueError
        If the file is not a mapping, the matrix is not 3x3, the focal lengths
        are not positive, or the image size is missing.
    """
    import yaml

    with open(path, "r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise ValueError(f"{path} is not a camera_info mapping")

    camera_matrix = _matrix_data(document, "camera_matrix", 3, 3)
    distortion = _matrix_data(document, "distortion_coefficients", 1, None).ravel()
    if camera_matrix[0, 0] <= 0.0 or camera_matrix[1, 1] <= 0.0:
        raise ValueError(f"{path} has non-positive focal lengths")

    try:
        width = int(document["image_width"])
        height = int(document["image_height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path} does not state image_width and image_height") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"{path} states a non-positive image size {width}x{height}")

    return CameraInfoCalibration(camera_matrix, distortion, width, height, path)


def _package_share_directory(name: str) -> str:
    from ament_index_python.packages import get_package_share_directory

    return get_package_share_directory(name)


def resolve_driver_calibration_path() -> Optional[str]:
    """Locate the calibration installed by the Bebop driver package.

    Returns
    -------
    str or None
        Absolute path, or ``None`` when the driver package is not in the
        ament index or does not ship the file.
    """
    try:
        share = _package_share_directory(DRIVER_PACKAGE)
    except (LookupError, ValueError, ImportError) as exc:
        logger.debug("%s is not resolvable through the ament index: %s", DRIVER_PACKAGE, exc)
        return None
    path = os.path.join(share, DRIVER_CALIBRATION_RELPATH)
    return path if os.path.isfile(path) else None


def apply_driver_calibration(detector: Any) -> Optional[CameraInfoCalibration]:
    """Replace a detector's intrinsics with the Bebop 2 calibration.

    Parameters
    ----------
    detector : Any
        Object exposing ``camera_matrix`` and ``camera_distortion``, as
        ``nectar.vision.Aruco`` does.

    Returns
    -------
    CameraInfoCalibration or None
        The calibration applied, or ``None`` when none was found or it could
        not be read, in which case the detector is left untouched.
    """
    path = resolve_driver_calibration_path()
    if path is None:
        return None
    try:
        calibration = load_camera_info_yaml(path)
    except (OSError, ValueError) as exc:
        logger.warning("Bebop calibration at %s is unusable (%s); keeping the SDK intrinsics.", path, exc)
        return None

    detector.camera_matrix = calibration.camera_matrix.copy()
    detector.camera_distortion = calibration.distortion.copy()
    return calibration
