"""Which analyst actions a verdict level allows (hard rule 4)."""
from app.signals import NOT_PROVEN, PROVEN, SUSPICIOUS

ALLOWED_ACTIONS = {
    NOT_PROVEN: ("Close",),
    SUSPICIOUS: ("Warn", "Close"),
    PROVEN: ("Suspend", "Warn", "Close"),
}


APPEALABLE = ("Suspend", "Warn")
LEVEL_RANK = {NOT_PROVEN: 0, SUSPICIOUS: 1, PROVEN: 2}


def appeal_actions(level_at_action, revisit_level):
    """Reinstate needs a second visit that really happened and came out lower. Anything else keeps the action."""
    if revisit_level is not None and LEVEL_RANK[revisit_level] < LEVEL_RANK[level_at_action]:
        return ("Reinstate", "Keep")
    return ("Keep",)


def allowed_actions(level):
    return ALLOWED_ACTIONS.get(level, ())


def allowed_for(case):
    """A "Test any URL" case has no target, so no action exists for it."""
    return () if case["mode"] == "test" else allowed_actions(case["level"])
