"""
Recency weighting for projections.

A projected point next week is worth more than a projected point ten
weeks from now: injuries, role changes and byes make far-out projections
much less reliable, and near-term points are the ones you can actually
act on. So every place that ranks by "projected points" can optionally
rank by a WEIGHTED total instead:

    weight(week) = decay ** (week - start_week)

`start_week` is the first week of the range being evaluated (the low end
of the week slider, i.e. "now" by default), so the first week always
counts 1.0 and each later week counts `decay` times the one before it.
With decay=0.9 over weeks 3-16: week 3 = 1.00, week 4 = 0.90,
week 10 = 0.48, week 16 = 0.25.

decay=None (or 1.0) means "no weighting": every week counts 1.0, which
is exactly the old plain-sum behavior. Every function that takes a
`decay` argument treats None as "off".
"""

from typing import Optional


def validate_decay(decay: Optional[float]) -> Optional[float]:
    if decay is None:
        return None
    if not (0 < decay <= 1):
        raise ValueError(f"decay must be in (0, 1], got {decay!r}")
    return decay


def week_weights(start_week: int, end_week: int, decay: Optional[float] = None) -> dict:
    """{week: weight} for every week in [start_week, end_week]."""
    decay = validate_decay(decay)
    if decay is None:
        return {w: 1.0 for w in range(start_week, end_week + 1)}
    return {w: decay ** (w - start_week) for w in range(start_week, end_week + 1)}


def weighted_total(weekly: dict, weights: dict) -> float:
    """Sum of weights[w] * points over a {week: points} dict."""
    return sum(weights[w] * pts for w, pts in weekly.items())