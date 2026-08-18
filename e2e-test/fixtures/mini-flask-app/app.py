from flask import Flask, request
from auth import require_login
from repository import UserRepository

app = Flask(__name__)
repo = UserRepository()


@app.route("/users/<user_id>", methods=["GET"])
@require_login
def get_user(user_id):
    """Return a user's profile. Requires login."""
    return repo.find_by_id(user_id)


@app.route("/users/<user_id>/export", methods=["GET"])
def export_user(user_id):
    """Export full user data as JSON. No auth check here."""
    return repo.find_full_record(user_id)


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}
