"""Domain-specific exceptions for mission and flight supervisor."""


class MissionException(Exception):
    """Base exception for all mission runtime errors."""


class KinematicConstraintViolation(MissionException):
    """Raised when commanded velocity violates flight envelope invariants."""


class AltitudeCeilingBreachException(MissionException):
    """Raised when relative altitude exceeds the safety ceiling."""


class TelemetryTimeoutException(MissionException):
    """Raised when odometry telemetry updates cease beyond timeout."""


class VideoStreamTimeoutException(MissionException):
    """Raised when incoming video frames cease beyond watchdog timeout."""


class EmergencyAbortException(MissionException):
    """Raised when an operator or external system interrupts execution."""
