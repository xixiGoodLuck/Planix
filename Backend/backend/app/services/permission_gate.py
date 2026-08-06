from ..schemas import CommandActionRisk, CommandPermission


def command_action_requires_approval(permission: CommandPermission, risk: CommandActionRisk) -> bool:
    if risk == "read":
        return False
    if permission == "low":
        return risk in {"write", "delete", "dangerous"}
    if permission == "medium":
        return risk in {"delete", "dangerous"}
    if permission == "high":
        return risk == "dangerous"
    return True
