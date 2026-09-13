import os

path = os.path.abspath("fantasy.db")
print("Looking for fantasy.db at:", path)

if os.path.exists(path):
    print("File exists. Size:", os.path.getsize(path), "bytes")
    try:
        os.remove(path)
        print("Deleted successfully.")
    except PermissionError as e:
        print("Could NOT delete - it's locked by another process:", e)
        print("Close any program that might have this file open (DB Browser for")
        print("SQLite, a previous 'python cli.py' or 'python ingest.py' window")
        print("still running, VS Code's SQLite viewer, etc.) and run this again.")
else:
    print("No file found at this path - ingest.py must be writing somewhere else,")
    print("or DB_PATH in config.py has been changed.")