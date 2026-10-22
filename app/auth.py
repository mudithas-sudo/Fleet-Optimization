"""Users, roles and sessions for the back-office console.

Switched on with AUTH_ENABLED=1 (off by default, so the demo opens straight in).
When on:
 - staff sign in at /static/login.html; the session is an HMAC-signed, HttpOnly
   cookie; passwords are scrypt-hashed; repeated wrong passwords lock the account
   briefly;
 - every /api route needs a role: viewer (read), dispatcher (plan and change
   deliveries), supervisor (fleet registry, depot, reports, costs), admin (users);
 - the driver app and the public tracking page stay reachable without a staff
   login. Driver endpoints need the driver's own key (the `k` in their link), so a
   guessed run or parcel id can't be used to mark deliveries.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from . import db, store

log = logging.getLogger("fleetops.auth")

ROLES = {"viewer": 1, "dispatcher": 2, "supervisor": 3, "admin": 4}
ROLE_LABEL = {"viewer": "Viewer", "dispatcher": "Dispatcher", "supervisor": "Supervisor", "admin": "Administrator"}
COOKIE = "fleetops_session"
SESSION_SECONDS = 12 * 3600
MIN_PASSWORD = 8
MAX_FAILURES, LOCK_SECONDS = 5, 60

router = APIRouter()


def enabled() -> bool:
    return os.environ.get("AUTH_ENABLED", "0") == "1"


# ------------------------------------------------------------------ passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _scheme, salt, digest = stored.split("$")
        probe = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(probe.hex(), digest)
    except (ValueError, TypeError):
        return False


def _check_password_rules(password: str, username: str):
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Use at least {MIN_PASSWORD} characters for the password.")
    if password.lower() == username.lower():
        raise HTTPException(400, "The password can't be the same as the username.")


# ------------------------------------------------------------------ sessions

def _secret() -> bytes:
    env = os.environ.get("SECRET_KEY")
    if env:
        return env.encode()
    secret = db.get_setting("auth_secret")
    if not secret:
        secret = secrets.token_hex(32)
        db.set_setting("auth_secret", secret)
    return secret.encode()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(user: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(
        {"u": user["id"], "v": user.get("pwVersion", 0), "exp": time.time() + SESSION_SECONDS}).encode()).decode()
    return f"{body}.{_sign(body)}"


def user_from_token(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    user = db.load_entity("users", data.get("u", ""))
    if not user or not user.get("active") or user.get("pwVersion", 0) != data.get("v"):
        return None        # deactivated, or the password changed since this session began
    return user


def current_user(request: Request) -> dict | None:
    return user_from_token(request.cookies.get(COOKIE))


def public_user(user: dict) -> dict:
    return {"id": user["id"], "username": user["username"], "name": user["name"], "role": user["role"],
            "roleLabel": ROLE_LABEL[user["role"]], "active": user.get("active", True),
            "lastLoginAt": user.get("lastLoginAt"), "createdAt": user.get("createdAt")}


# ------------------------------------------------------------------ users

def find_by_username(username: str) -> dict | None:
    want = username.strip().lower()
    return next((u for u in db.all_entities("users") if u["username"] == want), None)


def create_user(username: str, name: str, role: str, password: str) -> dict:
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,32}", username):
        raise HTTPException(400, "Usernames are 3–32 characters: letters, numbers, dot, dash or underscore.")
    if role not in ROLES:
        raise HTTPException(400, f"role must be one of {list(ROLES)}")
    if find_by_username(username):
        raise HTTPException(409, "That username is taken.")
    _check_password_rules(password, username)
    user = {"id": f"u-{uuid.uuid4().hex[:8]}", "username": username, "name": name.strip()[:60] or username,
            "role": role, "passwordHash": hash_password(password), "pwVersion": 0, "active": True,
            "createdAt": time.time(), "lastLoginAt": None}
    db.save_entity("users", user)
    return user


def bootstrap_admin() -> None:
    """First start with auth on and no users: create `admin`. The password comes
    from ADMIN_PASSWORD, or is generated and written to the server log once."""
    if not enabled() or db.all_entities("users"):
        return
    password = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(9)
    create_user(os.environ.get("ADMIN_USERNAME", "admin"), "Administrator", "admin", password)
    if not os.environ.get("ADMIN_PASSWORD"):
        msg = (f"FleetOps: created the first administrator — username '{os.environ.get('ADMIN_USERNAME', 'admin')}', "
               f"password '{password}'. Sign in and change it.")
        print(msg, flush=True)
        log.warning(msg)


def _active_admins(excluding: str | None = None) -> int:
    return sum(1 for u in db.all_entities("users")
               if u["role"] == "admin" and u.get("active") and u["id"] != excluding)


# ------------------------------------------------------------------ permissions

def required_role(method: str, path: str) -> int:
    write = method not in ("GET", "HEAD", "OPTIONS")
    if path == "/api/auth/password":                      # everyone may change their own password
        return ROLES["viewer"]
    if path.startswith("/api/users"):
        return ROLES["admin"]
    if path.startswith(("/api/reports", "/api/impact", "/api/settings/costs", "/api/settings/fuel")):
        return ROLES["supervisor"]
    if write and path.startswith("/api/route-templates") and not path.endswith("/run"):
        return ROLES["supervisor"]                          # editing standing routes is a supervisor job; running one is dispatch
    if write and (path.startswith(("/api/vehicles", "/api/drivers", "/api/settings"))
                  or (path == "/api/trips" and method == "DELETE")):
        return ROLES["supervisor"]
    return ROLES["dispatcher"] if write else ROLES["viewer"]


PUBLIC = (
    ("POST", re.compile(r"^/api/auth/login$")),
    ("POST", re.compile(r"^/api/auth/logout$")),
    ("GET", re.compile(r"^/api/auth/me$")),
    ("GET", re.compile(r"^/api/config$")),
    ("GET", re.compile(r"^/api/track/[^/]+$")),
)

# endpoints the driver app calls: (method, pattern, which id the group is)
DRIVER_ROUTES = (
    ("GET", re.compile(r"^/api/drivers/([^/]+)/queue$"), "driver"),
    ("GET", re.compile(r"^/api/trips/([^/]+)$"), "trip"),
    ("GET", re.compile(r"^/api/trips/([^/]+)/parcels$"), "trip"),
    ("POST", re.compile(r"^/api/trips/([^/]+)/(?:start|end|position)$"), "trip"),
    ("POST", re.compile(r"^/api/parcels/([^/]+)/(?:pod|fail|collect)$"), "parcel"),
)


def driver_key(driver: dict) -> str:
    """The driver's secret link key (created on first use for older drivers)."""
    if not driver.get("token"):
        driver["token"] = secrets.token_urlsafe(12)
        db.save_entity("drivers", driver)
    return driver["token"]


