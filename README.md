# MVP Mission Bebop

Autonomous search, visual servoing, nadir forensic inspection, and closed-loop return-to-launch (RTL) package for the Parrot Bebop 2 unmanned aerial vehicle (UAV) running under ROS 2 and Nectar SDK.

---

## 1. System Overview

`mvp_mission_bebop` implements a deterministic, fault-tolerant flight state machine designed for rapid aerial accident reconnaissance and forensic inspection. The system combines real-time object detection (YOLOv8), image-based visual servoing (IBVS), active ultrasound anti-climb regulation, and body-frame proportional odometry navigation back to the takeoff origin.

### Architectural Pipeline

```
 +-----------------------------------------------------------------------+
 |                            MissionRunner                              |
 +-----------------------------------------------------------------------+
        |
        v
 [Step 1: TakeoffStep]
        |  - IMU flat trim calibration
        |  - Origin ground reference (x0, y0, z0) calibration
        |  - Controlled ascent to target altitude & hover stabilization
        v
 [Step 2: ForwardSearchStep]
        |  - Forward translation cruise (vx > 0)
        |  - Camera inclined at search tilt (-20 deg)
        |  - Active anti-climb regulation (vz <= 0)
        |  - Real-time YOLO target identification with temporal confirmation
        v
 [Step 3: VisualServoingStep]
        |  - Longitudinal and lateral target visual servoing
        |  - Proportional gimbal pitch tracking (-20 deg down to -80 deg nadir)
        |  - Target loss recovery routine
        v
 [Step 4: NadirInspectionStep]
        |  - Motionless nadir hover (-80 deg tilt)
        |  - Onboard snapshot triggering & local dual-fidelity storage
        v
 [Step 5: ClosedLoopRTLStep]
        |  - Proportional navigation in FLU body frame toward origin (x0, y0)
        |  - Arrival radius convergence validation
        |  - Origin hover station-keeping & terminal controlled landing
        v
     [Completed]
```

---

## 2. Core Subsystems

### Image-Based Visual Servoing (IBVS) Controller
- **Gimbal Pitch Control**: Drives the camera tilt from search inclination down to nadir (-80.0 deg) proportionally to the optical elevation error.
- **Lateral Deviation Correction**: Regulates body-frame lateral velocity ($v_y$) via PID control with a deadband to eliminate oscillations.
- **Longitudinal Advance**: Couples forward velocity ($v_x$) to centering quality, reducing forward speed as the camera pitches toward nadir.

### Anti-Climb Altitude Governor
Parrot Bebop 2 firmware utilizes ultrasonic ranging for low-altitude estimation. Flying over ground obstacles or targets can cause unintended upward altitude spikes. The `AltitudeAntiClimbGovernor` continuously monitors odometry relative altitude and issues non-positive vertical velocity commands ($v_z \le 0.0$) whenever altitude exceeds the target safety threshold.

### Kinematic Invariant Enforcer & Failsafe Supervisor
- **Strict Yaw Lock**: Yaw rate commands ($v_{yaw}$) are strictly prohibited during autonomous operation to maintain consistent forward visual search tracks.
- **Ceiling Monitor**: Automatically dispatches a controlled safe descent if relative altitude exceeds the safety ceiling.
- **Heartbeat Watchdog**: Monitors odometry message frequency and video frame freshness, triggering immediate landing upon sensor disconnection.
- **Operator Emergency Signal Handling**: Intercepts SIGINT/SIGTERM (Ctrl+C) and executes a multi-frame deceleration and landing sequence. Motors are never cut abruptly in mid-air.

---

## 3. Repository Structure

