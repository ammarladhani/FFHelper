"""
Shared validation for the "points" vs "money" optimizer objective.

This exact check ("lowercase it, confirm it's one of the two values we
support") used to be copy-pasted with slightly different wording in
waiver.best_pickups, waiver.plan_waiver_moves, waiver.explain_pickup,
waiver._search_pickups, trades.evaluate_trade, trades.suggest_trades,
trades.explain_trade, and again (as its own reimplementation) in
server.py's _optimizer_objective. Centralizing it here means a new
objective, or a rename of an existing one, only has to change in one place.
"""

VALID_OBJECTIVES = {"points", "money"}


def normalize_objective(objective: str) -> str:
    """
    Lowercase/strip `objective` and confirm it's one this codebase
    understands, defaulting to "points" when nothing is given.

    Raises plain ValueError (not an HTTP-specific exception) since this is
    called from waiver.py/trades.py directly - including from the CLI and
    the test suite, not just from server.py's request handlers. server.py
    catches the ValueError and turns it into an HTTPException itself.
    """
    value = (objective or "points").strip().lower()
    if value not in VALID_OBJECTIVES:
        raise ValueError(f"objective must be one of {sorted(VALID_OBJECTIVES)}, got {objective!r}")
    return value