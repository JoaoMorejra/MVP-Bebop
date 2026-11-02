#!/usr/bin/env python3
"""Numeric summary of mission logs: ascent overshoot, altitude divergence, landing gate.

Parses the station's ``mission_*.log`` files (``~/.config/bebop-mission-control/logs``)
and extracts, per flight:

* every Stage 1 ascent above the firmware plateau (``Closing a ... deficit``),
  its final altitude, its peak and its overshoot over the target, and whether it
  converged before the deadline; the structured ``[ASCENT_REPORT ...]`` line
  supersedes the legacy prose when present;
* every ``[ALT_PROBE ...]`` sample and the divergence ``|z_rel - fw_alt|``
  between the integrated odometry and the firmware altitude;
* the radial error at which the terminal landing was authorized
  (``Centred over marker``) and the ``z_rel`` at touchdown;
* loop cadence reports, the ``[CMD ...]`` inter-command ``p95_ms`` and the
  ``[DESCENT ...]`` error at the commit point.

The ``--identify-sink`` mode regresses the firmware vertical rate
``d(fw_alt)/dt`` against ``|v_h|``, ``|vyaw|`` and ``|d|v_h|/dt|`` (least
squares over the 3x3 normal equations, no numpy) to identify the altitude
governor feedforward gains from flight data.

Usage::

    python3 scripts/analyze_flight_logs.py ~/.config/bebop-mission-control/logs
    python3 scripts/analyze_flight_logs.py a.log b.log --csv out.csv
    python3 scripts/analyze_flight_logs.py <dir> --identify-sink
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

_FLOAT = r"(-?\d+(?:\.\d+)?)"

_CLOSING = re.compile(rf"Closing a {_FLOAT} m altitude deficit: {_FLOAT} m -> {_FLOAT} m")
_PROGRESS = re.compile(rf"Ascent progress: alt={_FLOAT} m")
_CONVERGED = re.compile(rf"Ascent converged at {_FLOAT} m \(target {_FLOAT} m\)")
_NOT_CONVERGED = re.compile(rf"Ascent did not converge .*?holding {_FLOAT} m against a {_FLOAT} m target")
_ASCENT_REPORT = re.compile(
    rf"\[ASCENT_REPORT target={_FLOAT} peak={_FLOAT} overshoot={_FLOAT} "
    rf"t_converge={_FLOAT} err_final={_FLOAT}(?: converged=(\d))?"
)
_PROBE = re.compile(rf"\[ALT_PROBE t={_FLOAT} .*?z_rel={_FLOAT} .*?fw_alt={_FLOAT} vz={_FLOAT}")
_CENTRED = re.compile(rf"Centred over marker \d+ after {_FLOAT} s: radial error {_FLOAT} m")
_TOUCHDOWN = re.compile(rf"Touchdown confirmed: altitude {_FLOAT} m")
_CADENCE = re.compile(r"\] (\w+) loop cadence: (\d+) cycles at [\d.]+ Hz target, (\d+) overruns")
_CMD = re.compile(
    rf"\[CMD t={_FLOAT} vx={_FLOAT} vy={_FLOAT} vz={_FLOAT} vyaw={_FLOAT} "
    rf"dt_ms={_FLOAT} p95_ms={_FLOAT} max_ms={_FLOAT}"
)
_DESCENT_COMMIT = re.compile(rf"\[DESCENT .*?err=\({_FLOAT},{_FLOAT}\).*?commit=1")

#: A probe is counted as hover when the integrated vertical speed is below this
#: and the firmware altitude is clear of the ground-effect band.
HOVER_MAX_VZ_MPS = 0.05
HOVER_MIN_FW_ALT_M = 0.30


@dataclass
class Ascent:
    """One Stage 1 climb above the firmware plateau."""

    deficit_m: float
    start_m: float
    target_m: float
    final_m: Optional[float] = None
    peak_m: Optional[float] = None
    converged: Optional[bool] = None
    t_converge_sec: Optional[float] = None

    @property
    def overshoot_m(self) -> Optional[float]:
        """Excess of the highest altitude reached over the target, floored at zero."""
        candidates = [v for v in (self.peak_m, self.final_m) if v is not None]
        if not candidates:
            return None
        return max(0.0, max(candidates) - self.target_m)


@dataclass
class Probe:
    """One ``[ALT_PROBE ...]`` sample."""

    t: float
    z_rel: float
    fw_alt: float
    vz: float


@dataclass
class Command:
    """One ``[CMD ...]`` sample."""

    t: float
    vx: float
    vy: float
    vz: float
    vyaw: float
    p95_ms: float


@dataclass
class FlightRecord:
    """Everything extracted from one log file."""

    path: str
    ascents: List[Ascent] = field(default_factory=list)
    probes: List[Probe] = field(default_factory=list)
    landing_gate_radial_m: List[float] = field(default_factory=list)
    touchdown_z_rel: List[float] = field(default_factory=list)
    cadence: List[Tuple[str, int, int]] = field(default_factory=list)
    commands: List[Command] = field(default_factory=list)
    commit_error_m: List[float] = field(default_factory=list)
    #: Firmware altitude of the first probe after each touchdown (the last
    #: probe of the file when none follows).
    touchdown_fw_alt: List[float] = field(default_factory=list)

    @property
    def name(self) -> str:
        """Log file stem."""
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def alt_divergence_m(self) -> List[float]:
        """``|z_rel - fw_alt|`` for every probe, in file order."""
        return [abs(p.z_rel - p.fw_alt) for p in self.probes]

    @property
    def hover_divergence_m(self) -> List[float]:
        """Divergence restricted to hover probes (see ``HOVER_MAX_VZ_MPS``)."""
        return [
            abs(p.z_rel - p.fw_alt)
            for p in self.probes
            if abs(p.vz) <= HOVER_MAX_VZ_MPS and p.fw_alt >= HOVER_MIN_FW_ALT_M
        ]


    @property
    def cmd_p95_ms(self) -> List[float]:
        """``p95_ms`` of every ``[CMD ...]`` line."""
        return [c.p95_ms for c in self.commands]


def parse_log(path: str) -> FlightRecord:
    """Parse one mission log.

    Parameters
    ----------
    path : str
        Log file path.

    Returns
    -------
    FlightRecord
        Extracted series. Lines that match no pattern are ignored.
    """
    record = FlightRecord(path=path)
    current: Optional[Ascent] = None
    awaiting_touchdown_probe = False
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = _ASCENT_REPORT.search(line)
            if match:
                target, peak, overshoot, t_conv, err = (float(match.group(i)) for i in range(1, 6))
                converged = match.group(6) != "0" if match.group(6) is not None else err <= 0.05
                ascent = current if current is not None else Ascent(deficit_m=math.nan, start_m=math.nan, target_m=target)
                ascent.target_m = target
                ascent.peak_m = peak
                ascent.final_m = target + err if ascent.final_m is None else ascent.final_m
                ascent.converged = converged
                ascent.t_converge_sec = t_conv
                if current is None:
                    record.ascents.append(ascent)
                current = None
                continue
            match = _CLOSING.search(line)
            if match:
                current = Ascent(
                    deficit_m=float(match.group(1)),
                    start_m=float(match.group(2)),
                    target_m=float(match.group(3)),
                )
                record.ascents.append(current)
                continue
            match = _PROGRESS.search(line)
            if match and current is not None:
                alt = float(match.group(1))
                current.peak_m = alt if current.peak_m is None else max(current.peak_m, alt)
                continue
            match = _CONVERGED.search(line) or _NOT_CONVERGED.search(line)
            if match and current is not None:
                current.final_m = float(match.group(1))
                current.converged = "did not converge" not in line
                continue
            match = _PROBE.search(line)
            if match:
                t, z_rel, fw_alt, vz = (float(match.group(i)) for i in range(1, 5))
                record.probes.append(Probe(t=t, z_rel=z_rel, fw_alt=fw_alt, vz=vz))
                if awaiting_touchdown_probe:
                    record.touchdown_fw_alt.append(fw_alt)
                    awaiting_touchdown_probe = False
                if current is not None and current.final_m is None:
                    current.peak_m = z_rel if current.peak_m is None else max(current.peak_m, z_rel)
                continue
            match = _CENTRED.search(line)
            if match:
                record.landing_gate_radial_m.append(float(match.group(2)))
                continue
            match = _TOUCHDOWN.search(line)
            if match:
                record.touchdown_z_rel.append(float(match.group(1)))
                awaiting_touchdown_probe = True
                continue
            match = _CADENCE.search(line)
            if match:
                record.cadence.append((match.group(1), int(match.group(2)), int(match.group(3))))
                continue
            match = _CMD.search(line)
            if match:
                values = [float(match.group(i)) for i in range(1, 9)]
                record.commands.append(
                    Command(t=values[0], vx=values[1], vy=values[2], vz=values[3], vyaw=values[4], p95_ms=values[6])
                )
                continue
            match = _DESCENT_COMMIT.search(line)
            if match:
                record.commit_error_m.append(math.hypot(float(match.group(1)), float(match.group(2))))
    if awaiting_touchdown_probe and record.probes:
        record.touchdown_fw_alt.append(record.probes[-1].fw_alt)
    for ascent in record.ascents:
        if ascent.final_m is not None and ascent.peak_m is None:
            ascent.peak_m = ascent.final_m
    return record


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.ceil(fraction * len(ordered))) - 1))
    return ordered[index]


def _fmt(value: Optional[float], digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value:.{digits}f}"


def collect(paths: Iterable[str]) -> List[str]:
    """Expand directories into their ``mission_*.log`` files, sorted."""
    files: List[str] = []
    for path in paths:
        if os.path.isdir(path):
            files.extend(sorted(glob.glob(os.path.join(path, "mission_*.log"))))
        else:
            files.append(path)
    return files


def format_table(records: Sequence[FlightRecord]) -> str:
    """Per-flight table followed by the aggregates (median, p90)."""
    header = (
        f"{'log':<28} {'deficit':>7} {'target':>6} {'final':>6} {'oversh':>6} {'conv':>4} "
        f"{'hov_div':>7} {'gate_m':>6} {'td_zrel':>7} {'p95_ms':>6} {'commit':>6}"
    )
    lines = [header]
    overshoots: List[float] = []
    hover_div: List[float] = []
    gates: List[float] = []
    touch: List[float] = []
    p95: List[float] = []
    commits: List[float] = []
    for record in records:
        hover = record.hover_divergence_m
        hover_div.extend(hover)
        gates.extend(record.landing_gate_radial_m)
        touch.extend(record.touchdown_z_rel)
        p95.extend(record.cmd_p95_ms)
        commits.extend(record.commit_error_m)
        ascents = record.ascents or [None]
        for index, ascent in enumerate(ascents):
            if ascent is not None and ascent.overshoot_m is not None:
                overshoots.append(ascent.overshoot_m)
            first = index == 0
            lines.append(
                f"{record.name[:28]:<28} "
                f"{_fmt(ascent.deficit_m if ascent else None, 2):>7} "
                f"{_fmt(ascent.target_m if ascent else None, 2):>6} "
                f"{_fmt(ascent.final_m if ascent else None, 2):>6} "
                f"{_fmt(ascent.overshoot_m if ascent else None, 2):>6} "
                f"{('-' if ascent is None or ascent.converged is None else ('yes' if ascent.converged else 'no')):>4} "
                f"{_fmt(_percentile(hover, 0.5) if first else None):>7} "
                f"{_fmt(record.landing_gate_radial_m[-1] if first and record.landing_gate_radial_m else None):>6} "
                f"{_fmt(record.touchdown_z_rel[-1] if first and record.touchdown_z_rel else None, 2):>7} "
                f"{_fmt(_percentile(record.cmd_p95_ms, 0.5) if first else None, 1):>6} "
                f"{_fmt(record.commit_error_m[-1] if first and record.commit_error_m else None):>6}"
            )
    lines.append("")
    lines.append(f"flights={len(records)} ascents={len(overshoots)}")
    for label, values in (
        ("ascent overshoot m", overshoots),
        ("hover |z_rel - fw_alt| m", hover_div),
        ("landing gate radial m", gates),
        ("touchdown z_rel m", touch),
        ("touchdown fw_alt m", [v for r in records for v in r.touchdown_fw_alt]),
        ("cmd p95_ms", p95),
        ("descent commit error m", commits),
    ):
        spread = (max(values) - min(values)) if values else math.nan
        lines.append(
            f"{label:<28} n={len(values):<5} median={_fmt(_percentile(values, 0.5))} "
            f"p90={_fmt(_percentile(values, 0.9))} min={_fmt(min(values) if values else None)} "
            f"max={_fmt(max(values) if values else None)} spread={_fmt(spread)}"
        )
    return "\n".join(lines)


def write_csv(records: Sequence[FlightRecord], path: str) -> None:
    """One row per ascent (or per flight without ascent)."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "log", "deficit_m", "target_m", "final_m", "overshoot_m", "converged",
                "hover_divergence_median_m", "landing_gate_radial_m", "touchdown_z_rel_m",
                "cmd_p95_ms_median", "commit_error_m",
            ]
        )
        for record in records:
            for ascent in record.ascents or [None]:
                writer.writerow(
                    [
                        record.name,
                        _fmt(ascent.deficit_m if ascent else None),
                        _fmt(ascent.target_m if ascent else None),
                        _fmt(ascent.final_m if ascent else None),
                        _fmt(ascent.overshoot_m if ascent else None),
                        "" if ascent is None or ascent.converged is None else int(ascent.converged),
                        _fmt(_percentile(record.hover_divergence_m, 0.5)),
                        _fmt(record.landing_gate_radial_m[-1] if record.landing_gate_radial_m else None),
                        _fmt(record.touchdown_z_rel[-1] if record.touchdown_z_rel else None),
                        _fmt(_percentile(record.cmd_p95_ms, 0.5), 1),
                        _fmt(record.commit_error_m[-1] if record.commit_error_m else None),
                    ]
                )


