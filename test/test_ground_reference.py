"""The mission publishes its z0 once the ground reference is accepted (6.2)."""

from __future__ import annotations

import types

from mvp_mission_bebop.steps.takeoff import TakeoffStep


class Odometry:
    def __init__(self, accepted, z0):
        self.accepted = accepted
        self.z0 = z0

    def calibrate_ground_reference(self):
        return self.accepted

    def snapshot(self):
        return types.SimpleNamespace(ground_reference=self.z0)


def ctx(accepted, z0=0.12, no_fly=True):
    published = []
    return published, types.SimpleNamespace(
        odom_supervisor=Odometry(accepted, z0),
        drone=types.SimpleNamespace(no_fly=no_fly),
        publish_ground_reference=published.append,
    )


def test_an_accepted_reference_is_published():
    published, context = ctx(accepted=True)
    assert TakeoffStep()._calibrate(context) is True
    assert published == [0.12]


def test_a_refused_reference_is_not_published():
    published, context = ctx(accepted=False)
    assert TakeoffStep()._calibrate(context) is True
    assert published == []
