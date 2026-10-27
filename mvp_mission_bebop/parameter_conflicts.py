"""Conflicts between operational parameters, mirrored from the ground station.

The station (``bebop_mission_control/src/lib/parameterConflicts.ts``) is the
gate: it refuses to save or launch a combination that is provably
inexecutable, and asks for confirmation on one that flies degraded. This module
holds the same rules and constants so the mission can say the same thing in
its own log; :func:`mvp_mission_bebop.config_audit.log_envelope_divergences`
logs each finding as ``[CONFIG CONFLICT] {json}`` and never aborts.

``test/fixtures/parameter_conflict_cases.json`` is the single source of the
expected answers, consumed by ``test/test_parameter_conflicts.py`` and by the
station's vitest, so the two implementations cannot drift apart.

Every input is read from the nested parameter document. A rule whose input is
missing or not a finite number is skipped rather than fed a default.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Final, List, Mapping, Optional, Set, Tuple

from mvp_mission_bebop.launch_envelope import (
    ALTITUDE_MAX_M,
    ALTITUDE_MIN_M,
    PAD_CLEARANCE_M,
    TOTEM_MARGIN_UNLOCK_ABOVE_M,
    VENUE_HEADROOM_M,
)

#: Height at which the Bebop 2 firmware ends its takeoff, in metres. The
#: airframe ignores the altitude it is handed and stabilizes here
#: (``actuators/simulator.FIRMWARE_HOVER_ALTITUDE_M``, ``TakeoffStep._acquire_altitude``).
#: Estimated from the code's comments, not measured in flight. The ceiling
#: ratchet (``estimation.ceiling_schedule``) rests on it.
FIRMWARE_HOVER_HEIGHT_M: Final[float] = 1.0

#: Resolution floor of one marker module for a reliable decode, pixels (W12).
MIN_PX_PER_MODULE: Final[float] = 4.0

#: IMU settle after the flat trim, seconds (``steps.takeoff._FLAT_TRIM_SETTLE_SEC``).
FLAT_TRIM_SETTLE_SEC: Final[float] = 2.0

#: Width of the REC1080_STREAM480 frame Stage 5 detects on, pixels.
STREAM_WIDTH_PX: Final[float] = 856.0

#: Fastest forward cruise flown inside the original horizontal ceiling (W14).
UNCALIBRATED_SPEED_ADVISORY: Final[float] = 0.30

#: Share of the visible ground width the marker may cover (W13).
TAG_FRAME_SHARE: Final[float] = 0.5

#: Tolerance of every comparison against a threshold derived by arithmetic.
_EPS: Final[float] = 1e-9

#: Predefined dictionaries the station offers, with their marker count
#: (``test/fixtures/marker_dictionaries.json``).
MARKER_DICTIONARIES: Final[Tuple[Tuple[str, int], ...]] = (
    ("DICT_4X4_50", 50),
    ("DICT_4X4_100", 100),
    ("DICT_4X4_250", 250),
    ("DICT_4X4_1000", 1000),
    ("DICT_5X5_50", 50),
    ("DICT_5X5_100", 100),
    ("DICT_5X5_250", 250),
    ("DICT_5X5_1000", 1000),
    ("DICT_6X6_50", 50),
    ("DICT_6X6_100", 100),
    ("DICT_6X6_250", 250),
    ("DICT_6X6_1000", 1000),
    ("DICT_7X7_50", 50),
    ("DICT_7X7_1000", 1000),
    ("DICT_ARUCO_ORIGINAL", 1024),
    ("DICT_APRILTAG_16h5", 30),
    ("DICT_APRILTAG_25h9", 35),
    ("DICT_APRILTAG_36h10", 2320),
    ("DICT_APRILTAG_36h11", 587),
)
_MARKER_COUNTS: Final[Dict[str, int]] = dict(MARKER_DICTIONARIES)

_ALTITUDE = "kinematics.target_altitude_m"
_MARGIN = "kinematics.altitude_ceiling_margin_m"
_FORWARD = "kinematics.forward_cruise_velocity"
_REVERSE = "rtl.reverse_cruise_velocity"
_CEILING = "kinematics.max_horizontal_speed"
_APPROACH = "kinematics.max_approach_forward_speed"
_SEARCH = "gimbal.search_tilt_deg"
_NADIR = "gimbal.nadir_tilt_deg"
_NADIR_TOLERANCE = "gimbal.nadir_tilt_tolerance_deg"
_BAND_UP = "gimbal.centering_tilt_band_up_deg"
_CLIMB_BAND = "kinematics.climb_deadband_m"
_CLIMB_SIGMA = "kinematics.climb_settle_max_position_sigma_m"
_IBVS_FLOOR = "vision.min_altitude_for_ibvs_m"
_SETTLE_FLOOR = "kinematics.takeoff_settle_min_altitude_m"
_FOV = "vision.horizontal_fov_deg"
_ACCEL = "kinematics.max_accel_mps2"
_JERK = "kinematics.max_jerk_mps3"
_SEARCH_TIMEOUT = "timeouts.search_timeout_sec"
_RTL_TIMEOUT = "rtl.timeout_sec"
_STABILIZE = "kinematics.takeoff_stabilize_duration_sec"
_SETTLE_WINDOW = "kinematics.takeoff_settle_window_sec"
_COUNTDOWN = "kinematics.countdown_sec"
_FLAT_TRIM_ACK = "timeouts.flat_trim_ack_timeout_sec"
_RETURN_TILT = "rtl.camera_tilt_deg"
_TAG = "rtl.tag_size"
_MARKER_ID = "rtl.target_aruco_id"
_MARKER_DICT = "rtl.marker_dict"
_PAD = "platform.height_m"
_GROUND = "platform.takeoff_from_ground"
_VENUE = "platform.venue_ceiling_agl_m"

_MISSING = object()


@dataclass(frozen=True)
class ParameterConflict:
    """One combination of parameters the mission cannot fly, or flies degraded."""

    id: str
    #: ``"error"`` (inexecutable as configured) or ``"warning"`` (degraded).
    severity: str
    paths: Tuple[str, ...]
    title: str
    message: str
    fix: str

    def as_log_line(self, tag: str) -> str:
        """Render as ``[<tag>] {json}`` on a single line."""
        body = json.dumps(
            {
                "id": self.id,
                "severity": self.severity,
                "paths": list(self.paths),
                "title": self.title,
                "message": self.message,
                "fix": self.fix,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return f"[{tag}] {body}"


def _get(doc: Any, path: str) -> Any:
    node = doc
    for key in path.split("."):
        if not isinstance(node, Mapping) or key not in node:
            return _MISSING
        node = node[key]
    return node


def _real(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def marker_grid(name: Any) -> Optional[int]:
    """Module grid of a dictionary family, without the black border."""
    if not isinstance(name, str):
        return None
    if "4X4" in name or name == "DICT_APRILTAG_16h5":
        return 4
    if "5X5" in name or name in ("DICT_ARUCO_ORIGINAL", "DICT_APRILTAG_25h9"):
        return 5
    if "6X6" in name or name in ("DICT_APRILTAG_36h10", "DICT_APRILTAG_36h11"):
        return 6
    if "7X7" in name:
        return 7
    return None


def _m2(value: float) -> str:
    return f"{value:.2f} m"


def _s1(value: float) -> str:
    return f"{value:.1f} s" if math.isfinite(value) else "sem limite"


Finding = Optional[Tuple[Tuple[str, ...], str, str]]
Reader = Callable[[str], Optional[float]]


class _Evaluation:
    """The rules of the catalogue over one document."""

    def __init__(self, doc: Any) -> None:
        self.doc = doc
        self.fired: Set[str] = set()

    def read(self, path: str) -> Optional[float]:
        return _real(_get(self.doc, path))

    def footprint(self) -> Optional[Tuple[float, float, float, float]]:
        """``(distance, width, tilt, fov)`` of the Stage 5 view at the return altitude."""
        h, tilt, fov = self.read(_ALTITUDE), self.read(_RETURN_TILT), self.read(_FOV)
        if h is None or tilt is None or fov is None or abs(tilt) < _EPS or fov <= 0.0:
            return None
        distance = h / math.sin(math.radians(abs(tilt)))
        return distance, 2.0 * distance * math.tan(math.radians(abs(fov)) / 2.0), tilt, fov

    def usable_tag(self) -> Optional[float]:
        tag = self.read(_TAG)
        return tag if tag is not None and 0.0 < tag <= 1.0 else None

    def from_ground(self) -> bool:
        """Whether the flight takes off from the floor: the flag set, or no pad (``launch_envelope``)."""
        if _get(self.doc, _GROUND) is True:
            return True
        pad = self.read(_PAD)
        return pad is None or pad <= 0.0

    def margin_unlocked(self) -> bool:
        """Whether the ceiling applies the operator margin (``launch_envelope.margin_unlocked``)."""
        if self.from_ground():
            return True
        h = self.read(_ALTITUDE)
        return h is not None and h > TOTEM_MARGIN_UNLOCK_ABOVE_M + _EPS

    def envelope(self) -> Tuple[float, float]:
        """``launch_envelope.altitude_envelope`` over the document, without the venue cap."""
        if self.from_ground():
            return ALTITUDE_MIN_M, ALTITUDE_MAX_M
        settle = self.read(_SETTLE_FLOOR)
        clearance = max(PAD_CLEARANCE_M, settle if settle is not None else PAD_CLEARANCE_M)
        return max(ALTITUDE_MIN_M, (self.read(_PAD) or 0.0) + clearance), ALTITUDE_MAX_M

    def noise_band(self) -> Optional[float]:
        band, sigma = self.read(_CLIMB_BAND), self.read(_CLIMB_SIGMA)
        return None if band is None or sigma is None else band + 2.0 * sigma

    # ------------------------------------------------------------------ errors

    def e01(self) -> Finding:
        s, n = self.read(_SEARCH), self.read(_NADIR)
        if s is None or n is None or n <= s - _EPS:
            return None
        return (
            (_NADIR, _SEARCH),
            f"A câmera precisa terminar mais baixa que o ângulo de varredura (nadir {n:.0f}°, varredura {s:.0f}°).",
            f"Ajuste o nadir para menos de {s:.0f}° ou suba a varredura acima de {n:.0f}°.",
        )

    def e02(self) -> Finding:
        s, n, tol = self.read(_SEARCH), self.read(_NADIR), self.read(_NADIR_TOLERANCE)
        if s is None or n is None or tol is None or n > s - _EPS or s - n > tol + _EPS:
            return None
        return (
            (_SEARCH, _NADIR),
            f"Diferença entre varredura e nadir de {s - n:.1f}°, dentro da tolerância de chegada ({tol:.1f}°).",
            f"Deixe o nadir abaixo de {s - tol:.1f}°.",
        )

    def e03(self) -> Finding:
        """Retired: the ceiling ratchet covers the end of the native takeoff.

        A ceiling under the firmware's takeoff height used to trip right after
        liftoff; the ratchet of ``estimation.ceiling_schedule`` keeps the
        ceiling above the native height plus the margin until the altitude
        acquisition converges, so no combination of altitude and margin trips
        it any more. Kept in the catalogue to preserve the rule ids and order
        shared with the station.
        """
        return None

    def e04(self) -> Finding:
        m, band = self.read(_MARGIN), self.read(_CLIMB_BAND)
        if not self.margin_unlocked() or m is None or band is None or m >= band - _EPS:
            return None
        return (
            (_MARGIN,),
            f"Margem de {_m2(m)} menor que a banda de chegada da subida ({_m2(band)}).",
            f"Aumente a margem para pelo menos {_m2(band)}.",
        )

    def e05(self) -> Finding:
        vf, vr = self.read(_FORWARD), self.read(_REVERSE)
        if vf is None or vr is None or (abs(vf + vr) <= _EPS and vr < 0.0):
            return None
        return (
            (_FORWARD, _REVERSE),
            f"Velocidade de avanço ({vf:.2f}) e de retorno ({vr:.2f}) devem ter a mesma magnitude.",
            f"Retorno em {-abs(vf):.2f}.",
        )

    def e06(self) -> Finding:
        vmax, vf, vr, va = (self.read(p) for p in (_CEILING, _FORWARD, _REVERSE, _APPROACH))
        if vmax is None or vf is None or vr is None or va is None:
            return None
        demand = max(abs(vf), abs(vr), abs(va))
        if vmax >= demand - _EPS and vmax < 1.0:
            return None
        return (
            (_CEILING, _FORWARD),
            f"O teto de velocidade horizontal ({vmax:.2f}) não cobre a velocidade pedida ({demand:.2f}) ou não está "
            "abaixo de 1.0.",
            f"Teto entre {demand:.2f} e 0.95.",
        )

    def e07(self) -> Finding:
        tag = _get(self.doc, _TAG)
        if tag is _MISSING:
            return None
        real = _real(tag)
        if real is not None and 0.0 < real <= 1.0:
            return None
        return ((_TAG,), f"Tamanho de tag {tag!r} fora de (0, 1.0] m.", "Aresta externa da borda preta, em metros.")

    def e08(self) -> Finding:
        marker_id = _get(self.doc, _MARKER_ID)
        if marker_id is _MISSING:
            return None
        dictionary = _get(self.doc, _MARKER_DICT)
        count = _MARKER_COUNTS.get(dictionary) if isinstance(dictionary, str) else None
        number = _real(marker_id)
        integer = number is not None and float(number).is_integer() and number >= 0.0
        if integer and (count is None or number < count):
            return None
        bounds = f" (válidos 0 a {count - 1})" if count is not None else ""
        return (
            (_MARKER_ID, _MARKER_DICT),
            f"ID {marker_id!r} não existe em {dictionary!r}{bounds}.",
            "ID dentro da faixa ou outro dicionário.",
        )

    def e09(self) -> Finding:
        dictionary = _get(self.doc, _MARKER_DICT)
        if dictionary is _MISSING:
            return None
        if isinstance(dictionary, str) and (dictionary in _MARKER_COUNTS or re.fullmatch(r"\s*\d+\s*", dictionary)):
            return None
        if not isinstance(dictionary, bool) and isinstance(dictionary, int) and dictionary >= 0:
            return None
        if isinstance(dictionary, float) and math.isfinite(dictionary) and dictionary.is_integer() and dictionary >= 0:
            return None
        return ((_MARKER_DICT,), f"Dicionário {dictionary!r} não reconhecido.", "Dicionário da lista da estação.")

    def e10(self) -> Finding:
        h = self.read(_ALTITUDE)
        if h is None:
            return None
        low, high = self.envelope()
        if low - _EPS <= h <= high + _EPS:
            return None
        if h < low and not self.from_ground():
            message = f"Cota de {_m2(h)} abaixo do mínimo de {_m2(low)} sobre o totem (topo do totem + folga)."
        else:
            message = f"Cota de {_m2(h)} fora da faixa de {_m2(low)} a {_m2(high)} sobre o piso."
        return ((_ALTITUDE,), message, f"Cota entre {_m2(low)} e {_m2(high)}.")

    def e11(self) -> Finding:
        h, venue = self.read(_ALTITUDE), self.read(_VENUE)
        if h is None or venue is None or h <= venue - VENUE_HEADROOM_M + _EPS:
            return None
        return (
            (_ALTITUDE, _VENUE),
            f"Cota de {_m2(h)} a menos de {_m2(VENUE_HEADROOM_M)} do teto do local ({_m2(venue)}).",
            f"Cota de no máximo {_m2(venue - VENUE_HEADROOM_M)}.",
        )

    # ---------------------------------------------------------------- warnings

    def w01(self) -> Finding:
        """Retired with E03: the ceiling ratchet covers the end of the native takeoff."""
        return None

    def w02(self) -> Finding:
        m, band = self.read(_MARGIN), self.noise_band()
        if not self.margin_unlocked() or "E04" in self.fired or m is None or band is None or m >= band - _EPS:
            return None
        return ((_MARGIN,), f"Margem de {_m2(m)} abaixo do ruído de sonar e barômetro ({_m2(band)}).", f"Margem de pelo menos {_m2(band)}.")

    def w03(self) -> Finding:
        h, ibvs, settle = self.read(_ALTITUDE), self.read(_IBVS_FLOOR), self.read(_SETTLE_FLOOR)
        if h is None or ibvs is None or settle is None:
            return None
        floor = max(ibvs, settle)
        if h > floor + _EPS:
            return None
        return ((_ALTITUDE,), f"Altitude de {_m2(h)}, no piso de {_m2(floor)} ou abaixo.", f"Altitude acima de {_m2(floor)}.")

    def w04(self) -> Finding:
        """Retired: the overrun check rested on the forward approach distance.

        The lateral pattern has no forward approach, so there is no geometry
        between the search and nadir tilts to overrun. Kept in the catalogue
        to preserve rule ids and order shared with the station.
        """
        return None

    def w05(self) -> Finding:
        """Retired: the minimum approach length no longer exists (see :meth:`w04`)."""
        return None

    def w06(self) -> Finding:
        """Retired: the approach no longer consumes the tracking window (see :meth:`w04`)."""
        return None

    def w07(self) -> Finding:
        search, vf, accel, jerk = (self.read(p) for p in (_SEARCH_TIMEOUT, _FORWARD, _ACCEL, _JERK))
        if search is None or vf is None or accel is None or jerk is None or accel <= 0.0 or jerk <= 0.0:
            return None
        ramp = 2.0 * (vf / accel + accel / jerk)
        if search >= ramp - _EPS:
            return None
        return ((_SEARCH_TIMEOUT, _FORWARD), f"Varredura de {_s1(search)} contra rampa de {_s1(ramp)}.", f"Timeout de pelo menos {_s1(ramp)}.")

    def w08(self) -> Finding:
        timeout, vf, vr, search = (self.read(p) for p in (_RTL_TIMEOUT, _FORWARD, _REVERSE, _SEARCH_TIMEOUT))
        if timeout is None or vf is None or vr is None or search is None or abs(vr) < _EPS:
            return None
        needed = (vf * search) / abs(vr)
        if timeout >= needed - _EPS:
            return None
        return ((_RTL_TIMEOUT, _REVERSE, _SEARCH_TIMEOUT), f"Retorno de {_s1(timeout)} contra {_s1(needed)}.", f"Timeout de pelo menos {math.ceil(needed - _EPS)} s.")

    def w09(self) -> Finding:
        stabilize, window = self.read(_STABILIZE), self.read(_SETTLE_WINDOW)
        if stabilize is None or window is None or stabilize > window + 1.0 + _EPS:
            return None
        return ((_STABILIZE,), f"Estabilização de {_s1(stabilize)} contra janela de {_s1(window)}.", f"Mais de {_s1(window + 1.0)}.")

    def w10(self) -> Finding:
        countdown, ack = self.read(_COUNTDOWN), self.read(_FLAT_TRIM_ACK)
        if countdown is None or ack is None:
            return None
        ground = ack + FLAT_TRIM_SETTLE_SEC
        if countdown >= ground - _EPS:
            return None
        return ((_COUNTDOWN,), f"Contagem de {_s1(countdown)} contra sequência de solo de até {_s1(ground)}.", f"Pelo menos {_s1(ground)}.")

    def w11(self) -> Finding:
        s, band = self.read(_SEARCH), self.read(_BAND_UP)
        if s is None or band is None or s + band <= _EPS:
            return None
        return ((_SEARCH,), f"Faixa de centragem sobe a {s + band:.0f}°.", f"Varredura de {-band:.0f}° ou menos.")

    def w12(self) -> Finding:
        footprint, tag = self.footprint(), self.usable_tag()
        grid = marker_grid(_get(self.doc, _MARKER_DICT))
        if footprint is None or tag is None or grid is None:
            return None
        distance, _width, _tilt, fov = footprint
        fx = STREAM_WIDTH_PX / 2.0 / math.tan(math.radians(abs(fov)) / 2.0)
        per_module = fx * tag / distance / (grid + 2)
        if per_module >= MIN_PX_PER_MODULE - _EPS:
            return None
        return ((_TAG, _ALTITUDE), f"Cerca de {per_module:.1f} px por módulo a {_m2(distance)}.", "Tag maior ou altitude menor.")

    def w13(self) -> Finding:
        footprint, tag = self.footprint(), self.usable_tag()
        if "W15" in self.fired or footprint is None or tag is None:
            return None
        if tag <= TAG_FRAME_SHARE * footprint[1] + _EPS:
            return None
        return ((_TAG, _ALTITUDE), f"Marca de {_m2(tag)} em campo de {_m2(footprint[1])}.", "Altitude maior ou tag menor.")

    def w14(self) -> Finding:
        vf = self.read(_FORWARD)
        if vf is None or vf <= UNCALIBRATED_SPEED_ADVISORY + _EPS:
            return None
        return ((_FORWARD,), f"Avanço de {vf:.2f} acima de {UNCALIBRATED_SPEED_ADVISORY:.2f}, normalizado e não calibrado.", "Validar em bancada.")

    def w15(self) -> Finding:
        footprint, tag = self.footprint(), self.usable_tag()
        if footprint is None or tag is None or tag < footprint[1] - _EPS:
            return None
        _distance, width, tilt, fov = footprint
        minimum = tag * math.sin(math.radians(abs(tilt))) / (2.0 * math.tan(math.radians(abs(fov)) / 2.0))
        return ((_TAG, _ALTITUDE), f"Marca de {_m2(tag)} maior que o campo visível ({_m2(width)}).", f"Altitude acima de {_m2(minimum)}.")


#: ``(id, severity, title)`` in the catalogue's order; W15 is evaluated first
#: because W13 defers to it.
_CATALOGUE: Final[Tuple[Tuple[str, str, str], ...]] = (
    ("E01", "error", "Câmera de inspeção acima da varredura"),
    ("E02", "error", "Varredura e nadir quase iguais"),
    ("E03", "error", "Teto abaixo da altura de decolagem do firmware"),
    ("E04", "error", "Margem do teto menor que a banda de chegada"),
    ("E05", "error", "Velocidades de avanço e retorno desacopladas"),
    ("E06", "error", "Teto de velocidade horizontal corta a velocidade pedida"),
    ("E07", "error", "Tamanho de tag inválido"),
    ("E08", "error", "ID do marcador fora do dicionário"),
    ("E09", "error", "Dicionário do marcador não reconhecido"),
    ("E10", "error", "Cota fora do envelope da superfície de decolagem"),
    ("E11", "error", "Cota colada no teto do local"),
    ("W01", "warning", "Teto próximo da altura de decolagem do firmware"),
    ("W02", "warning", "Margem do teto abaixo do ruído dos sensores"),
    ("W03", "warning", "Altitude abaixo do piso da guiagem visual"),
    ("W04", "warning", "A aeronave tende a passar do alvo"),
    ("W05", "warning", "Aproximação curta demais"),
    ("W06", "warning", "Aproximação consome a janela de rastreamento"),
    ("W07", "warning", "Varredura curta para atingir a velocidade"),
    ("W08", "warning", "Janela de retorno menor que o tempo para voltar"),
    ("W09", "warning", "Estabilização sem tempo para assentar"),
    ("W10", "warning", "Contagem menor que a sequência de solo"),
    ("W11", "warning", "Faixa de centragem acima da horizontal"),
    ("W12", "warning", "Marca pequena demais para a câmera"),
    ("W13", "warning", "Marca sem folga no campo de visão"),
    ("W14", "warning", "Velocidade acima do envelope original"),
    ("W15", "warning", "Marca maior que o campo de visão"),
)


def evaluate_parameter_conflicts(doc: Mapping[str, Any]) -> List[ParameterConflict]:
    """Conflicts between the parameters of ``doc``.

    Parameters
    ----------
    doc : Mapping[str, Any]
        The nested parameter document (``MissionParameters.to_dict()``, or the
        ``mission_config.json`` / ``--params-json`` payload).

    Returns
    -------
    List[ParameterConflict]
        Errors first, then warnings, each level in the catalogue's order; one
        record per rule. Empty for a document without conflicts.
    """
    evaluation = _Evaluation(doc)
    order = sorted(_CATALOGUE, key=lambda entry: 0 if entry[0] == "W15" else 1)
    found: Dict[str, ParameterConflict] = {}
    for rule_id, severity, title in order:
        finding = getattr(evaluation, rule_id.lower())()
        if finding is None:
            continue
        evaluation.fired.add(rule_id)
        paths, message, fix = finding
        found[rule_id] = ParameterConflict(rule_id, severity, tuple(paths), title, message, fix)
    return [found[rule_id] for rule_id, _severity, _title in _CATALOGUE if rule_id in found]
