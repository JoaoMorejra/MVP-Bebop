"""Physics and flight-state machine of the emulated Bebop 2. No ROS, no clocks of its own.

The caller owns time: every command carries the instant it was received and
:meth:`BebopPlant.advance` integrates up to an instant, returning what happened
on the way. That keeps the plant deterministic under test and lets the rclpy
adapter (``node.py``) drive it from ``time.monotonic``.

Independence from ``mvp_mission_bebop.actuators.simulator.KinematicSimulator``
is the point. Nothing is imported from the mission: the gain differs from
``normalized_to_mps`` by a seeded efficiency, commands arrive late and through a
first-order lag, the driver's int8 quantization drops sub-percent demands, and
the odometry the mission sees is the integral of noisy, biased speed samples, as
``ros2_bebop_driver`` builds it (``telemetry_state.cpp:OdometryIntegrator``).

Frames: world and body coincide with the yaw the mission holds at zero; ``x``
forward, ``y`` left, ``z`` up, which is what the driver publishes after its NED
sign flips (``bebop_driver_node.cpp:publishOdometry``).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, fields
from enum import IntEnum
from typing import Final, List, Optional, Protocol, Sequence, Tuple, Union

Number = Union[int, float]
Vector3 = Tuple[float, float, float]


class FlyingState(IntEnum):
    """``ARCOMMANDS_ARDRONE3_PILOTINGSTATE_FLYINGSTATECHANGED_STATE``."""

    LANDED = 0
    TAKINGOFF = 1
    HOVERING = 2
    FLYING = 3
    LANDING = 4
    EMERGENCY = 5
    USERTAKEOFF = 6
    MOTOR_RAMPING = 7
    EMERGENCY_LANDING = 8


#: States in which the firmware honours a piloting command (PCMD).
PILOTED_STATES: Final[frozenset] = frozenset({FlyingState.HOVERING, FlyingState.FLYING})

#: States in which a ``land`` request starts a landing. ``MOTOR_RAMPING`` stops
#: the ramp instead (Suposicao: section 8, item 2 of the scope document).
LANDABLE_STATES: Final[frozenset] = frozenset({FlyingState.TAKINGOFF, FlyingState.HOVERING, FlyingState.FLYING})

#: ``ARCOMMANDS_ARDRONE3_MEDIARECORDSTATE_PICTURESTATECHANGEDV2_STATE``: ready, busy.
PICTURE_READY: Final[int] = 0
PICTURE_BUSY: Final[int] = 1
#: ``ARCOMMANDS_ARDRONE3_MEDIARECORDEVENT_PICTUREEVENTCHANGED_EVENT``: taken.
PICTURE_TAKEN: Final[int] = 0

#: Longest integration substep, seconds.
MAX_STEP_SEC: Final[float] = 0.005
#: The driver caps one odometry integration step at this many seconds
#: (``BebopDriverNode`` builds ``OdometryIntegrator(0.5)``).
ODOMETRY_MAX_STEP_SEC: Final[float] = 0.5
#: Fall rate with the motors cut (``EMERGENCY``), m/s.
EMERGENCY_FALL_MPS: Final[float] = 3.0
#: Descent rate of a firmware emergency landing, m/s.
EMERGENCY_LANDING_MPS: Final[float] = 0.8
#: Time ``EMERGENCY`` is held on the ground before ``LANDED``, seconds.
EMERGENCY_SETTLE_SEC: Final[float] = 0.5


def _number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return float(value)


def quantize(value: float) -> float:
    """Normalized command as the driver transmits it.

    ``Bebop::move`` clamps to [-1, 1] and sends ``static_cast<int8_t>(v * 100)``,
    which truncates towards zero: anything below one percent is an exact zero.
    """
    clamped = max(-1.0, min(1.0, float(value)))
    return math.trunc(clamped * 100.0) / 100.0


@dataclass(frozen=True)
class PlantConfig:
    """Plant parameters. Every default is a documented property of the airframe or the driver.

    Parameters
    ----------
    gain_mps : tuple of float
        Speed per normalized unit along x, y, z at unit efficiency, m/s. The
        mission's ``normalized_to_mps`` default is 1.0; the plant departs from
        it through ``efficiency``.
    efficiency : float
        Multiplier on ``gain_mps``, in (0, 3]. Seeds draw it in [0.8, 1.2].
    tau_sec : float
        First-order time constant of the horizontal and commanded vertical
        speed, seconds. Positive.
    latency_sec : float
        Radio and firmware delay before any command takes effect, seconds.
    ramp_sec, takeoff_altitude_m, takeoff_climb_mps : float
        Firmware launch profile: ``MOTOR_RAMPING`` for ``ramp_sec``, then
        ``TAKINGOFF`` at ``takeoff_climb_mps`` up to ``takeoff_altitude_m``.
    landing_descent_mps : float
        Firmware landing descent rate, m/s.
    speed_noise_mps : float
        Standard deviation of each reported speed component, m/s.
    speed_bias_mps : tuple of float
        Constant bias of the reported speed, m/s: the optical-flow drift.
    speed_bias_walk_mps : float
        Random-walk intensity of the bias, m/s per square-root second.
    odom_rate_hz : float
        ARSDK ``SpeedChanged`` rate; the driver publishes odometry only on a new
        sample (measured 4.97 Hz live).
    max_tilt_rad : float
        Attitude at full horizontal command, reported in the odometry
        quaternion.
    battery_start_pct : float
        Initial charge, percent [0, 100].
    battery_drain_flying_pct_min, battery_drain_ground_pct_min : float
        Drain with the rotors turning and on the ground, percent per minute.
    flat_trim_ack_sec : float
        ``FlatTrimChanged`` delay on the ground (measured < 0.5 s live).
    photo_busy_sec, photo_taken_sec : float
        ``PictureStateChangedV2`` busy and ``PictureEventChanged`` taken delays
        after the request (taken measured 2.5 s live); ready follows taken.
    seed : int
        Seed of the noise generator.

    Raises
    ------
    TypeError
        If a numeric field is not a number (``bool`` refused).
    ValueError
        If a field is non-finite or outside its range.
    """

    gain_mps: Vector3 = (1.0, 1.0, 1.0)
    efficiency: float = 1.0
    tau_sec: float = 0.35
    latency_sec: float = 0.15
    ramp_sec: float = 1.0
    takeoff_altitude_m: float = 1.0
    takeoff_climb_mps: float = 0.4
    landing_descent_mps: float = 0.4
    speed_noise_mps: float = 0.01
    speed_bias_mps: Vector3 = (0.0, 0.0, 0.0)
    speed_bias_walk_mps: float = 0.002
    odom_rate_hz: float = 5.0
    max_tilt_rad: float = 0.35
    battery_start_pct: float = 80.0
    battery_drain_flying_pct_min: float = 5.0
    battery_drain_ground_pct_min: float = 0.2
    flat_trim_ack_sec: float = 0.3
    photo_busy_sec: float = 0.1
    photo_taken_sec: float = 2.5
    seed: int = 0

    def __post_init__(self) -> None:
        positive = ("efficiency", "tau_sec", "ramp_sec", "takeoff_altitude_m", "takeoff_climb_mps",
                    "landing_descent_mps", "odom_rate_hz")
        non_negative = ("latency_sec", "speed_noise_mps", "speed_bias_walk_mps", "max_tilt_rad",
                        "battery_drain_flying_pct_min", "battery_drain_ground_pct_min",
                        "flat_trim_ack_sec", "photo_busy_sec", "photo_taken_sec")
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name in ("gain_mps", "speed_bias_mps"):
                if not isinstance(value, (tuple, list)) or len(value) != 3:
                    raise TypeError(f"{item.name} must be a 3-tuple, got {value!r}")
                components = tuple(_number(f"{item.name}[{i}]", v) for i, v in enumerate(value))
                if item.name == "gain_mps" and any(v <= 0.0 for v in components):
                    raise ValueError(f"gain_mps must be positive, got {value!r}")
                object.__setattr__(self, item.name, components)
            elif item.name == "seed":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise TypeError(f"seed must be an int, got {type(value).__name__}")
            else:
                _number(item.name, value)
                if item.name in positive and value <= 0.0:
                    raise ValueError(f"{item.name} must be positive, got {value!r}")
                if item.name in non_negative and value < 0.0:
                    raise ValueError(f"{item.name} must be non-negative, got {value!r}")
        if self.efficiency > 3.0:
            raise ValueError(f"efficiency must lie in (0, 3], got {self.efficiency!r}")
        if not 0.0 <= self.battery_start_pct <= 100.0:
            raise ValueError(f"battery_start_pct must lie in [0, 100], got {self.battery_start_pct!r}")

    @classmethod
    def from_seed(cls, seed: int) -> "PlantConfig":
        """A plant drawn from ``seed``: efficiency [0.8, 1.2], tau [0.25, 0.5] s, latency [0.10, 0.20] s.

        Parameters
        ----------
        seed : int
            Any integer; the same seed always draws the same plant.
        """
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError(f"seed must be an int, got {type(seed).__name__}")
        draw = random.Random(seed)
        return cls(
            efficiency=round(draw.uniform(0.8, 1.2), 4),
            tau_sec=round(draw.uniform(0.25, 0.5), 4),
            latency_sec=round(draw.uniform(0.10, 0.20), 4),
            speed_bias_mps=(round(draw.uniform(-0.01, 0.01), 4), round(draw.uniform(-0.01, 0.01), 4), 0.0),
            seed=seed,
        )


# ------------------------------------------------------------------ events


@dataclass(frozen=True)
class StateChange:
    """``FlyingStateChanged`` at plant time ``t``."""

    t: float
    old: FlyingState
    new: FlyingState


@dataclass(frozen=True)
class FlatTrimAck:
    """``FlatTrimChanged``: the driver's ``states/flat_trim`` count becomes ``sequence``."""

    t: float
    sequence: int


