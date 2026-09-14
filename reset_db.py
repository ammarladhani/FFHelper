"""
Delete the local database so `python ingest.py` can rebuild it from
scratch (e.g. after a schema change - see README's upgrade note about
player_id becoming text after Sleeper support was added).

This used to be debug_slots.py, which did the same os.remove() but
unconditionally and without saying what it was going to delete until
after it happened. Same operation, but now it asks first.

Usage:
    python reset_db.py
"""

import os

import config


def main():
    path = os.path.abspath(config.DB_PATH)

    if not os.path.exists(path):
        print(f"No database found at {path} - nothing to do.")
        return

    size_mb = os.path.getsize(path) / (1024 * 1024)
    answer = input(
        f"This will permanently delete {path} ({size_mb:.1f} MB). "
        f"You'll need to re-run `python ingest.py` afterward. Continue? [y/N] "
    )
    if answer.strip().lower() != "y":
        print("Cancelled.")
        return

    try:
        os.remove(path)
        print("Deleted.")
    except PermissionError as e:
        print(f"Could NOT delete - it's locked by another process: {e}")
        print("Close any program that might have this file open (DB Browser for "
              "SQLite, a running 'python cli.py' or 'python ingest.py', an editor's "
              "SQLite viewer, etc.) and try again.")


if __name__ == "__main__":
    main()
