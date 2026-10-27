#!/usr/bin/env python3
"""Offline satellite tile pipeline for the BMG tactical map.

Computes the XYZ tiles the tactical map can request around the fixed site
base, writes them to ``bebop_mission_control/public/tiles/{z}/{x}/{y}.jpg``
with a ``manifest.json``, and verifies coverage without network access.

Subcommands: ``plan``, ``raster``, ``xyz``, ``wms``, ``verify``.
Exit codes: 0 success, 1 incomplete coverage, 2 usage or network error.
"""

import argparse
import http.client
import io
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import string
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Final, List, NamedTuple, Optional, Sequence, Tuple

from PIL import Image

TILE_PX: Final[int] = 256
DEFAULT_VIEWPORT_PX: Final[int] = 1280
DEFAULT_ZOOMS: Final[str] = "16-20"
MIN_DISPLAY_ZOOM: Final[float] = 16.0
MAX_DISPLAY_ZOOM: Final[float] = 22.0
MAX_LATITUDE: Final[float] = 85.0511287798
MANIFEST_SCHEMA: Final[str] = "bmg.tiles.v1"
MANIFEST_NAME: Final[str] = "manifest.json"
KEY_ENV: Final[str] = "BMG_TILE_KEY"
JPEG_QUALITY: Final[int] = 80
FETCH_ATTEMPTS: Final[int] = 4
FETCH_TIMEOUT_S: Final[float] = 15.0
MAX_RETRY_AFTER_S: Final[float] = 30.0
TEMPLATE_FIELDS: Final[frozenset] = frozenset({"z", "x", "y", "key"})
TEMPLATE_REQUIRED: Final[frozenset] = frozenset({"z", "x", "y"})
WEB_MERCATOR_HALF_M: Final[float] = 20037508.342789244
WMS_SRS: Final[str] = "EPSG:900913"

_REPO_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
_BMG_ROOT: Final[pathlib.Path] = _REPO_ROOT / "bebop_mission_control"
DEFAULT_ROOT: Final[pathlib.Path] = _BMG_ROOT / "public" / "tiles"
DEFAULT_ANCHOR: Final[pathlib.Path] = _BMG_ROOT / "config" / "site-anchor.json"


class TileKey(NamedTuple):
    """XYZ tile address (y grows southward, as in ``project`` of mapView.ts)."""

    z: int
    x: int
    y: int


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    return value


