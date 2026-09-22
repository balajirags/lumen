class EmailChannel:
    def notify_user(self):
        """The only `notify_user` in this fixture -- called via a duck-typed,
        untyped parameter in orders.py, so it can only resolve via the 0.50
        global-name-fallback CALLS tier."""
        print("emailing user")
