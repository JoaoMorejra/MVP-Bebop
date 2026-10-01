"""Native photo download after touchdown (4.7), against a stand-in FTP server.

The Bebop serves its internal storage on ``192.168.42.1:21`` and names photos
after its own clock, which nothing sets; the fetch therefore pairs photos to
acknowledged sidecars by order rather than by timestamp: the newest photos not
already on the station, oldest first, to the sidecars in request order.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "bebop_mission_control", "streamer", "media_fetch.py")
_spec = importlib.util.spec_from_file_location("media_fetch_under_test", _SCRIPT)
media_fetch = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = media_fetch
_spec.loader.exec_module(media_fetch)


class FakeFTP:
    instances = []

    def __init__(self, files, fail_connect=False):
        self.files = dict(files)
        self.fail_connect = fail_connect
        self.cwd_path = None
        self.closed = False
        self.retrieved = []

    def __call__(self):
        FakeFTP.instances.append(self)
        return self

    def connect(self, host, port, timeout):
        if self.fail_connect:
            raise OSError("No route to host")
        self.address = (host, port, timeout)

    def login(self):
        pass

    def cwd(self, path):
        self.cwd_path = path

    def nlst(self):
        return list(self.files)

    def retrbinary(self, command, callback):
        name = command.split(" ", 1)[1]
        self.retrieved.append(name)
        for start in range(0, len(self.files[name]), 4):
            callback(self.files[name][start : start + 4])

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True


def sidecar(directory, stamp, requested_at, acknowledged=True):
    path = directory / f"accident_metadata_{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "timestamp": stamp,
                "native_photo": {
                    "requested_at_utc": requested_at,
                    "acknowledged": acknowledged,
                    "event": "taken" if acknowledged else None,
                    "error": "ok" if acknowledged else None,
                    "remote_path": None,
                    "local_path": None,
                },
            }
        )
    )
    return path


PHOTOS = {
    "Bebop_2_19700101T000512+0000_A1.jpg": b"old-photo",
    "Bebop_2_19700101T001001+0000_B2.jpg": b"first-photo",
    "Bebop_2_19700101T001003+0000_C3.jpg": b"second-photo",
    "Bebop_2_19700101T001003+0000_C3.mp4": b"video",
}


def test_acknowledged_photos_are_downloaded_and_their_sidecars_updated(tmp_path):
    first = sidecar(tmp_path, "20260930_101000", "2026-09-30T13:10:00+00:00")
    second = sidecar(tmp_path, "20260930_101005", "2026-09-30T13:10:05+00:00")
    server = FakeFTP(PHOTOS)

    report = media_fetch.fetch_native_photos(str(tmp_path), ftp_factory=server)

    assert [item["remote_path"] for item in report] == [
        "internal_000/Bebop_2/media/Bebop_2_19700101T001001+0000_B2.jpg",
        "internal_000/Bebop_2/media/Bebop_2_19700101T001003+0000_C3.jpg",
    ]
    assert server.address == ("192.168.42.1", 21, media_fetch.FTP_TIMEOUT_SEC)
    assert server.closed
    native = json.loads(first.read_text())["native_photo"]
    assert native["remote_path"].endswith("B2.jpg")
    local = tmp_path / native["local_path"]
    assert local.read_bytes() == b"first-photo"
    assert json.loads(second.read_text())["native_photo"]["local_path"].endswith("C3.jpg")
    assert not list(tmp_path.glob("native/.*")), "a temporary download was left behind"


def test_a_second_run_downloads_nothing_again(tmp_path):
    sidecar(tmp_path, "20260930_101000", "2026-09-30T13:10:00+00:00")
    media_fetch.fetch_native_photos(str(tmp_path), ftp_factory=FakeFTP(PHOTOS))
    server = FakeFTP(PHOTOS)
    assert media_fetch.fetch_native_photos(str(tmp_path), ftp_factory=server) == []
    assert server.retrieved == []


def test_unacknowledged_sidecars_never_open_a_connection(tmp_path):
    sidecar(tmp_path, "20260930_101000", "2026-09-30T13:10:00+00:00", acknowledged=False)
    FakeFTP.instances.clear()
    assert media_fetch.fetch_native_photos(str(tmp_path), ftp_factory=FakeFTP(PHOTOS)) == []
    assert FakeFTP.instances == []


def test_fewer_photos_than_sidecars_pairs_the_latest_requests(tmp_path):
    early = sidecar(tmp_path, "20260930_101000", "2026-09-30T13:10:00+00:00")
    late = sidecar(tmp_path, "20260930_101005", "2026-09-30T13:10:05+00:00")
    media_fetch.fetch_native_photos(str(tmp_path), ftp_factory=FakeFTP({"Bebop_2_X.jpg": b"only"}))
    assert json.loads(early.read_text())["native_photo"]["local_path"] is None
    assert json.loads(late.read_text())["native_photo"]["remote_path"].endswith("Bebop_2_X.jpg")


def test_an_unreachable_aircraft_raises_and_leaves_the_sidecars_untouched(tmp_path):
    path = sidecar(tmp_path, "20260930_101000", "2026-09-30T13:10:00+00:00")
    before = path.read_text()
    with pytest.raises(OSError):
        media_fetch.fetch_native_photos(str(tmp_path), ftp_factory=FakeFTP(PHOTOS, fail_connect=True))
    assert path.read_text() == before


def test_the_cli_reports_json_and_exit_codes(tmp_path, monkeypatch, capsys):
    sidecar(tmp_path, "20260930_101000", "2026-09-30T13:10:00+00:00")
    monkeypatch.setattr(media_fetch, "_default_ftp", FakeFTP(PHOTOS))
    assert media_fetch.main(["--output-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["fetched"][0]["remote_path"].endswith("C3.jpg")
    monkeypatch.setattr(media_fetch, "_default_ftp", FakeFTP(PHOTOS, fail_connect=True))
    sidecar(tmp_path, "20260930_101009", "2026-09-30T13:10:09+00:00")
    assert media_fetch.main(["--output-dir", str(tmp_path)]) == 1