def _require_real(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def project(lat: float, lng: float, zoom: int) -> Tuple[float, float]:
    """Project a coordinate to fractional Web Mercator tiles.

    Mirrors ``project()`` in ``bebop_mission_control/src/lib/mapView.ts``.

    Parameters
    ----------
    lat : float
        Latitude in degrees, within the Web Mercator limit.
    lng : float
        Longitude in degrees.
    zoom : int
        Tile zoom level, non-negative.

    Returns
    -------
    Tuple[float, float]
        Fractional tile coordinates ``(x, y)`` at ``zoom``.

    Raises
    ------
    TypeError
        If an argument has the wrong type.
    ValueError
        If ``zoom`` is negative or ``lat`` is outside the Mercator range.
    """
    lat = _require_real(lat, "lat")
    lng = _require_real(lng, "lng")
    zoom = _require_int(zoom, "zoom")
    if zoom < 0:
        raise ValueError("zoom must be >= 0")
    if abs(lat) > MAX_LATITUDE:
        raise ValueError(f"lat must be within +-{MAX_LATITUDE}")
    n = 2 ** zoom
    x = ((lng + 180.0) / 360.0) * n
    lat_rad = lat * math.pi / 180.0
    y = ((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0) * n
    return x, y


def plan_tiles(
    lat: float,
    lng: float,
    zooms: Sequence[int],
    viewport_px: int = DEFAULT_VIEWPORT_PX,
) -> List[TileKey]:
    """Tiles of the plan formula alone (documented reference, lower bound of ``required_tiles``).

    Per level, ``half = viewport_px / 2 + 256`` px and the tile range is
    ``floor(c - half / 256)`` to ``floor(c + half / 256)`` on both axes, with
    ``(cx, cy) = project(lat, lng, z)``; ranges are clipped to ``[0, 2^z)``.

    Parameters
    ----------
    lat, lng : float
        Base coordinate in degrees.
    zooms : Sequence[int]
        Non-empty sequence of tile zoom levels.
    viewport_px : int
        Largest map panel side the set must cover, in pixels.

    Returns
    -------
    List[TileKey]
        Tiles ordered by zoom, x, y, without duplicates.

    Raises
    ------
    TypeError
        If an argument has the wrong type.
    ValueError
        If ``zooms`` is empty, a zoom is negative or ``viewport_px`` is not positive.
    """
    viewport_px = _require_int(viewport_px, "viewport_px")
    if viewport_px <= 0:
        raise ValueError("viewport_px must be > 0")
    if len(zooms) == 0:
        raise ValueError("zooms must not be empty")
    half_tiles = (viewport_px / 2 + TILE_PX) / TILE_PX
    out: List[TileKey] = []
    for zoom in sorted({_require_int(z, "zoom") for z in zooms}):
        cx, cy = project(lat, lng, zoom)
        n = 2 ** zoom
        x0, x1 = math.floor(cx - half_tiles), math.floor(cx + half_tiles)
        y0, y1 = math.floor(cy - half_tiles), math.floor(cy + half_tiles)
        for x in range(max(x0, 0), min(x1, n - 1) + 1):
            for y in range(max(y0, 0), min(y1, n - 1) + 1):
                out.append(TileKey(zoom, x, y))
    return out


def map_loop_tiles(
    lat: float,
    lng: float,
    zooms: Sequence[int],
    viewport_px: int = DEFAULT_VIEWPORT_PX,
) -> List[TileKey]:
    """Tiles the ``tiles`` loop of ``TacticalMap.tsx`` can request.

    Mirrors ``fixedView``/``tileZoom`` (``mapView.ts``) and the loop: for each
    display zoom ``d`` from ``max(MIN_DISPLAY_ZOOM, min(zooms))`` to
    ``MAX_DISPLAY_ZOOM`` in 0.5 steps, ``z = min(floor(d), max(zooms))`` (kept
    only if ``z`` is in ``zooms``; overzoom from the deepest level included),
    ``tileSize = 256 * 2^(d - z)``, ``cols = ceil(W / tileSize) + 2`` and
    ``origin = floor(centre) - floor(cols / 2)``. The panel is taken as
    ``viewport_px`` on both axes: ``cols`` is non-decreasing in ``W`` and the
    origin moves outward with it, so the largest panel bounds every smaller
    one (continuous wheel/pinch zoom is bounded by its integer level, where
    ``tileSize`` is smallest).

    Returns
    -------
    List[TileKey]
        Tiles ordered by zoom, x, y, without duplicates.

    Raises
    ------
    TypeError, ValueError
        As for ``plan_tiles``.
    """
    viewport_px = _require_int(viewport_px, "viewport_px")
    if viewport_px <= 0:
        raise ValueError("viewport_px must be > 0")
    if len(zooms) == 0:
        raise ValueError("zooms must not be empty")
    levels = {_require_int(z, "zoom") for z in zooms}
    top = max(levels)
    out: set = set()
    steps = int((MAX_DISPLAY_ZOOM - MIN_DISPLAY_ZOOM) / 0.5)
    for i in range(steps + 1):
        display = MIN_DISPLAY_ZOOM + 0.5 * i
        z = min(math.floor(display), top)
        if z not in levels:
            continue
        tile_size = TILE_PX * 2 ** (display - z)
        cx, cy = project(lat, lng, z)
        cols = math.ceil(viewport_px / tile_size) + 2
        ox = math.floor(cx) - cols // 2
        oy = math.floor(cy) - cols // 2
        n = 2 ** z
        for tx in range(max(ox, 0), min(ox + cols, n)):
            for ty in range(max(oy, 0), min(oy + cols, n)):
                out.add(TileKey(z, tx, ty))
    return sorted(out)


def required_tiles(
    lat: float,
    lng: float,
    zooms: Sequence[int],
    viewport_px: int = DEFAULT_VIEWPORT_PX,
) -> List[TileKey]:
    """Union of ``plan_tiles`` and ``map_loop_tiles``.

    The set the offline map must hold so that neither the plan envelope nor
    any request of the tactical map's tile loop (panel widths up to
    ``viewport_px``, display zoom 17 to 22) falls outside the tile tree.

    Returns
    -------
    List[TileKey]
        Tiles ordered by zoom, x, y, without duplicates.

    Raises
    ------
    TypeError, ValueError
        As for ``plan_tiles``.
    """
    return sorted(
        set(plan_tiles(lat, lng, zooms, viewport_px)) | set(map_loop_tiles(lat, lng, zooms, viewport_px))
    )


def _atomic_write(path: pathlib.Path, writer: Callable[[pathlib.Path], None]) -> None:
    """Write ``path`` through ``<name>.part`` and ``os.replace``.

    ``writer`` receives the temporary path. On any exception the temporary
    file is removed and ``path`` is left untouched.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        writer(tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def tile_is_valid(path: pathlib.Path) -> bool:
    """True if ``path`` is a non-empty file that Pillow decodes completely."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        with Image.open(path) as img:
            img.load()
        return True
    except (OSError, SyntaxError, ValueError):
        return False


def tile_path(root: pathlib.Path, key: TileKey) -> pathlib.Path:
    """Path of a tile under ``root`` as ``{z}/{x}/{y}.jpg``."""
    return root / str(key.z) / str(key.x) / f"{key.y}.jpg"


def verify_coverage(root: pathlib.Path, required: Sequence[TileKey]) -> List[TileKey]:
    """Return the required tiles absent (or empty) under ``root``, in input order."""
    root = pathlib.Path(root)
    missing: List[TileKey] = []
    for key in required:
        if not tile_is_valid(tile_path(root, key)):
            missing.append(key)
    return missing


def write_manifest(
    root: pathlib.Path,
    required: Sequence[TileKey],
    lat: float,
    lng: float,
    viewport_px: int,
    source: str,
    license_name: str,
    attribution: str,
) -> pathlib.Path:
    """Write ``manifest.json`` (schema ``bmg.tiles.v1``) under ``root``.

    Parameters
    ----------
    root : pathlib.Path
        Tile root directory; created if absent.
    required : Sequence[TileKey]
        Planned tile set; ``count`` is its length and ``bytes`` the size of
        the planned tiles present on disk.
    lat, lng : float
        Base coordinate.
    viewport_px : int
        Viewport the set was planned for.
    source, license_name, attribution : str
        Provenance strings supplied by the operator; no default is assumed.

    Returns
    -------
    pathlib.Path
        Manifest path.

    Raises
    ------
    ValueError
        If ``required`` is empty or a provenance string is blank.
    """
    if len(required) == 0:
        raise ValueError("required must not be empty")
    for name, value in (("source", source), ("license", license_name), ("attribution", attribution)):
        if not isinstance(value, str):
            raise TypeError(f"{name} must be str")
        if not value.strip():
            raise ValueError(f"{name} must not be blank")
    root = pathlib.Path(root)
    root.mkdir(parents=True, exist_ok=True)
    size = sum(tile_path(root, k).stat().st_size for k in required if tile_path(root, k).is_file())
    zooms = [k.z for k in required]
    data: Dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "source": source,
        "license": license_name,
        "attribution": attribution,
        "anchor": {"lat": lat, "lng": lng},
        "minZoom": min(zooms),
        "maxZoom": max(zooms),
        "viewportPx": viewport_px,
        "count": len(required),
        "bytes": size,
    }
    path = root / MANIFEST_NAME
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    _atomic_write(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))
    return path


