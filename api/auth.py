"""Autentisering: bcrypt-hashning + session-cookie (Starlette SessionMiddleware)."""

import hashlib

import bcrypt
from fastapi import Depends, HTTPException, Request

from . import database


def hash_token(raw):
    """SHA-256 av en opak token/nyckel (lagras hashad, aldrig i klartext)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bearer(request: Request):
    h = request.headers.get("Authorization") or ""
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, hashed):
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, AttributeError):
        return False


def current_user(request: Request):
    """FastAPI-dependency: inloggad användare (dict) eller None. Accepterar både
    session-cookie (webben) och en opak `Authorization: Bearer`-token (icke-webb-klienter)."""
    uid = request.session.get("uid")
    if not uid:
        token = _bearer(request)
        if token:
            uid = database.user_id_for_token(hash_token(token))
    if not uid:
        return None
    return database.get_user_by_id(uid)


def public_user(user):
    """Fält som är säkra att returnera (utan lösenordshash)."""
    if not user:
        return None
    return {"id": user["id"], "email": user["email"]}


# ---- Admin-/konsolkonton (egen session-nyckel, skilda från app-konton) ----
def current_admin(request: Request):
    """FastAPI-dependency: returnerar inloggad admin (dict) eller None."""
    aid = request.session.get("admin_uid")
    if not aid:
        return None
    return database.get_admin_by_id(aid)


def public_admin(admin):
    if not admin:
        return None
    return {"id": admin["id"], "email": admin["email"]}


def require_admin(admin=Depends(current_admin)):
    """Dependency: 403 om ingen konsol-admin är inloggad."""
    if not admin:
        raise HTTPException(status_code=403, detail="Admin krävs.")
    return admin