def _solve3(a: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Gaussian elimination with partial pivoting on a 3x3 system."""
    m = [row[:] + [rhs] for row, rhs in zip(a, b)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for row in range(3):
            if row != col:
                factor = m[row][col] / m[col][col]
                for k in range(col, 4):
                    m[row][k] -= factor * m[col][k]
    return [m[i][3] / m[i][i] for i in range(3)]


def identify_sink(records: Sequence[FlightRecord]) -> Optional[Tuple[List[float], int]]:
    """Regress ``-d(fw_alt)/dt`` on ``|v_h|``, ``|vyaw|`` and ``|d|v_h|/dt|``.

    Returns
    -------
    Optional[Tuple[List[float], int]]
        Gains ``[k_horizontal, k_yaw, k_accel]`` and the sample count, or
        ``None`` when the logs carry no ``[CMD ...]`` lines or the system is
        singular.
    """
    ata = [[0.0] * 3 for _ in range(3)]
    atb = [0.0] * 3
    samples = 0
    for record in records:
        if not record.commands:
            continue
        commands = record.commands
        for previous, probe in zip(record.probes, record.probes[1:]):
            dt = probe.t - previous.t
            if dt <= 0.0 or probe.fw_alt < HOVER_MIN_FW_ALT_M:
                continue
            window = [c for c in commands if previous.t <= c.t <= probe.t]
            if len(window) < 2:
                continue
            vh = [math.hypot(c.vx, c.vy) for c in window]
            if any(abs(c.vz) > 1e-3 for c in window):
                continue
            span = window[-1].t - window[0].t
            accel = abs(vh[-1] - vh[0]) / span if span > 0.0 else 0.0
            x = [sum(vh) / len(vh), sum(abs(c.vyaw) for c in window) / len(window), accel]
            y = -(probe.fw_alt - previous.fw_alt) / dt
            for i in range(3):
                atb[i] += x[i] * y
                for j in range(3):
                    ata[i][j] += x[i] * x[j]
            samples += 1
    if samples < 3:
        return None
    gains = _solve3(ata, atb)
    return (gains, samples) if gains is not None else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", help="log files or directories of mission_*.log")
    parser.add_argument("--csv", dest="csv_path", help="write the per-ascent rows to this CSV")
    parser.add_argument("--identify-sink", action="store_true", help="regress the altitude sink feedforward gains")
    args = parser.parse_args(argv)
    files = collect(args.paths)
    if not files:
        print("no mission logs found", file=sys.stderr)
        return 1
    records = [parse_log(path) for path in files]
    print(format_table(records))
    if args.csv_path:
        write_csv(records, args.csv_path)
    if args.identify_sink:
        result = identify_sink(records)
        if result is None:
            print("identify-sink: insufficient [CMD ...] and [ALT_PROBE ...] samples")
        else:
            gains, samples = result
            print(
                f"identify-sink: n={samples} feedforward_gain={gains[0]:.4f} "
                f"yaw_feedforward_gain={gains[1]:.4f} accel_feedforward_gain={gains[2]:.4f}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