def parse_zooms(spec: str) -> List[int]:
    """Parse ``"17-20"`` or ``"17,19,20"`` into a sorted list of zooms.

    Raises
    ------
    ValueError
        If the specification is malformed or the range is inverted.
    """
    zooms: List[int] = []
    try:
        for part in spec.split(","):
            if "-" in part:
                lo, hi = (int(p) for p in part.split("-", 1))
                if lo > hi:
                    raise ValueError("inverted range")
                zooms.extend(range(lo, hi + 1))
            else:
                zooms.append(int(part))
    except ValueError as exc:
        raise ValueError(f"invalid zoom specification {spec!r}") from exc
    if not zooms or min(zooms) < 0:
        raise ValueError(f"invalid zoom specification {spec!r}")
    return sorted(set(zooms))


def load_anchor(path: pathlib.Path) -> Tuple[float, float]:
    """Read ``(lat, lng)`` from ``site-anchor.json`` (``latitude``/``longitude``).

    Raises
    ------
    ValueError
        If the file is unreadable or lacks numeric coordinates.
    """
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return _require_real(data["latitude"], "latitude"), _require_real(data["longitude"], "longitude")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid anchor file {path}: {exc}") from exc


def recode_tiles(
    src_dir: pathlib.Path,
    dst_root: pathlib.Path,
    required: Sequence[TileKey],
    quality: int = JPEG_QUALITY,
) -> List[TileKey]:
    """Recode ``{z}/{x}/{y}.png|jpg`` tiles under ``src_dir`` to JPEG under ``dst_root``.

    Only tiles in ``required`` are kept; alpha is composited on black.

    Returns
    -------
    List[TileKey]
        Tiles written, in ``required`` order.
    """
    written: List[TileKey] = []
    for key in required:
        base = pathlib.Path(src_dir) / str(key.z) / str(key.x)
        src = next((p for p in (base / f"{key.y}.png", base / f"{key.y}.jpg") if p.is_file()), None)
        if src is None:
            continue
        with Image.open(src) as img:
            rgba = img.convert("RGBA")
            canvas = Image.new("RGB", rgba.size, (0, 0, 0))
            canvas.paste(rgba, mask=rgba.getchannel("A"))
        out = tile_path(pathlib.Path(dst_root), key)
        _atomic_write(out, lambda tmp, c=canvas: c.save(tmp, format="JPEG", quality=quality))
        written.append(key)
    return written


