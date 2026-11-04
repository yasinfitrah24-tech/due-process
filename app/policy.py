"""Which analyst actions a verdict level allows (hard rule 4)."""
from app.signals import NOT_PROVEN, PROVEN, SUSPICIOUS

ALLOWED_ACTIONS = {
    NOT_PROVEN: ("Close",),
    SUSPICIOUS: ("Warn", "Close"),
    PROVEN: ("Suspend", "Warn", "Close"),
}


def allowed_actions(level):
    return ALLOWED_ACTIONS.get(level, ())


def allowed_for(case):
    """A "Test any URL" case has no target, so no action exists for it."""
    return () if case["mode"] == "test" else allowed_actions(case["level"])