def _driver_for(kind: str, ident: str) -> dict | None:
    if kind == "driver":
        return db.load_entity("drivers", ident)
    if kind == "parcel":
        parcel = db.load_entity("parcels", ident)
        ident = (parcel or {}).get("tripId") or ((parcel or {}).get("lastFailure") or {}).get("tripId") or ""
    trip = store.get_trip(ident) if ident else None
    return db.load_entity("drivers", trip["driverId"]) if trip and trip.get("driverId") else None


def driver_key_allows(request: Request) -> bool:
    given = request.headers.get("x-driver-key") or request.query_params.get("k") or ""
    if not given:
        return False
    for method, pattern, kind in DRIVER_ROUTES:
        m = pattern.match(request.url.path)
        if m and request.method == method:
            driver = _driver_for(kind, m.group(1))
            return bool(driver) and hmac.compare_digest(given, driver_key(driver))
    return False


async def middleware(request: Request, call_next):
    path = request.url.path
    if not enabled() or not path.startswith("/api/") or request.method == "OPTIONS":
        return await call_next(request)
    if any(request.method == m and p.match(path) for m, p in PUBLIC):
        return await call_next(request)
    user = current_user(request)
    if user:
        if ROLES[user["role"]] < required_role(request.method, path):
            return JSONResponse({"detail": "Your role doesn't allow that."}, status_code=403)
        return await call_next(request)
    if driver_key_allows(request):
        return await call_next(request)
    return JSONResponse({"detail": "Sign in to continue."}, status_code=401)


