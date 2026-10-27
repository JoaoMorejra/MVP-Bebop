"""Tests for scripts/offline_tiles.py: tile planning, coverage and CLI contracts."""

import importlib.util
import io
import json
import math
import pathlib
import socket
from typing import List, Optional

import pytest
from PIL import Image

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "offline_tiles.py"
_spec = importlib.util.spec_from_file_location("offline_tiles", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ot)

LAT = -23.6488913
LNG = -46.7187124
UNION_COUNTS_1920 = {15: 121, 16: 110, 17: 110, 18: 121, 19: 119, 20: 119}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _deny(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", _deny)
    monkeypatch.setattr(socket, "create_connection", _deny)
    monkeypatch.setattr(socket, "getaddrinfo", _deny)


def _ts_project(lat: float, lng: float, zoom: int):
    n = 2 ** zoom
    x = ((lng + 180) / 360) * n
    lat_rad = lat * math.pi / 180
    y = ((1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2) * n
    return x, y


def _jpeg(color=(40, 90, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _touch(root: pathlib.Path, key, payload: Optional[bytes] = None) -> None:
    payload = _jpeg() if payload is None else payload
    path = root / str(key.z) / str(key.x) / f"{key.y}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


@pytest.mark.parametrize("zoom", [17, 20])
def test_project_matches_known_tile(zoom: int) -> None:
    ex, ey = _ts_project(LAT, LNG, zoom)
    x, y = ot.project(LAT, LNG, zoom)
    assert x == pytest.approx(ex, abs=1e-6)
    assert y == pytest.approx(ey, abs=1e-6)


def test_required_tiles_square_and_contains_base() -> None:
    for zoom in range(17, 21):
        tiles = ot.required_tiles(LAT, LNG, [zoom], 1280)
        xs = sorted({t.x for t in tiles})
        ys = sorted({t.y for t in tiles})
        assert xs == list(range(xs[0], xs[-1] + 1))
        assert ys == list(range(ys[0], ys[-1] + 1))
        assert len(tiles) == len(xs) * len(ys)
        assert len(xs) == len(ys)
        cx, cy = ot.project(LAT, LNG, zoom)
        assert ot.TileKey(zoom, math.floor(cx), math.floor(cy)) in tiles
        half = 1280 / 2 + 256
        assert xs[0] <= math.floor(cx - half / 256)
        assert xs[-1] >= math.floor(cx + half / 256)
        assert set(ot.plan_tiles(LAT, LNG, [zoom], 1280)) <= set(tiles)
        assert len(tiles) >= 49


def test_plan_formula_reference_is_a_lower_bound() -> None:
    expected = {15: 121, 16: 100, 17: 110, 18: 121, 19: 100, 20: 100}
    plan = ot.plan_tiles(LAT, LNG, list(expected), 1920)
    assert {z: sum(1 for t in plan if t.z == z) for z in expected} == expected
    full = ot.required_tiles(LAT, LNG, list(expected), 1920)
    assert set(plan) <= set(full)


def test_required_counts_match_documented_reference() -> None:
    expected = UNION_COUNTS_1920
    tiles = ot.required_tiles(LAT, LNG, list(expected), 1920)
    got = {z: sum(1 for t in tiles if t.z == z) for z in expected}
    assert got == expected


def _loop_tiles(lat, lng, zooms, width, height):
    """Mirror of fixedView/tileZoom and the `tiles` loop of TacticalMap.tsx."""
    out = set()
    max_zoom = max(zooms)
    steps = int((22 - ot.MIN_DISPLAY_ZOOM) / 0.5)
    for i in range(steps + 1):
        display = ot.MIN_DISPLAY_ZOOM + 0.5 * i
        z = min(math.floor(display), max_zoom)
        if z not in zooms:
            continue
        tile_size = 256 * 2 ** (display - z)
        cx, cy = _ts_project(lat, lng, z)
        cols = math.ceil(width / tile_size) + 2
        rows = math.ceil(height / tile_size) + 2
        ox = math.floor(cx) - cols // 2
        oy = math.floor(cy) - rows // 2
        n = 2 ** z
        for r in range(rows):
            for c in range(cols):
                tx, ty = ox + c, oy + r
                if 0 <= tx < n and 0 <= ty < n:
                    out.add(ot.TileKey(z, tx, ty))
    return out


ANCHORS = [(LAT, LNG), (-23.6488913 + 0.00031, -46.7187124 + 0.00017), (-23.6491, -46.7185), (0.0001, 0.0001), (47.3, 8.5)]


@pytest.mark.parametrize("viewport", [1280, 1920, 2560])
def test_required_tiles_cover_tactical_map_loop(viewport: int) -> None:
    zooms = list(range(15, 21))
    for lat, lng in ANCHORS:
        required = set(ot.required_tiles(lat, lng, zooms, viewport))
        widths = sorted(set(range(640, viewport + 1, 13)) | {640, viewport})
        for width in widths:
            for height in (min(width, 360), width, viewport):
                missing = _loop_tiles(lat, lng, zooms, width, height) - required
                assert not missing, (viewport, lat, lng, width, height, sorted(missing)[:3])


def test_verify_uses_union_and_manifest_viewport(tmp_path: pathlib.Path, anchor_file: pathlib.Path) -> None:
    root = tmp_path / "tiles"
    zooms = list(range(15, 21))
    plan = set(ot.plan_tiles(LAT, LNG, zooms, 1920))
    required = ot.required_tiles(LAT, LNG, zooms, 1920)
    extra = [k for k in required if k not in plan]
    assert extra
    for key in required:
        if key != extra[0]:
            _touch(root, key)
    ot.write_manifest(root, required, LAT, LNG, 1920, source="s", license_name="l", attribution="a")
    assert json.loads((root / "manifest.json").read_text(encoding="utf-8"))["viewportPx"] == 1920
    args = ["verify", "--root", str(root), "--anchor", str(anchor_file), "--zooms", "15-20", "--viewport-px", "1920"]
    assert ot.main(args) == 1
    _touch(root, extra[0])
    assert ot.main(args) == 0


def test_required_tiles_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        ot.required_tiles(LAT, LNG, [], 1280)
    with pytest.raises(ValueError):
        ot.required_tiles(LAT, LNG, [17], 0)
    with pytest.raises(ValueError):
        ot.required_tiles(95.0, LNG, [17], 1280)
    with pytest.raises(TypeError):
        ot.required_tiles(LAT, LNG, [17.5], 1280)


def test_verify_reports_missing(tmp_path: pathlib.Path) -> None:
    required = ot.required_tiles(LAT, LNG, [17, 18], 1280)
    half = required[::2]
    for key in half:
        _touch(tmp_path, key)
    missing = ot.verify_coverage(tmp_path, required)
    assert missing == [k for k in required if k not in set(half)]
    for key in required:
        _touch(tmp_path, key)
    assert ot.verify_coverage(tmp_path, required) == []


def test_manifest_schema(tmp_path: pathlib.Path) -> None:
    required = ot.required_tiles(LAT, LNG, [18, 19], 1280)
    for key in required:
        _touch(tmp_path, key)
    path = ot.write_manifest(
        tmp_path, required, LAT, LNG, 1280, source="s", license_name="l", attribution="a"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {
        "schema", "source", "license", "attribution", "anchor",
        "minZoom", "maxZoom", "viewportPx", "count", "bytes",
    }
    assert data["schema"] == "bmg.tiles.v1"
    assert data["count"] == len(required)
    assert data["bytes"] == len(_jpeg()) * len(required)
    assert data["anchor"] == {"lat": LAT, "lng": LNG}
    assert (data["minZoom"], data["maxZoom"], data["viewportPx"]) == (18, 19, 1280)


def test_xyz_reads_key_from_env_only(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.delenv("BMG_TILE_KEY", raising=False)

    def _fail(*args, **kwargs):
        raise AssertionError("connection attempted")

    monkeypatch.setattr(socket, "socket", _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)
    code = ot.main([
        "xyz", "--url-template", "https://example.invalid/{z}/{x}/{y}.jpg?key={key}",
        "--root", str(tmp_path), "--source", "s", "--license", "l", "--attribution", "a",
    ])
    assert code == 2
    assert not list(tmp_path.iterdir())


def test_raster_exits_2_without_gdal(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys) -> None:
    monkeypatch.setattr(ot.shutil, "which", lambda name: None)
    src = tmp_path / "ortho.tif"
    src.write_bytes(b"")
    code = ot.main([
        "raster", "--src", str(src), "--root", str(tmp_path / "out"),
        "--source", "s", "--license", "l", "--attribution", "a",
    ])
    assert code == 2
    assert "gdalwarp" in capsys.readouterr().err


def test_recode_keeps_only_required_and_writes_jpeg(tmp_path: pathlib.Path) -> None:
    required = ot.required_tiles(LAT, LNG, [18], 1280)
    keep = required[0]
    extra = ot.TileKey(18, keep.x + 1000, keep.y)
    src = tmp_path / "src"
    for key in (keep, extra):
        path = src / str(key.z) / str(key.x) / f"{key.y}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (256, 256), (10, 120, 30, 255)).save(path)
    dst = tmp_path / "dst"
    written = ot.recode_tiles(src, dst, required)
    assert written == [keep]
    out = dst / "18" / str(keep.x) / f"{keep.y}.jpg"
    with Image.open(out) as img:
        assert img.format == "JPEG" and img.size == (256, 256)
    assert not (dst / "18" / str(extra.x)).exists()


def test_verify_cli_exit_codes(tmp_path: pathlib.Path) -> None:
    anchor = tmp_path / "anchor.json"
    anchor.write_text(json.dumps({"latitude": LAT, "longitude": LNG}), encoding="utf-8")
    root = tmp_path / "tiles"
    args = ["verify", "--root", str(root), "--anchor", str(anchor), "--zooms", "18-18"]
    assert ot.main(args) == 1
    required: List = ot.required_tiles(LAT, LNG, [18], 1280)
    for key in required:
        _touch(root, key)
    assert ot.main(args) == 1
    ot.write_manifest(root, required, LAT, LNG, 1280, source="s", license_name="l", attribution="a")
    assert ot.main(args) == 0


def test_plan_prints_totals(capsys) -> None:
    assert ot.main(["plan", "--zooms", "17-18"]) == 0
    out = capsys.readouterr().out
    assert "z17" in out and "z18" in out and "total" in out


def _no_part(root: pathlib.Path) -> bool:
    return not list(root.rglob("*.part"))


def test_atomic_write_interrupted_leaves_nothing(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "a" / "b.jpg"

    def _writer(tmp: pathlib.Path) -> None:
        tmp.write_bytes(b"partial")
        raise OSError("disk")

    with pytest.raises(OSError):
        ot._atomic_write(target, _writer)
    assert not target.exists()
    assert _no_part(tmp_path)


def test_store_jpeg_replace_failure_leaves_no_part(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    def _boom(src, dst):
        raise OSError("replace")

    monkeypatch.setattr(ot.os, "replace", _boom)
    target = tmp_path / "18" / "1" / "2.jpg"
    with pytest.raises(OSError):
        ot._store_jpeg(_jpeg(), target)
    assert not target.exists()
    assert _no_part(tmp_path)


def test_manifest_write_is_atomic(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    required = ot.required_tiles(LAT, LNG, [18], 1280)
    for key in required:
        _touch(tmp_path, key)
    monkeypatch.setattr(ot.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        ot.write_manifest(tmp_path, required, LAT, LNG, 1280, source="s", license_name="l", attribution="a")
    assert not (tmp_path / "manifest.json").exists()
    assert _no_part(tmp_path)


def test_truncated_tile_is_missing(tmp_path: pathlib.Path) -> None:
    required = ot.required_tiles(LAT, LNG, [18], 1280)
    for key in required:
        _touch(tmp_path, key)
    bad = required[3]
    tile = ot.tile_path(tmp_path, bad)
    tile.write_bytes(tile.read_bytes()[:200])
    assert ot.verify_coverage(tmp_path, required) == [bad]


def _xyz_args(root: pathlib.Path, anchor: pathlib.Path, template: str, *extra: str) -> List[str]:
    return [
        "xyz", "--url-template", template, "--root", str(root), "--anchor", str(anchor),
        "--zooms", "18-18", "--source", "s", "--license", "l", "--attribution", "a", *extra,
    ]


@pytest.fixture
def anchor_file(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "anchor.json"
    path.write_text(json.dumps({"latitude": LAT, "longitude": LNG}), encoding="utf-8")
    return path


GOOD_TEMPLATE = "https://example.invalid/{z}/{x}/{y}.jpg?key={key}"


def test_xyz_resume_refetches_truncated_tile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, anchor_file: pathlib.Path
) -> None:
    root = tmp_path / "tiles"
    required = ot.required_tiles(LAT, LNG, [18], 1280)
    for key in required:
        _touch(root, key)
    bad = required[5]
    tile = ot.tile_path(root, bad)
    tile.write_bytes(tile.read_bytes()[:150])
    calls: List[str] = []

    def _fake(url: str) -> bytes:
        calls.append(url)
        return _jpeg((1, 2, 3))

    monkeypatch.setenv("BMG_TILE_KEY", "k")
    monkeypatch.setattr(ot, "_fetch_tile", _fake)
    monkeypatch.setattr(ot.time, "sleep", lambda s: None)
    assert ot.main(_xyz_args(root, anchor_file, GOOD_TEMPLATE)) == 0
    assert len(calls) == 1 and f"/{bad.z}/{bad.x}/{bad.y}.jpg" in calls[0]
    assert ot.verify_coverage(root, required) == []
    assert _no_part(root)


@pytest.mark.parametrize(
    "template",
    [
        "https://example.invalid/{z}/{x}/{y}.jpg?key={key}&s={subdomain}",
        "https://example.invalid/{z}/{y}.jpg?key={key}",
        "http://example.invalid/{z}/{x}/{y}.jpg?key={key}",
        "https://example.invalid/{z}/{x}/{y}.jpg?key={key",
    ],
)
def test_xyz_rejects_bad_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, anchor_file: pathlib.Path, template: str, capsys
) -> None:
    monkeypatch.setenv("BMG_TILE_KEY", "k")

    def _fail(url: str) -> bytes:
        raise AssertionError("request attempted")

    monkeypatch.setattr(ot, "_fetch_tile", _fail)
    assert ot.main(_xyz_args(tmp_path / "t", anchor_file, template)) == 2
    assert "template" in capsys.readouterr().err


def test_xyz_rejects_non_finite_rate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, anchor_file: pathlib.Path) -> None:
    monkeypatch.setenv("BMG_TILE_KEY", "k")
    monkeypatch.setattr(ot, "_fetch_tile", lambda url: (_ for _ in ()).throw(AssertionError("request attempted")))
    for rate in ("nan", "inf"):
        assert ot.main(_xyz_args(tmp_path / "t", anchor_file, GOOD_TEMPLATE, "--rate", rate)) == 2


def test_fetch_honours_bounded_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    import email.message
    import urllib.error

    sleeps: List[float] = []
    monkeypatch.setattr(ot.time, "sleep", sleeps.append)
    state = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    def _open(url, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            hdr = email.message.Message()
            hdr["Retry-After"] = "9999"
            raise urllib.error.HTTPError(url, 429, "slow", hdr, None)
        return _Resp()

    monkeypatch.setattr(ot.urllib.request, "urlopen", _open)
    assert ot._fetch_tile("https://example.invalid/x") == b"ok"
    assert sleeps == [ot.MAX_RETRY_AFTER_S]


def test_fetch_retries_http_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.client

    monkeypatch.setattr(ot.time, "sleep", lambda s: None)
    state = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    def _open(url, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise http.client.IncompleteRead(b"")
        return _Resp()

    monkeypatch.setattr(ot.urllib.request, "urlopen", _open)
    assert ot._fetch_tile("https://example.invalid/x") == b"ok"


def test_verify_checks_manifest_provenance_and_count(tmp_path: pathlib.Path, anchor_file: pathlib.Path) -> None:
    root = tmp_path / "tiles"
    required = ot.required_tiles(LAT, LNG, [18], 1280)
    for key in required:
        _touch(root, key)
    ot.write_manifest(root, required, LAT, LNG, 1280, source="s", license_name="l", attribution="a")
    args = ["verify", "--root", str(root), "--anchor", str(anchor_file), "--zooms", "18-18"]
    assert ot.main(args) == 0
    manifest_path = root / "manifest.json"
    good = json.loads(manifest_path.read_text(encoding="utf-8"))
    for field, value in (("license", " "), ("source", ""), ("attribution", 3), ("count", len(required) - 1)):
        manifest_path.write_text(json.dumps({**good, field: value}), encoding="utf-8")
        assert ot.main(args) == 1, field


WMS_ENDPOINT = "https://wms.invalid/geoserver/geoportal/wms"


def _wms_args(root: pathlib.Path, anchor: pathlib.Path, *extra: str, endpoint: str = WMS_ENDPOINT) -> List[str]:
    return [
        "wms", "--endpoint", endpoint, "--layer", "geoportal:ORTO", "--root", str(root),
        "--anchor", str(anchor), "--zooms", "18-18", "--source", "s", "--license", "l",
        "--attribution", "a", *extra,
    ]


def test_tile_bbox_3857_contains_base_and_has_tile_extent() -> None:
    half = 20037508.342789244
    for zoom in (17, 20):
        x, y = ot.project(LAT, LNG, zoom)
        key = ot.TileKey(zoom, int(x), int(y))
        minx, miny, maxx, maxy = ot.tile_bbox_3857(key)
        size = 2 * half / 2 ** zoom
        assert maxx - minx == pytest.approx(size, rel=1e-12)
        assert maxy - miny == pytest.approx(size, rel=1e-12)
        mx = math.radians(LNG) * 6378137.0
        my = math.log(math.tan(math.pi / 4 + math.radians(LAT) / 2)) * 6378137.0
        assert minx <= mx <= maxx and miny <= my <= maxy


def test_wms_url_requests_one_exact_tile() -> None:
    import urllib.parse

    key = ot.TileKey(19, 191233, 289412)
    url = ot.build_wms_url(WMS_ENDPOINT, "geoportal:ORTO", key)
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == WMS_ENDPOINT
    assert query["request"] == ["GetMap"] and query["layers"] == ["geoportal:ORTO"]
    assert query["srs"] == ["EPSG:900913"] and query["format"] == ["image/jpeg"]
    assert query["width"] == ["256"] and query["height"] == ["256"]
    assert [float(v) for v in query["bbox"][0].split(",")] == list(ot.tile_bbox_3857(key))


@pytest.mark.parametrize("endpoint", ["http://wms.invalid/wms", "ftp://wms.invalid/wms", "wms.invalid/wms"])
def test_wms_rejects_non_https_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, anchor_file: pathlib.Path, endpoint: str
) -> None:
    monkeypatch.setattr(ot, "_fetch_tile", lambda url: (_ for _ in ()).throw(AssertionError("fetched")))
    assert ot.main(_wms_args(tmp_path / "t", anchor_file, endpoint=endpoint)) == 2


def test_wms_fetches_only_missing_tiles_and_writes_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, anchor_file: pathlib.Path
) -> None:
    root = tmp_path / "tiles"
    required = ot.required_tiles(LAT, LNG, [18], 1280)
    _touch(root, required[0])
    calls: List[str] = []

    def _fake(url: str) -> bytes:
        calls.append(url)
        return _jpeg((9, 9, 9))

    monkeypatch.setattr(ot, "_fetch_tile", _fake)
    monkeypatch.setattr(ot.time, "sleep", lambda s: None)
    assert ot.main(_wms_args(root, anchor_file)) == 0
    assert len(calls) == len(required) - 1
    assert ot.verify_coverage(root, required) == []
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["count"] == len(required) and manifest["source"] == "s"
    assert _no_part(root)


def test_wms_non_image_response_exits_2_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, anchor_file: pathlib.Path
) -> None:
    root = tmp_path / "tiles"
    monkeypatch.setattr(ot, "_fetch_tile", lambda url: b"<ServiceExceptionReport/>")
    monkeypatch.setattr(ot.time, "sleep", lambda s: None)
    assert ot.main(_wms_args(root, anchor_file)) == 2
    assert not list(root.rglob("*.jpg")) if root.exists() else True
