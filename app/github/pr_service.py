def pr_mode(decision: str):
    if decision == "AUTO_PR":
        return {"draft": False}
    if decision == "DRAFT_PR":
        return {"draft": True}
    return None