@dataclass(frozen=True)
class PictureEvent:
    """One entry of the driver's ``states/picture_event`` stream."""

    t: float
    sequence: int
    kind: str
    value: int
    error: int


@dataclass(frozen=True)
class OdometrySample:
    """A ``SpeedChanged`` sample and the pose the driver integrates from it."""

    t: float
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    roll: float
    pitch: float
    yaw: float


@dataclass(frozen=True)
class FaultFired:
    """A scheduled or conditional fault took effect."""

    t: float
    kind: str
    detail: str = ""


PlantEvent = Union[StateChange, FlatTrimAck, PictureEvent, OdometrySample, FaultFired]


class FaultPolicy(Protocol):
    """What the plant asks of a fault schedule (``faults.FaultSchedule``)."""

    def rejects_takeoff(self) -> bool: ...
    def stuck_on_ground(self) -> bool: ...
    def ignores_land(self) -> bool: ...
    def acknowledges_flat_trim(self) -> bool: ...
    def acknowledges_photo(self) -> bool: ...
    def due(self, t: float, anchors: "Anchors") -> Sequence["TimedFault"]: ...


class TimedFault(Protocol):
    """A fault that fires at an instant: ``battery_step`` or ``forced_landing``."""

    kind: str

    def value(self) -> float: ...


