"""
Per-league season structure, read from config.LEAGUES, plus "what week
is it right now".

Kept separate from config.py so config stays pure data/constants and
importable everywhere without side effects.
"""

from datetime import date, datetime
from typing import Optional

import config


def get_league(league_key: str) -> dict:
    for league in config.LEAGUES:
        if league["name"] == league_key:
            return league
    raise KeyError(f"No league named '{league_key}' in config.LEAGUES")


def league_end_week(league_key: str) -> int:
    """Last week of this league's season, playoffs included."""
    return get_league(league_key).get("end_week") or config.END_WEEK


def current_week(today: Optional[date] = None, last_week: Optional[int] = None) -> int:
    """
    The current fantasy week, rolling over at midnight every Sunday
    (config.WEEK_1_START is the Sunday week 1 begins). Clamped to
    [1, last_week] so it stays sensible before the season starts and
    after it ends.
    """
    today = today or date.today()
    if isinstance(today, datetime):
        today = today.date()
    week = (today - config.WEEK_1_START).days // 7 + 1
    upper = last_week if last_week is not None else config.END_WEEK
    return max(1, min(week, upper))


def playoff_settings(league_key: str) -> dict:
    """
    {end_week, playoff_teams, playoff_weeks, regular_season_weeks} for a
    league. Raises ValueError, with a message that says exactly what to
    fix, if the league's playoff settings haven't been filled in yet.
    """
    league = get_league(league_key)
    missing = [k for k in ("playoff_teams", "playoff_weeks") if not league.get(k)]
    if missing:
        raise ValueError(
            f"League '{league_key}' is missing {' and '.join(missing)} in config.LEAGUES - "
            "set how many teams make the playoffs and how many weeks the playoffs last."
        )
    end_week = league_end_week(league_key)
    playoff_weeks = league["playoff_weeks"]
    if playoff_weeks >= end_week:
        raise ValueError(
            f"League '{league_key}': playoff_weeks ({playoff_weeks}) must be smaller "
            f"than end_week ({end_week})."
        )
    return {
        "end_week": end_week,
        "playoff_teams": league["playoff_teams"],
        "playoff_weeks": playoff_weeks,
        "regular_season_weeks": end_week - playoff_weeks,
    }