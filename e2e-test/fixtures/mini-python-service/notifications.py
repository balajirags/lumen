def send(user_id):
    """Free module-level function, called via `import notifications;
    notifications.send(...)` from app.py -- exercises the CALLS gap the old
    engine explicitly skips (`import x; x.foo()` for an internal `x`)."""
    print(f"notifying {user_id}")