@dataclass
class Anchors:
    """Instants faults can be scheduled against, plant time; ``None`` until reached."""

    takeoff: Optional[float] = None
    hover: Optional[float] = None


class _NoFaults:
    def rejects_takeoff(self) -> bool:
        return False

    def stuck_on_ground(self) -> bool:
        return False

    def ignores_land(self) -> bool:
        return False

    def acknowledges_flat_trim(self) -> bool:
        return True

    def acknowledges_photo(self) -> bool:
        return True

    def due(self, t: float, anchors: Anchors) -> Sequence[TimedFault]:
        return ()


@dataclass(order=True)
class _Pending:
    due: float
    order: int
    action: str = field(compare=False)
    args: Tuple[float, ...] = field(compare=False, default=())


class BebopPlant:
    """The emulated airframe.

    Parameters
    ----------
    config : PlantConfig
    faults : FaultPolicy, optional
        Defaults to no fault.
    start_time : float
        Plant time of construction, seconds.
    """

    def __init__(self, config: PlantConfig, faults: Optional[FaultPolicy] = None, start_time: float = 0.0) -> None:
        if not isinstance(config, PlantConfig):
            raise TypeError(f"config must be a PlantConfig, got {type(config).__name__}")
        self.config = config
        self.faults: FaultPolicy = faults if faults is not None else _NoFaults()
        self.time = _number("start_time", start_time)
        self.state = FlyingState.LANDED
        self.anchors = Anchors()
        self.battery_pct = config.battery_start_pct
        self.flat_trim_sequence = 0
        self.picture_sequence = 0
        self.last_odometry: Optional[OdometrySample] = None

        self._rng = random.Random(config.seed)
        self._position = [0.0, 0.0, 0.0]
        self._velocity = [0.0, 0.0, 0.0]
        self._pcmd: Vector3 = (0.0, 0.0, 0.0)
        self._tilt = [0.0, 0.0]
        self._bias = list(config.speed_bias_mps)
        self._pending: List[_Pending] = []
        self._order = 0
        self._ramp_until: Optional[float] = None
        self._emergency_grounded_at: Optional[float] = None
        self._next_odometry = self.time + 1.0 / config.odom_rate_hz
        self._odometry_pose = [0.0, 0.0, 0.0]
        self._odometry_last: Optional[float] = None
        self._events: List[PlantEvent] = []

    # ------------------------------------------------------------- readouts

    @property
    def true_position(self) -> Vector3:
        return (self._position[0], self._position[1], self._position[2])

    @property
    def true_velocity(self) -> Vector3:
        return (self._velocity[0], self._velocity[1], self._velocity[2])

    @property
    def true_altitude(self) -> float:
        return self._position[2]

    @property
    def latched_pcmd(self) -> Vector3:
        """Last quantized piloting command; libARController resends it every 50 ms."""
        return self._pcmd

    # ------------------------------------------------------------- commands

    def takeoff(self, t: float) -> None:
        """``/bebop/takeoff`` received at ``t``."""
        self._schedule(t + self.config.latency_sec, "takeoff")

    def land(self, t: float) -> None:
        """``/bebop/land`` received at ``t``."""
        self._schedule(t + self.config.latency_sec, "land")

    def emergency(self, t: float) -> None:
        """``/bebop/reset`` received at ``t``: motors cut."""
        self._schedule(t + self.config.latency_sec, "emergency")

    def pcmd(self, t: float, vx: float, vy: float, vz: float, vyaw: float) -> None:
        """``/bebop/cmd_vel`` received at ``t``, in the Twist's normalized FLU components.

        ``vyaw`` is accepted and discarded: the mission holds yaw at zero and
        the plant does not model rotation.
        """
        for name, value in (("vx", vx), ("vy", vy), ("vz", vz), ("vyaw", vyaw)):
            _number(name, value)
        self._schedule(t + self.config.latency_sec, "pcmd", (quantize(vx), quantize(vy), quantize(vz)))

    def flat_trim(self, t: float) -> None:
        """``/bebop/flattrim`` received at ``t``. Acknowledged only on the ground."""
        if self.state is FlyingState.LANDED and self.faults.acknowledges_flat_trim():
            self._schedule(t + self.config.flat_trim_ack_sec, "flat_trim_ack")
        elif self.state is FlyingState.LANDED:
            self._events.append(FaultFired(t, "flat_trim_no_ack"))

    def photo(self, t: float) -> None:
        """``/bebop/photo`` (``true``) received at ``t``: RecordPictureV2."""
        cfg = self.config
        self._schedule(t + cfg.photo_busy_sec, "picture", (0.0, float(PICTURE_BUSY)))
        if self.faults.acknowledges_photo():
            self._schedule(t + cfg.photo_taken_sec, "picture", (1.0, float(PICTURE_TAKEN)))
        else:
            self._events.append(FaultFired(t, "photo_no_ack"))
        self._schedule(t + cfg.photo_taken_sec + cfg.photo_busy_sec, "picture", (0.0, float(PICTURE_READY)))

    # ------------------------------------------------------------- integration

    def advance(self, t: float) -> List[PlantEvent]:
        """Integrate up to plant time ``t`` and return the events on the way, in order.

        Raises
        ------
        ValueError
            If ``t`` is earlier than the plant time.
        """
        t = _number("t", t)
        if t < self.time - 1e-12:
            raise ValueError(f"time runs forward only: {t} < {self.time}")
        while self.time < t - 1e-12:
            boundary = min(t, self.time + MAX_STEP_SEC, self._next_odometry)
            if self._pending and self._pending[0].due < boundary:
                boundary = max(self.time, self._pending[0].due)
            self._integrate(boundary - self.time)
            self.time = boundary
            self._apply_due()
            self._apply_faults()
            self._step_state_machine()
            if self.time >= self._next_odometry - 1e-12:
                self._sample_odometry()
                self._next_odometry += 1.0 / self.config.odom_rate_hz
        self._apply_due()
        events, self._events = self._events, []
        return events

    def _schedule(self, due: float, action: str, args: Tuple[float, ...] = ()) -> None:
        self._order += 1
        entry = _Pending(due, self._order, action, args)
        self._pending.append(entry)
        self._pending.sort()

    def _apply_due(self) -> None:
        while self._pending and self._pending[0].due <= self.time + 1e-12:
            entry = self._pending.pop(0)
            getattr(self, f"_on_{entry.action}")(*entry.args)

    def _on_takeoff(self) -> None:
        if self.state is not FlyingState.LANDED:
            return
        if self.faults.rejects_takeoff():
            self._events.append(FaultFired(self.time, "takeoff_rejected"))
            return
        self.anchors.takeoff = self.time
        self._ramp_until = self.time + self.config.ramp_sec
        self._set_state(FlyingState.MOTOR_RAMPING)

    def _on_land(self) -> None:
        if self.state not in LANDABLE_STATES and self.state is not FlyingState.MOTOR_RAMPING:
            return
        if self.faults.ignores_land():
            self._events.append(FaultFired(self.time, "ignore_first_n_lands"))
            return
        if self.state is FlyingState.MOTOR_RAMPING:
            self._ramp_until = None
            self._set_state(FlyingState.LANDED)
            return
        self._set_state(FlyingState.LANDING)

    def _on_emergency(self) -> None:
        if self.state is not FlyingState.LANDED:
            self._set_state(FlyingState.EMERGENCY)

    def _on_pcmd(self, vx: float, vy: float, vz: float) -> None:
        self._pcmd = (vx, vy, vz)
        self._update_piloted_state()

    def _on_flat_trim_ack(self) -> None:
        self.flat_trim_sequence += 1
        self._events.append(FlatTrimAck(self.time, self.flat_trim_sequence))

    def _on_picture(self, is_event: float, value: float) -> None:
        self.picture_sequence += 1
        kind = "event" if is_event else "state"
        self._events.append(PictureEvent(self.time, self.picture_sequence, kind, int(value), 0))

    def _apply_faults(self) -> None:
        for fault in self.faults.due(self.time, self.anchors):
            if fault.kind == "battery_step":
                self.battery_pct = max(0.0, min(100.0, fault.value()))
                self._events.append(FaultFired(self.time, "battery_step", f"{self.battery_pct:.1f}"))
            elif fault.kind == "forced_landing":
                target = FlyingState(int(fault.value()))
                self._events.append(FaultFired(self.time, "forced_landing", str(int(target))))
                if self.state not in (FlyingState.LANDED, target):
                    self._set_state(target)

    def _update_piloted_state(self) -> None:
        if self.state not in PILOTED_STATES:
            return
        translating = self._pcmd[0] != 0.0 or self._pcmd[1] != 0.0
        self._set_state(FlyingState.FLYING if translating else FlyingState.HOVERING)

    def _set_state(self, new: FlyingState) -> None:
        if new is self.state:
            return
        self._events.append(StateChange(self.time, self.state, new))
        self.state = new
        if new is FlyingState.HOVERING and self.anchors.hover is None:
            self.anchors.hover = self.time

    def _step_state_machine(self) -> None:
        state = self.state
        z = self._position[2]
        if state is FlyingState.MOTOR_RAMPING and self._ramp_until is not None and not self.faults.stuck_on_ground():
            if self.time >= self._ramp_until - 1e-12:
                self._ramp_until = None
                self._set_state(FlyingState.TAKINGOFF)
        elif state is FlyingState.TAKINGOFF and z >= self.config.takeoff_altitude_m - 1e-9:
            self._position[2] = self.config.takeoff_altitude_m
            self._velocity[2] = 0.0
            self._set_state(FlyingState.HOVERING)
            self._update_piloted_state()
        elif state in (FlyingState.LANDING, FlyingState.EMERGENCY_LANDING) and z <= 0.0:
            self._ground()
            self._set_state(FlyingState.LANDED)
        elif state is FlyingState.EMERGENCY and z <= 0.0:
            self._ground()
            if self._emergency_grounded_at is None:
                self._emergency_grounded_at = self.time
            elif self.time - self._emergency_grounded_at >= EMERGENCY_SETTLE_SEC:
                self._emergency_grounded_at = None
                self._set_state(FlyingState.LANDED)

    def _ground(self) -> None:
        self._position[2] = 0.0
        self._velocity = [0.0, 0.0, 0.0]

    def _integrate(self, dt: float) -> None:
        if dt <= 0.0:
            return
        cfg = self.config
        state = self.state
        alpha = 1.0 - math.exp(-dt / cfg.tau_sec)
        horizontal_target = (0.0, 0.0)
        vertical: Optional[float] = None
        vertical_target = 0.0
        if state in PILOTED_STATES:
            horizontal_target = tuple(self._pcmd[i] * cfg.gain_mps[i] * cfg.efficiency for i in (0, 1))
            vertical_target = self._pcmd[2] * cfg.gain_mps[2] * cfg.efficiency
        elif state is FlyingState.TAKINGOFF:
            vertical = cfg.takeoff_climb_mps
        elif state is FlyingState.LANDING:
            vertical = -cfg.landing_descent_mps
        elif state is FlyingState.EMERGENCY_LANDING:
            vertical = -EMERGENCY_LANDING_MPS
        elif state is FlyingState.EMERGENCY:
            vertical = -EMERGENCY_FALL_MPS if self._position[2] > 0.0 else 0.0

        if state in (FlyingState.LANDED, FlyingState.MOTOR_RAMPING):
            self._velocity = [0.0, 0.0, 0.0]
        else:
            for axis in (0, 1):
                self._velocity[axis] += (horizontal_target[axis] - self._velocity[axis]) * alpha
            if vertical is None:
                self._velocity[2] += (vertical_target - self._velocity[2]) * alpha
            else:
                self._velocity[2] = vertical

        for axis in range(3):
            self._position[axis] += self._velocity[axis] * dt
        if self._position[2] < 0.0:
            self._position[2] = 0.0
            if self._velocity[2] < 0.0:
                self._velocity[2] = 0.0

        piloted = state in PILOTED_STATES
        for axis in (0, 1):
            command = self._pcmd[axis] if piloted else 0.0
            self._tilt[axis] += (command * cfg.max_tilt_rad - self._tilt[axis]) * alpha

        rotors = state not in (FlyingState.LANDED, FlyingState.EMERGENCY)
        drain = cfg.battery_drain_flying_pct_min if rotors else cfg.battery_drain_ground_pct_min
        self.battery_pct = max(0.0, self.battery_pct - drain * dt / 60.0)

        walk = cfg.speed_bias_walk_mps
        if walk > 0.0:
            for axis in (0, 1):
                self._bias[axis] += self._rng.gauss(0.0, walk * math.sqrt(dt))

    def _sample_odometry(self) -> None:
        cfg = self.config
        noise = cfg.speed_noise_mps
        reported = [
            self._velocity[i] + self._bias[i] + (self._rng.gauss(0.0, noise) if noise > 0.0 else 0.0)
            for i in range(3)
        ]
        if self.state in (FlyingState.LANDED, FlyingState.MOTOR_RAMPING):
            reported = [0.0 if noise == 0.0 else self._rng.gauss(0.0, noise) * 0.1 for _ in range(3)]
        if self._odometry_last is not None:
            dt = min(self.time - self._odometry_last, ODOMETRY_MAX_STEP_SEC)
            for axis in range(3):
                self._odometry_pose[axis] += reported[axis] * dt
        self._odometry_last = self.time
        sample = OdometrySample(
            t=self.time,
            x=self._odometry_pose[0],
            y=self._odometry_pose[1],
            z=self._odometry_pose[2],
            vx=reported[0],
            vy=reported[1],
            vz=reported[2],
            roll=-self._tilt[1],
            pitch=self._tilt[0],
            yaw=0.0,
        )
        self.last_odometry = sample
        self._events.append(sample)
