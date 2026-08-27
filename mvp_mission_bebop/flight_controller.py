# =============================================================================
# flight_controller.py — Missão Autónoma MVP Bebop 2
# =============================================================================
# Controlador de voo não-bloqueante para Parrot Bebop 2 operado indoor.
#
# Toda a interface mecânica com a aeronave é orquestrada exclusivamente
# através do Nectar SDK (Black Bee Drones). Publicações directas de mensagens
# ROS 2 (Twist, Empty, …) estão formalmente proibidas.
#
# Middleware: ROS 2 Jazzy · Executor: SingleThreadedExecutor
# =============================================================================
from __future__ import annotations

import enum
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.executors import SingleThreadedExecutor
from std_msgs.msg import Bool

import nectar
from nectar.control import DroneFactory


# ---------------------------------------------------------------------------
# Constantes cinemáticas da missão
# ---------------------------------------------------------------------------

# Fase TAKEOFF — tempo de assimilação térmica inercial do sensor óptico
TAKEOFF_ALTITUDE_M: float = 1.0
TAKEOFF_STABILIZE_S: float = 5.0

# Fase NAVIGATE_OUTBOUND — vector de aproximação à moto acidentada
OUTBOUND_VX: float = 0.5
OUTBOUND_VY: float = 0.0
OUTBOUND_VZ: float = 0.0
OUTBOUND_YAW_RATE: float = 0.0
OUTBOUND_DURATION_S: float = 4.0

# Fase CAPTURE_AND_PROCESS — parâmetros ópticos de captura
CAMERA_TILT_DEG: float = -80.0
CAMERA_PAN_DEG: float = 0.0
CAPTURE_STABILIZE_S: float = 3.0

# Fase NAVIGATE_INBOUND — vector de regresso ao baricentro do totem
INBOUND_VX: float = -0.5
INBOUND_VY: float = 0.0
INBOUND_VZ: float = 0.0
INBOUND_YAW_RATE: float = 0.0
INBOUND_DURATION_S: float = 4.0

# Fase LAND_AND_RESET — duração de segurança pós-aterragem
LAND_COOLDOWN_S: float = 3.0


# ---------------------------------------------------------------------------
# Máquina de Estados Finita (FSM)
# ---------------------------------------------------------------------------
class MissionState(enum.Enum):
    """Estados lógicos da missão"""

    IDLE = enum.auto()
    TAKEOFF = enum.auto()
    NAVIGATE_OUTBOUND = enum.auto()
    CAPTURE_AND_PROCESS = enum.auto()
    NAVIGATE_INBOUND = enum.auto()
    LAND_AND_RESET = enum.auto()


