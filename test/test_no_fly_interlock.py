"""End-to-end: a ``--no-fly`` mission never puts a takeoff or a velocity on the wire.

Runs the real ``mission.py --no-fly --stages 1,2,3,4,5`` in a private
``ROS_DOMAIN_ID`` with a probe node standing where the driver would, subscribed
to ``/bebop/takeoff`` and ``/bebop/cmd_vel``. Any takeoff, or any non-zero
Twist, fails the test; a zero Twist is tolerated because the emergency burst
sends one. The probe also subscribes to ``/bebop/move_camera``, which stays
physical on the bench, and requires at least one: a probe that never matched
the mission would otherwise pass without having observed anything.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time

import pytest

_REPO = os.path.join(os.path.dirname(__file__), "..")
_DOMAIN = 87


def _short_bench_run():
    spec = importlib.util.spec_from_file_location(
        "contracts_for_bench", os.path.join(os.path.dirname(__file__), "test_contracts.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._SHORT_BENCH_RUN


class DriverProbe:
    def __init__(self, domain):
        import rclpy
        from geometry_msgs.msg import Twist, Vector3
        from rclpy.executors import SingleThreadedExecutor
        from std_msgs.msg import Empty

        self.takeoffs = 0
        self.nonzero_twists = []
        self.zero_twists = 0
        self.gimbal = 0
        self._ctx = rclpy.Context()
        rclpy.init(context=self._ctx, domain_id=domain)
        node = rclpy.create_node("no_fly_probe", namespace="/bebop", context=self._ctx)
        node.create_subscription(Empty, "takeoff", self._on_takeoff, 10)
        node.create_subscription(Twist, "cmd_vel", self._on_twist, 10)
        node.create_subscription(Vector3, "move_camera", self._on_gimbal, 10)
        self._executor = SingleThreadedExecutor(context=self._ctx)
        self._executor.add_node(node)
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self):
        try:
            while self._ctx.ok():
                self._executor.spin_once(timeout_sec=0.05)
        except Exception:  # noqa: BLE001 - shutdown race
            pass

    def _on_takeoff(self, _msg):
        self.takeoffs += 1

    def _on_twist(self, msg):
        values = (msg.linear.x, msg.linear.y, msg.linear.z, msg.angular.x, msg.angular.y, msg.angular.z)
        if any(value != 0.0 for value in values):
            self.nonzero_twists.append(values)
        else:
            self.zero_twists += 1

    def _on_gimbal(self, _msg):
        self.gimbal += 1

    def close(self):
        import rclpy

        if self._ctx.ok():
            rclpy.shutdown(context=self._ctx)
        self._thread.join(timeout=2.0)


def test_a_no_fly_mission_publishes_no_takeoff_and_no_velocity(tmp_path):
    pytest.importorskip("rclpy")
    probe = DriverProbe(_DOMAIN)
    try:
        time.sleep(1.0)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "mvp_mission_bebop.mission",
                "--no-fly",
                "--stages",
                "1,2,3,4,5",
                "--config",
                str(tmp_path / "mission_config.json"),
                "--bench-frame",
                os.path.join(_REPO, "test", "fixtures", "bench_target.jpg"),
                "--model-path",
                os.path.join(_REPO, "mvp_mission_bebop", "yolov8n.pt"),
                "--params-json",
                json.dumps(_short_bench_run()),
            ],
            cwd=tmp_path,
            env={
                **os.environ,
                "ROS_DOMAIN_ID": str(_DOMAIN),
                "BMG_INFERENCE_CACHE": str(tmp_path / "inference_device.json"),
            },
            capture_output=True,
            text=True,
            timeout=240,
        )
        time.sleep(0.5)
    finally:
        probe.close()

    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]
    assert probe.gimbal >= 1, "probe never observed the mission; the test proved nothing"
    assert probe.takeoffs == 0
    assert probe.nonzero_twists == []
