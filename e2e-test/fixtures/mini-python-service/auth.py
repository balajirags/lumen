import jwt


def require_login(fn):
    def wrapper(*args, **kwargs):
        token = "stub"
        jwt.decode(token, "secret", algorithms=["HS256"])
        return fn(*args, **kwargs)
    return wrapper