# ---------------------------------------------------------------------------
# Nó ROS 2 — Controlador de Voo
# ---------------------------------------------------------------------------
class FlightControllerNode(Node):
    """Nó ROS 2 que administra a máquina de estados da missão autónoma.

    O nó permanece reactivo ao ``SingleThreadedExecutor``: toda a
    temporização é conduzida por ``create_timer`` — sem ``while`` ou
    ``time.sleep``.

    A aeronave é comandada exclusivamente através da instância
    ``self.drone`` do Nectar SDK (``DroneFactory.create("bebop")``).
    """

    def __init__(self) -> None:
        super().__init__('bebop_flight_controller')

        # ---- Nectar SDK ----
        nectar.init()
        self.drone = DroneFactory.create("bebop")
        self.get_logger().info("Nectar SDK inicializado — BebopDrone criado")

        # ---- Estado da FSM ----
        self._state: MissionState = MissionState.IDLE
        self._phase_timer: Optional[rclpy.timer.Timer] = None

        # ---- Gatilho de disparo externo (webhook / rede) ----
        # Subscrição em /mission/trigger (std_msgs/Bool). Qualquer
        # publicação com data=True no estado IDLE inicia a sequência.
        self._trigger_sub = self.create_subscription(
            Bool,
            "/mission/trigger",
            self._on_trigger_received,
            10,
        )
        self.get_logger().info(
            "FSM em IDLE — aguarda gatilho em /mission/trigger"
        )

    # ------------------------------------------------------------------
    # Utilitário: transição de estado com temporizador one-shot
    # ------------------------------------------------------------------
    def _transition_to(
        self,
        new_state: MissionState,
        duration_s: float,
        on_complete,  # callable
    ) -> None:
        """Muda para ``new_state`` e arma um temporizador one-shot.

        O temporizador dispara ``on_complete`` após ``duration_s`` segundos
        e é automaticamente cancelado para evitar reentrada.
        """
        self._state = new_state
        self.get_logger().info(f"FSM → {new_state.name} ({duration_s:.1f} s)")

        # Cancelar temporizador anterior, se existir
        if self._phase_timer is not None:
            self._phase_timer.cancel()
            self.destroy_timer(self._phase_timer)
            self._phase_timer = None

        def _one_shot_wrapper() -> None:
            """Garante disparo único: cancela-se a si próprio."""
            if self._phase_timer is not None:
                self._phase_timer.cancel()
            on_complete()

        self._phase_timer = self.create_timer(duration_s, _one_shot_wrapper)

    # ------------------------------------------------------------------
    # Callback do gatilho externo (tópico /mission/trigger)
    # ------------------------------------------------------------------
    def _on_trigger_received(self, msg: Bool) -> None:
        """Reage a uma publicação ``Bool(data=True)`` no tópico de disparo."""
        if msg.data and self._state == MissionState.IDLE:
            self.get_logger().info("Gatilho recebido — início da missão")
            self._enter_takeoff()

    # ------------------------------------------------------------------
    # TAKEOFF
    # ------------------------------------------------------------------
    def _enter_takeoff(self) -> None:
        """Activa empuxo inicial e agenda estabilização térmica."""
        self.drone.takeoff(altitude=TAKEOFF_ALTITUDE_M)
        self._transition_to(
            MissionState.TAKEOFF,
            TAKEOFF_STABILIZE_S,
            self._enter_navigate_outbound,
        )

    # ------------------------------------------------------------------
    # NAVIGATE_OUTBOUND
    # ------------------------------------------------------------------
    def _enter_navigate_outbound(self) -> None:
        """Imprime o vector de aproximação frontal à moto acidentada."""
        self.drone.move_velocity(
            vx=OUTBOUND_VX,
            vy=OUTBOUND_VY,
            vz=OUTBOUND_VZ,
            yaw_rate=OUTBOUND_YAW_RATE,
        )
        self._transition_to(
            MissionState.NAVIGATE_OUTBOUND,
            OUTBOUND_DURATION_S,
            self._enter_capture_and_process,
        )

    # ------------------------------------------------------------------
    # CAPTURE_AND_PROCESS
    # ------------------------------------------------------------------
    def _enter_capture_and_process(self) -> None:
        """Estabiliza, alinha câmara e dispara fotografia do sinistro."""
        # Nulidade vetorial — estabilização forçada da malha aerodinâmica
        self.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, yaw_rate=0.0)

        # Inclinação extrema da câmara para o nível térreo
        self.drone.camera_control(tilt=CAMERA_TILT_DEG, pan=CAMERA_PAN_DEG)

        # Captura fotográfica
        self.drone.snapshot()
        self.get_logger().info("Snapshot capturado — processamento pendente")

        self._transition_to(
            MissionState.CAPTURE_AND_PROCESS,
            CAPTURE_STABILIZE_S,
            self._enter_navigate_inbound,
        )

    # ------------------------------------------------------------------
    # NAVIGATE_INBOUND
    # ------------------------------------------------------------------
    def _enter_navigate_inbound(self) -> None:
        """Reverso linear absoluto — regresso ao baricentro do totem."""
        self.drone.move_velocity(
            vx=INBOUND_VX,
            vy=INBOUND_VY,
            vz=INBOUND_VZ,
            yaw_rate=INBOUND_YAW_RATE,
        )
        self._transition_to(
            MissionState.NAVIGATE_INBOUND,
            INBOUND_DURATION_S,
            self._enter_land_and_reset,
        )

    # ------------------------------------------------------------------
    # LAND_AND_RESET
    # ------------------------------------------------------------------
    def _enter_land_and_reset(self) -> None:
        """Extinção progressiva de potência e reposição cíclica."""
        # Nulidade vetorial antes de aterrar
        self.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, yaw_rate=0.0)
        self.drone.land()

        self._transition_to(
            MissionState.LAND_AND_RESET,
            LAND_COOLDOWN_S,
            self._enter_idle,
        )

    # ------------------------------------------------------------------
    # IDLE (reposição cíclica)
    # ------------------------------------------------------------------
    def _enter_idle(self) -> None:
        """Restaura a FSM ao estado quiescente para nova iteração."""
        self._state = MissionState.IDLE

        # Limpar temporizador remanescente
        if self._phase_timer is not None:
            self._phase_timer.cancel()
            self.destroy_timer(self._phase_timer)
            self._phase_timer = None

        # Restaurar câmara à posição neutra
        self.drone.camera_control(tilt=0.0, pan=0.0)

        self.get_logger().info(
            "Missão concluída — FSM em IDLE, aguarda novo gatilho"
        )

    # ------------------------------------------------------------------
    # Destruidor — shutdown graceful do Nectar SDK
    # ------------------------------------------------------------------
    def destroy_node(self) -> None:
        """Garante encerramento limpo do SDK antes de destruir o nó."""
        self.get_logger().info("Encerramento do Nectar SDK…")
        try:
            nectar.shutdown()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warning(f"nectar.shutdown() falhou: {exc}")
        super().destroy_node()


# ---------------------------------------------------------------------------
# Ponto de entrada — executável ROS 2
# ---------------------------------------------------------------------------
def main(args=None) -> None:
    """Inicializa o nó e entrega o controlo ao ``SingleThreadedExecutor``."""
    rclpy.init(args=args)

    node = FlightControllerNode()
    executor = SingleThreadedExecutor()
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        node.get_logger().info("Interrupção manual — encerrando…")
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