def _retry_after_s(exc: urllib.error.HTTPError) -> Optional[float]:
    raw = exc.headers.get("Retry-After") if exc.headers is not None else None
    try:
        value = float(raw) if raw is not None else math.nan
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return min(value, MAX_RETRY_AFTER_S)


def _fetch_tile(url: str) -> bytes:
    delay = 1.0
    last: Optional[Exception] = None
    for attempt in range(FETCH_ATTEMPTS):
        wait = delay
        try:
            with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            last = exc
            if exc.code == 429:
                advised = _retry_after_s(exc)
                if advised is not None:
                    wait = advised
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
            last = exc
        if attempt + 1 < FETCH_ATTEMPTS:
            time.sleep(wait)
            delay *= 2
    assert last is not None
    raise last


def _store_jpeg(payload: bytes, out: pathlib.Path) -> None:
    with Image.open(io.BytesIO(payload)) as img:
        img.load()
        if img.size != (TILE_PX, TILE_PX):
            raise ValueError(f"unexpected tile size {img.size}")
        if img.format == "JPEG":
            _atomic_write(out, lambda tmp: tmp.write_bytes(payload))
        else:
            rgb = img.convert("RGB")
            _atomic_write(out, lambda tmp: rgb.save(tmp, format="JPEG", quality=JPEG_QUALITY))


def validate_url_template(template: str) -> None:
    """Validate an XYZ URL template before any request is issued.

    Only the placeholders ``{z}``, ``{x}``, ``{y}`` and ``{key}`` are allowed,
    ``{z}``, ``{x}`` and ``{y}`` are mandatory and the scheme must be https.

    Raises
    ------
    ValueError
        If the template violates any of these rules.
    """
    try:
        fields = {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}
    except ValueError as exc:
        raise ValueError(f"invalid url template: {exc}") from exc
    unknown = fields - TEMPLATE_FIELDS
    if unknown:
        raise ValueError(f"invalid url template: unknown placeholder(s) {sorted(unknown)}")
    absent = TEMPLATE_REQUIRED - fields
    if absent:
        raise ValueError(f"invalid url template: missing placeholder(s) {sorted(absent)}")
    if urllib.parse.urlsplit(template).scheme != "https":
        raise ValueError("invalid url template: scheme must be https")


def tile_bbox_3857(key: TileKey) -> Tuple[float, float, float, float]:
    """Web Mercator extent of an XYZ tile, in metres.

    Parameters
    ----------
    key : TileKey
        Tile with ``0 <= x, y < 2**z``.

    Returns
    -------
    Tuple[float, float, float, float]
        ``(min_x, min_y, max_x, max_y)``; ``y`` grows northward here, while
        the tile ``y`` index grows southward.

    Raises
    ------
    TypeError, ValueError
        If a component is not an integer or the tile is outside the grid.
    """
    z = _require_int(key.z, "z")
    x = _require_int(key.x, "x")
    y = _require_int(key.y, "y")
    if z < 0 or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise ValueError(f"tile {z}/{x}/{y} is outside the grid")
    size = 2.0 * WEB_MERCATOR_HALF_M / (2 ** z)
    min_x = -WEB_MERCATOR_HALF_M + x * size
    max_y = WEB_MERCATOR_HALF_M - y * size
    return min_x, max_y - size, min_x + size, max_y


