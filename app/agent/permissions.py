"""Permission levels per action class.

chat and generate never need approval. send_message, publish and delete always do:
the action is queued in approvals.py and only runs when the owner sends /approve <id>.
"""

LEVELS = {
    "chat": "auto",
    "generate": "auto",
    "read": "auto",
    "send_message": "approval",
    "publish": "approval",
    "delete": "approval",
}


class PermissionError_(Exception):
    pass


def level(action_class):
    """Unknown action classes are treated as approval-required, never auto."""
    return LEVELS.get(action_class, "approval")


def needs_approval(action_class):
    return level(action_class) == "approval"


def check_known(action_class):
    if action_class not in LEVELS:
        raise PermissionError_(f"unknown action class '{action_class}'")
    return LEVELS[action_class]
