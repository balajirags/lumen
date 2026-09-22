import requests


class UserRepository:
    def find_by_id(self, user_id):
        return {"id": user_id, "name": "stub"}

    def find_full_record(self, user_id):
        profile = self.find_by_id(user_id)
        billing = requests.get(f"https://billing.internal/users/{user_id}").json()
        return {**profile, **billing}