# ------------------------------------------------------------------ endpoints

_failures: dict[str, list[float]] = {}


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    current: str
    new: str


class UserIn(BaseModel):
    username: str
    name: str = ""
    role: str = "dispatcher"
    password: str


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = None


@router.post("/api/auth/login")
async def login(body: LoginIn, request: Request, response: Response):
    if not enabled():
        return {"authEnabled": False}
    bootstrap_admin()
    key = f"{body.username.strip().lower()}|{request.client.host if request.client else '?'}"
    now = time.time()
    recent = [t for t in _failures.get(key, []) if now - t < LOCK_SECONDS]
    if len(recent) >= MAX_FAILURES:
        raise HTTPException(429, f"Too many wrong passwords. Wait {LOCK_SECONDS} seconds and try again.")
    user = find_by_username(body.username)
    ok = verify_password(body.password, user["passwordHash"]) if user else verify_password(body.password, "x$00$00")
    if not (user and ok and user.get("active")):
        _failures[key] = recent + [now]
        raise HTTPException(401, "That username and password don't match, or the account is switched off.")
    _failures.pop(key, None)
    user["lastLoginAt"] = now
    db.save_entity("users", user)
    response.set_cookie(COOKIE, make_token(user), max_age=SESSION_SECONDS, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https", path="/")
    return {"authEnabled": True, "user": public_user(user)}


@router.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
async def me(request: Request):
    if not enabled():
        return {"authEnabled": False, "user": {"id": "local", "username": "local", "name": "Local admin",
                                              "role": "admin", "roleLabel": ROLE_LABEL["admin"]}}
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    return {"authEnabled": True, "user": public_user(user)}


@router.post("/api/auth/password")
async def change_password(body: PasswordIn, request: Request, response: Response):
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    if not verify_password(body.current, user["passwordHash"]):
        raise HTTPException(400, "The current password is wrong.")
    _check_password_rules(body.new, user["username"])
    user["passwordHash"] = hash_password(body.new)
    user["pwVersion"] = user.get("pwVersion", 0) + 1      # signs out every other session
    db.save_entity("users", user)
    response.set_cookie(COOKIE, make_token(user), max_age=SESSION_SECONDS, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https", path="/")
    return {"ok": True}


@router.get("/api/users")
async def list_users():
    return [public_user(u) for u in db.all_entities("users")]


@router.post("/api/users")
async def add_user(body: UserIn):
    return public_user(create_user(body.username, body.name, body.role, body.password))


@router.put("/api/users/{user_id}")
async def edit_user(user_id: str, body: UserPatch, request: Request):
    user = db.load_entity("users", user_id)
    if not user:
        raise HTTPException(404, "User not found")
    me_ = current_user(request)
    losing_admin = user["role"] == "admin" and user.get("active") and (
        (body.role is not None and body.role != "admin") or body.active is False)
    if losing_admin and _active_admins(excluding=user_id) == 0:
        raise HTTPException(409, "There must be at least one active administrator.")
    if me_ and me_["id"] == user_id and body.active is False:
        raise HTTPException(409, "You can't switch off your own account.")
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, f"role must be one of {list(ROLES)}")
        user["role"] = body.role
    if body.name is not None:
        user["name"] = body.name.strip()[:60] or user["username"]
    if body.active is not None:
        user["active"] = body.active
    if body.password:
        _check_password_rules(body.password, user["username"])
        user["passwordHash"] = hash_password(body.password)
        user["pwVersion"] = user.get("pwVersion", 0) + 1
    db.save_entity("users", user)
    return public_user(user)


@router.delete("/api/users/{user_id}")
async def delete_user(user_id: str, request: Request):
    user = db.load_entity("users", user_id)
    if not user:
        raise HTTPException(404, "User not found")
    me_ = current_user(request)
    if me_ and me_["id"] == user_id:
        raise HTTPException(409, "You can't delete your own account.")
    if user["role"] == "admin" and user.get("active") and _active_admins(excluding=user_id) == 0:
        raise HTTPException(409, "There must be at least one active administrator.")
    db.delete_entity("users", user_id)
    return {"ok": True}