def validate_wms_endpoint(endpoint: str) -> None:
    """Require an ``https`` WMS endpoint with a host.

    Raises
    ------
    ValueError
        If the scheme is not https or the host is missing.
    """
    parts = urllib.parse.urlsplit(endpoint)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("invalid wms endpoint: https url with a host required")


def build_wms_url(endpoint: str, layer: str, key: TileKey, srs: str = WMS_SRS) -> str:
    """WMS 1.1.1 ``GetMap`` URL that returns exactly one XYZ tile as JPEG.

    The server renders the tile extent itself, so no resampling happens here.
    ``EPSG:900913`` is the alias GeoServer publishes for Web Mercator.

    Raises
    ------
    ValueError
        If ``endpoint`` is not https or ``layer`` is blank.
    """
    validate_wms_endpoint(endpoint)
    if not isinstance(layer, str) or not layer.strip():
        raise ValueError("wms layer must be a non-empty string")
    bbox = ",".join(repr(v) for v in tile_bbox_3857(key))
    query = urllib.parse.urlencode(
        {
            "service": "WMS",
            "version": "1.1.1",
            "request": "GetMap",
            "layers": layer,
            "styles": "",
            "srs": srs,
            "bbox": bbox,
            "width": TILE_PX,
            "height": TILE_PX,
            "format": "image/jpeg",
        }
    )
    return f"{endpoint}?{query}"


def _err(message: str) -> None:
    print(f"offline_tiles: {message}", file=sys.stderr)


def _plan_inputs(args: argparse.Namespace) -> Tuple[float, float, List[int], int]:
    lat, lng = load_anchor(args.anchor)
    return lat, lng, parse_zooms(args.zooms), args.viewport_px


def _cmd_plan(args: argparse.Namespace) -> int:
    lat, lng, zooms, viewport = _plan_inputs(args)
    tiles = required_tiles(lat, lng, zooms, viewport)
    print(f"anchor {lat:.7f} {lng:.7f} viewport_px {viewport}")
    for z in zooms:
        print(f"z{z} {sum(1 for t in tiles if t.z == z)}")
    print(f"total {len(tiles)}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    lat, lng, zooms, viewport = _plan_inputs(args)
    required = required_tiles(lat, lng, zooms, viewport)
    problems: List[str] = []
    missing = verify_coverage(args.root, required)
    if missing:
        problems.append(f"missing {len(missing)} of {len(required)} tiles")
        problems.extend(f"  {k.z}/{k.x}/{k.y}" for k in missing)
    manifest_path = pathlib.Path(args.root) / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != MANIFEST_SCHEMA:
            problems.append("manifest schema mismatch")
        for field in ("source", "license", "attribution"):
            value = manifest.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"manifest {field} missing or blank")
        if manifest.get("count") != len(required):
            problems.append(f"manifest count {manifest.get('count')} != {len(required)}")
        if manifest.get("minZoom", math.inf) > min(zooms) or manifest.get("maxZoom", -math.inf) < max(zooms):
            problems.append("manifest zoom range does not cover requested zooms")
        if manifest.get("viewportPx", 0) < viewport:
            problems.append("manifest viewportPx below requested viewport")
    except (OSError, json.JSONDecodeError, AttributeError):
        problems.append(f"manifest unreadable: {manifest_path}")
    if problems:
        for line in problems:
            _err(line)
        return 1
    print(f"ok {len(required)} tiles")
    return 0


def _fetch_required(args: argparse.Namespace, url_for: Callable[[TileKey], str]) -> int:
    """Fetch every required tile that is not valid on disk, then write the manifest."""
    lat, lng, zooms, viewport = _plan_inputs(args)
    required = required_tiles(lat, lng, zooms, viewport)
    interval = 1.0 / args.rate
    for index, tile in enumerate(required, 1):
        out = tile_path(args.root, tile)
        if tile_is_valid(out):
            continue
        started = time.monotonic()
        try:
            _store_jpeg(_fetch_tile(url_for(tile)), out)
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
            _err(f"tile {tile.z}/{tile.x}/{tile.y} failed: {type(exc).__name__}")
            return 2
        print(f"{index}/{len(required)} {tile.z}/{tile.x}/{tile.y}")
        time.sleep(max(0.0, interval - (time.monotonic() - started)))
    write_manifest(args.root, required, lat, lng, viewport, args.source, args.license, args.attribution)
    return 0 if not verify_coverage(args.root, required) else 1


