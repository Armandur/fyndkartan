"""Autentisering: bcrypt-hashning + session-cookie (Starlette SessionMiddleware)."""

import bcrypt
from fastapi import Request

from . import database


def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, hashed):
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, AttributeError):
        return False


def current_user(request: Request):
    """FastAPI-dependency: returnerar inloggad användare (dict) eller None."""
    uid = request.session.get("uid")
    if not uid:
        return None
    return database.get_user_by_id(uid)


def public_user(user):
    """Fält som är säkra att returnera (utan lösenordshash)."""
    if not user:
        return None
    return {"id": user["id"], "email": user["email"], "is_admin": bool(user.get("is_admin"))}
