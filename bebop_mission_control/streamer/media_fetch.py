"""Download of the native 14 MP photos after touchdown.

The Bebop 2 serves its internal storage over anonymous FTP on
``192.168.42.1:21``; photos taken by ``RecordPictureV2`` land in
``internal_000/Bebop_2/media`` as ``Bebop_2_<aircraft clock>_<id>.jpg``. The
aircraft clock is never set by the driver, so the names order the photos but do
not date them. Pairing is therefore by order: the newest photos not yet on the
station, oldest first, go to the acknowledged sidecars still missing one, in
request order. When the aircraft holds fewer new photos than there are pending
sidecars, the latest requests are the ones paired.

Each photo is written to ``<output_dir>/native/`` through a dot-prefixed
temporary file and an atomic rename, and its sidecar's ``native_photo`` gains
``remote_path``, ``local_path`` and ``fetched_at_utc``.

Usage (inside ``nectar-activate``)::

    python3 bebop_mission_control/streamer/media_fetch.py --output-dir <dir>
"""

from __future__ import annotations

import argparse
import ftplib
import glob
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Final, List, Optional, Sequence, Tuple

logger = logging.getLogger("MediaFetch")

BEBOP_FTP_HOST: Final[str] = "192.168.42.1"
BEBOP_FTP_PORT: Final[int] = 21
#: Photo directory relative to the FTP root.
BEBOP_MEDIA_DIR: Final[str] = "internal_000/Bebop_2/media"
#: Connect and per-operation timeout, seconds. A 14 MP JPEG is 4-6 MB; over the
#: Bebop's 802.11ac link a single transfer finishes well within it.
FTP_TIMEOUT_SEC: Final[float] = 20.0
#: Photo formats ``PictureFormatSelection`` can produce.
PHOTO_EXTENSIONS: Final[Tuple[str, ...]] = (".jpg", ".jpeg", ".dng")
#: Subdirectory of the output directory holding the downloads.
NATIVE_SUBDIR: Final[str] = "native"

FtpFactory = Callable[[], ftplib.FTP]
_default_ftp: FtpFactory = ftplib.FTP


def _pending_sidecars(output_dir: str) -> Tuple[List[Tuple[str, Dict[str, Any]]], set]:
    """Acknowledged sidecars without a download, and remote names already paired."""
    pending: List[Tuple[str, Dict[str, Any]]] = []
    paired = set()
    for path in sorted(glob.glob(os.path.join(output_dir, "accident_metadata_*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable sidecar %s: %s", path, exc)
            continue
        native = payload.get("native_photo") if isinstance(payload, dict) else None
        if not isinstance(native, dict):
            continue
        if native.get("remote_path"):
            paired.add(os.path.basename(str(native["remote_path"])))
        elif native.get("acknowledged") is True:
            pending.append((path, payload))
    pending.sort(key=lambda item: str(item[1]["native_photo"].get("requested_at_utc") or ""))
    return pending, paired


def _write_json_atomic(path: str, payload: Dict[str, Any]) -> None:
    directory = os.path.dirname(path) or "."
    base, extension = os.path.splitext(os.path.basename(path))
    temporary = os.path.join(directory, f".{base}.tmp{extension}")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    os.replace(temporary, path)


def _download(ftp: ftplib.FTP, name: str, destination: str) -> None:
    directory = os.path.dirname(destination)
    temporary = os.path.join(directory, f".{os.path.basename(destination)}.part")
    try:
        with open(temporary, "wb") as handle:
            ftp.retrbinary(f"RETR {name}", handle.write)
        os.replace(temporary, destination)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


def fetch_native_photos(
    output_dir: str,
    *,
    host: str = BEBOP_FTP_HOST,
    port: int = BEBOP_FTP_PORT,
    remote_dir: str = BEBOP_MEDIA_DIR,
    ftp_factory: Optional[FtpFactory] = None,
    timeout_sec: float = FTP_TIMEOUT_SEC,
) -> List[Dict[str, str]]:
    """Download the photos the aircraft acknowledged and update their sidecars.

    Parameters
    ----------
    output_dir : str
        Mission output directory holding the ``accident_metadata_*.json``
        sidecars.
    host, port : str, int
        Aircraft FTP endpoint.
    remote_dir : str
        Photo directory relative to the FTP root.
    ftp_factory : callable, optional
        Returns an unconnected :class:`ftplib.FTP`; defaults to the module's
        ``_default_ftp``.
    timeout_sec : float
        Connect and per-operation timeout, seconds.

    Returns
    -------
    list of dict
        One ``{"sidecar", "remote_path", "local_path"}`` per photo downloaded,
        in request order. Empty when nothing was pending; no connection is
        opened in that case.

    Raises
    ------
    TypeError
        If ``output_dir`` is not a string.
    ValueError
        If ``output_dir`` is not a directory.
    OSError, ftplib.Error
        On a connection or transfer failure. Sidecars already updated stay
        updated; the one in flight is untouched.
    """
    if not isinstance(output_dir, str):
        raise TypeError(f"output_dir must be a string, got {type(output_dir).__name__}.")
    if not os.path.isdir(output_dir):
        raise ValueError(f"output_dir is not a directory: {output_dir!r}.")

    pending, paired = _pending_sidecars(output_dir)
    if not pending:
        return []

    ftp = (ftp_factory or _default_ftp)()
    fetched: List[Dict[str, str]] = []
    try:
        ftp.connect(host, port, timeout_sec)
        ftp.login()
        ftp.cwd(remote_dir)
        names = sorted(
            os.path.basename(name)
            for name in ftp.nlst()
            if os.path.splitext(name)[1].lower() in PHOTO_EXTENSIONS
        )
        fresh = [name for name in names if name not in paired]
        count = min(len(fresh), len(pending))
        if count < len(pending):
            logger.warning(
                "%d acknowledged photo(s) pending, %d new on the aircraft; pairing the latest.",
                len(pending),
                len(fresh),
            )
        if count == 0:
            return []

        local_dir = os.path.join(output_dir, NATIVE_SUBDIR)
        os.makedirs(local_dir, exist_ok=True)
        for (path, payload), name in zip(pending[-count:], fresh[-count:]):
            destination = os.path.join(local_dir, name)
            _download(ftp, name, destination)
            remote_path = f"{remote_dir}/{name}"
            local_path = os.path.join(NATIVE_SUBDIR, name)
            payload["native_photo"].update(
                remote_path=remote_path,
                local_path=local_path,
                fetched_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            _write_json_atomic(path, payload)
            logger.info("Native photo %s -> %s.", remote_path, destination)
            fetched.append(
                {"sidecar": os.path.basename(path), "remote_path": remote_path, "local_path": local_path}
            )
    finally:
        try:
            ftp.quit()
        except (OSError, EOFError, ftplib.Error):
            ftp.close()
    return fetched


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print ``{"fetched": [...]}`` as JSON. Returns 1 on a connection failure."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--host", default=BEBOP_FTP_HOST)
    parser.add_argument("--port", type=int, default=BEBOP_FTP_PORT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s", stream=sys.stderr)
    try:
        fetched = fetch_native_photos(args.output_dir, host=args.host, port=args.port)
    except (OSError, EOFError, ftplib.Error) as exc:
        print(json.dumps({"fetched": [], "error": str(exc)}))
        return 1
    print(json.dumps({"fetched": fetched}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