def _cmd_xyz(args: argparse.Namespace) -> int:
    validate_url_template(args.url_template)
    key = os.environ.get(KEY_ENV)
    if not key:
        _err(f"environment variable {KEY_ENV} is required")
        return 2
    return _fetch_required(
        args, lambda tile: args.url_template.format(z=tile.z, x=tile.x, y=tile.y, key=key)
    )


def _cmd_wms(args: argparse.Namespace) -> int:
    validate_wms_endpoint(args.endpoint)
    if not args.layer.strip():
        raise ValueError("wms layer must be a non-empty string")
    return _fetch_required(args, lambda tile: build_wms_url(args.endpoint, args.layer, tile))


def _cmd_raster(args: argparse.Namespace) -> int:
    tools = {name: shutil.which(name) for name in ("gdalwarp", "gdal2tiles.py")}
    absent = [name for name, found in tools.items() if not found]
    if absent:
        _err(f"required GDAL tool(s) not found in PATH: {', '.join(absent)}")
        return 2
    src = pathlib.Path(args.src)
    if not src.is_file():
        _err(f"source raster not found: {src}")
        return 2
    lat, lng, zooms, viewport = _plan_inputs(args)
    required = required_tiles(lat, lng, zooms, viewport)
    with tempfile.TemporaryDirectory(prefix="bmg_tiles_") as tmp:
        warped = pathlib.Path(tmp) / "warped.tif"
        pyramid = pathlib.Path(tmp) / "pyramid"
        warp = [str(tools["gdalwarp"]), "-t_srs", "EPSG:3857", "-r", "lanczos", "-dstalpha"]
        if args.srs:
            warp += ["-s_srs", args.srs]
        steps = [
            warp + [str(src), str(warped)],
            [str(tools["gdal2tiles.py"]), "--xyz", "-z", f"{min(zooms)}-{max(zooms)}", str(warped), str(pyramid)],
        ]
        for command in steps:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                _err(f"{pathlib.Path(command[0]).name} failed: {result.stderr.strip()}")
                return 2
        recode_tiles(pyramid, args.root, required)
    write_manifest(args.root, required, lat, lng, viewport, args.source, args.license, args.attribution)
    return 0 if not verify_coverage(args.root, required) else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="offline_tiles", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, provenance: bool) -> None:
        p.add_argument("--zooms", default=DEFAULT_ZOOMS)
        p.add_argument("--viewport-px", type=int, default=DEFAULT_VIEWPORT_PX)
        p.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
        p.add_argument("--anchor", type=pathlib.Path, default=DEFAULT_ANCHOR)
        if provenance:
            p.add_argument("--source", required=True)
            p.add_argument("--license", required=True)
            p.add_argument("--attribution", required=True)

    common(sub.add_parser("plan", help="print tile counts per level"), False)
    common(sub.add_parser("verify", help="check tiles and manifest on disk"), False)
    raster = sub.add_parser("raster", help="cut tiles from a local georeferenced raster")
    common(raster, True)
    raster.add_argument("--src", type=pathlib.Path, required=True)
    raster.add_argument("--srs", default=None)
    xyz = sub.add_parser("xyz", help="fetch tiles from an authorized XYZ source")
    common(xyz, True)
    xyz.add_argument("--url-template", required=True)
    xyz.add_argument("--rate", type=float, default=4.0)
    wms = sub.add_parser("wms", help="fetch one GetMap image per tile from an open https WMS")
    common(wms, True)
    wms.add_argument("--endpoint", required=True)
    wms.add_argument("--layer", required=True)
    wms.add_argument("--rate", type=float, default=3.0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    rate = getattr(args, "rate", 1.0)
    if args.viewport_px <= 0 or not math.isfinite(rate) or rate <= 0:
        _err("viewport-px and rate must be positive and finite")
        return 2
    handlers = {"plan": _cmd_plan, "verify": _cmd_verify, "xyz": _cmd_xyz, "wms": _cmd_wms, "raster": _cmd_raster}
    try:
        return handlers[args.command](args)
    except (ValueError, TypeError) as exc:
        _err(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
