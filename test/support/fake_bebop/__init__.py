"""Emulator of the ``ros2_bebop_driver`` node for ``mission.py --fly`` without motors.

See ``docs/PROMPT_IMPLEMENTACAO_VOO_REAL_100.md`` section 4.1: ``plant`` (pure physics), ``faults``
(declarative fault injection), ``scene`` (camera frames from the pose) and ``node`` (the rclpy adapter
that honours the driver contract).
"""