```
mvp_mission_bebop/
├── .gitignore                      # Exclusions for weights, logs, and media
├── package.xml                     # ROS 2 package manifest
├── setup.py                        # Python setuptools build script
├── setup.cfg                       # Script installation paths
├── README.md                       # System documentation
├── scripts/
│   └── bmg                         # Desktop Mission GUI launcher
├── examples/
│   └── basic_square.py             # Basic waypoint flight demonstration
├── test/
│   ├── test_parameters.py          # Configuration persistence tests
│   ├── test_controllers.py         # PID and Governor unit tests
│   └── test_failsafe.py            # Safety invariant enforcement tests
└── mvp_mission_bebop/
    ├── __init__.py                 # Top-level module exports
    ├── context.py                  # Shared mission execution context
    ├── exceptions.py               # Domain-specific runtime exceptions
    ├── flight_controller.py        # CLI alias entry point
    ├── mission.py                  # Primary mission executable
    ├── parameters.py               # Dataclass-based mission parameters
    ├── actuators/
    │   ├── __init__.py
    │   └── proxy.py                # Hardware proxy with benchtop mode
    ├── controllers/
    │   ├── __init__.py
    │   ├── anti_climb.py           # Altitude anti-climb governor
    │   └── visual_servoing.py      # Coupled IBVS controller
    ├── engine/
    │   ├── __init__.py
    │   └── runner.py               # Step pipeline execution engine
    ├── steps/
    │   ├── __init__.py
    │   ├── base.py                 # Abstract step base class and status enums
    │   ├── takeoff.py              # Step 1: Calibration and takeoff
    │   ├── search.py               # Step 2: Linear search
    │   ├── tracking.py             # Step 3: Visual servoing
    │   ├── inspection.py           # Step 4: Nadir inspection
    │   └── rtl.py                  # Step 5: Closed-loop RTL
    └── telemetry/
        ├── __init__.py
        ├── announcer.py            # Acoustic telemetry notification system
        ├── failsafe.py             # Invariant watchdog supervisor
        └── odometry.py             # Telemetry processing and coordinate math
```

---

## 4. Installation & Build

### Prerequisites
- Ubuntu 22.04 / 24.04 LTS
- ROS 2 (Humble / Iron / Rolling)
- Python 3.10+
- Nectar SDK (`nectar-sdk`)
- `ros2_bebop_driver`

### Workspace Build
Clone into your ROS 2 workspace `src/` directory and build using `colcon`:

```bash
cd ~/ros2_ws
colcon build --packages-select mvp_mission_bebop --symlink-install
source install/setup.bash
```

---

## 5. Execution

### Driver Initialization
Prior to running the autonomous mission, connect the host machine to the Bebop 2 Wi-Fi network (`Bebop2-XXXXXX`) and launch the ROS 2 driver:

```bash
ros2 launch ros2_bebop_driver bebop_node_launch.xml ip:=192.168.42.1
```

### Full Autonomous Flight
Run the mission through the ROS 2 executable interface:

```bash
ros2 run mvp_mission_bebop mission
```

### Hardware-in-the-Loop Benchtop Testing (`--no-fly`)
For indoor testing without spinning propellers:

```bash
ros2 run mvp_mission_bebop mission --no-fly
```
In this mode:
- Vision inference, camera tilt commands, and telemetry publishing remain fully active.
- Motor takeoff, landing, and velocity translations are simulated safely on the workbench.

---

## 6. Command-Line Options

| Argument | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--height` | `float` | `1.00` | Target altitude and safety ceiling (meters) |
| `--velocity` | `float` | `0.05` | Forward cruise velocity during search |
| `--search-timeout`| `float` | `30.0` | Search phase timeout before initiating abort |
| `--hover-duration`| `float` | `7.0` | Duration of stationary nadir hover inspection (seconds) |
| `--confidence` | `float` | `0.50` | YOLO detection confidence threshold |
| `--model-path` | `str` | `yolov8n.pt` | Path to YOLO model weights |
| `--ip` | `str` | `192.168.42.1` | Drone Wi-Fi IP address |
| `--detection-topic`| `str` | `/bebop/camera/detections` | Annotated video stream ROS 2 topic |
| `--no-fly` | `flag` | `False` | Disables propeller actuation for benchtop verification |

---

## 7. Testing

Run the automated test suite with `pytest`:

```bash
cd ~/ros2_ws/src/mvp_mission_bebop
python3 -m pytest test/ -v
```

---

## 8. License

This project is licensed under the MIT License.
