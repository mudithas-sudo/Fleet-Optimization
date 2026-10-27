"""Users, roles and sessions for the back-office console.

Switched on with AUTH_ENABLED=1 (off by default, so the demo opens straight in).
When on:
 - staff sign in at /static/login.html; the session is an HMAC-signed, HttpOnly
   cookie; passwords are scrypt-hashed; repeated wrong passwords lock the account
   briefly;
 - every /api route needs a permission, and each role has its own set (admin, supervisor, dispatcher,
   customer service, warehouse, fleet manager, analyst, viewer);
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

# The people who use the console. Each role has a set of permissions (not a rank), so a warehouse
# worker can load and check in parcels without being able to plan runs, and a fleet manager can edit
# vehicles and drivers without touching parcels. `ROLES` keeps a rank only for ordering in lists.
ROLES = {"admin": 8, "supervisor": 7, "dispatcher": 6, "support": 5, "warehouse": 4, "fleet": 3, "analyst": 2, "viewer": 1}
ROLE_LABEL = {"admin": "Administrator", "supervisor": "Operations supervisor", "dispatcher": "Dispatcher",
              "support": "Customer service", "warehouse": "Warehouse / hub staff", "fleet": "Fleet manager",
              "analyst": "Analyst", "viewer": "Viewer"}

ALL_PERMS = {
    "parcels.read", "parcels.write", "parcels.review",      # see parcels; add/edit/import; reschedule or return failures
    "runs.read", "runs.plan", "routes.edit",                 # see runs; plan/change runs; edit predefined routes
    "registry.read", "registry.write",                       # see / edit vehicles and drivers
    "hub", "settings",                                       # change the hub; costs, fuel and other settings
    "reports", "impact", "customers",                        # reports; the Impact screen; customer messages
    "warehouse.read", "warehouse.load", "warehouse.receive", # loading bay; load parcels; check parcels in at the hub
    "cod.read", "cod.write", "cod.remit",                   # see the cash ledger; count a rider's cash in; pay a merchant out
    "users",
}
ROLE_PERMS = {
    "admin": set(ALL_PERMS),
    "supervisor": ALL_PERMS - {"users"},
    "dispatcher": {"parcels.read", "parcels.write", "parcels.review", "runs.read", "runs.plan", "registry.read",
                   "customers", "warehouse.read", "cod.read"},
    "support": {"parcels.read", "parcels.write", "parcels.review", "runs.read", "registry.read", "customers"},
    "warehouse": {"parcels.read", "runs.read", "registry.read", "warehouse.read", "warehouse.load", "warehouse.receive", "cod.read", "cod.write"},
    "fleet": {"parcels.read", "runs.read", "registry.read", "registry.write"},
    "analyst": {"parcels.read", "runs.read", "registry.read", "reports", "impact", "cod.read"},
    "viewer": {"parcels.read", "runs.read", "registry.read", "warehouse.read", "cod.read"},
}
# what each role sees: the pages in the side menu (dashboard, parcels, fleet = Runs, attention, warehouse,
# registry = Fleet and drivers, impact, reports) and where they land after signing in
ROLE_INFO = {
    "admin": {"help": "Everything, including people", "landing": "dashboard",
              "pages": ["dashboard", "parcels", "fleet", "routes", "attention", "warehouse", "cash", "registry", "impact", "reports"]},
    "supervisor": {"help": "Runs the operation: planning, fleet, hub, routes, reports", "landing": "dashboard",
                   "pages": ["dashboard", "parcels", "fleet", "routes", "attention", "warehouse", "cash", "registry", "impact", "reports"]},
    "dispatcher": {"help": "Plans runs and handles parcels, pickups and failures", "landing": "dashboard",
                   "pages": ["dashboard", "parcels", "fleet", "routes", "attention", "warehouse", "cash"]},
    "support": {"help": "Customer calls: parcels, pickups, rescheduling, messages", "landing": "parcels",
                "pages": ["parcels", "attention", "fleet", "routes"]},
    "warehouse": {"help": "Loading bay: what to hand each driver, and check-in of returns", "landing": "warehouse",
                  "pages": ["warehouse", "cash", "parcels"]},
    "fleet": {"help": "Vehicles and drivers: add, edit, availability", "landing": "registry",
              "pages": ["registry", "fleet", "routes"]},
    "analyst": {"help": "Reports and the Impact screen, read-only", "landing": "dashboard",
                "pages": ["dashboard", "parcels", "fleet", "routes", "cash", "impact", "reports"]},
    "viewer": {"help": "Can look, can’t change anything", "landing": "dashboard",
               "pages": ["dashboard", "parcels", "fleet", "routes", "attention", "warehouse", "cash"]},
}


def has_perm(role: str, perm: str | None) -> bool:
    return perm is None or perm in ROLE_PERMS.get(role, set())
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
            "permissions": sorted(ROLE_PERMS[user["role"]]), "landing": ROLE_INFO[user["role"]]["landing"],
            "pages": ROLE_INFO[user["role"]]["pages"],
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

_PATTERNS = {
    "loaded_parcel": re.compile(r"^/api/parcels/[^/]+/loaded$"),
    "received": re.compile(r"^/api/parcels/[^/]+/received$"),
    "loaded_run": re.compile(r"^/api/runs/[^/]+/loaded$"),
    "review": re.compile(r"^/api/parcels/[^/]+/(?:review|reschedule|return|early)$"),
    "operate": re.compile(r"^/api/parcels/[^/]+/(?:pod|fail|collect|move)$"),
    "notes": re.compile(r"^/api/(?:notifications|parcels/[^/]+/notifications)$"),
    "absent": re.compile(r"^/api/drivers/[^/]+/absent$"),
    "driver_queue": re.compile(r"^/api/drivers/[^/]+/queue$"),
}


def required_perm(method: str, path: str) -> str | None:
    """The permission an API call needs (None = any signed-in person)."""
    write = method not in ("GET", "HEAD", "OPTIONS")
    if path.startswith("/api/auth/") or path in ("/api/geocode", "/api/revgeocode", "/api/config"):
        return None
    if path.startswith("/api/users"):
        return "users"
    if path.startswith("/api/reports"):
        return "reports"
    if path.startswith(("/api/impact", "/api/scorecard")):
        return "impact"
    if path.startswith(("/api/settings/costs", "/api/settings/fuel")):
        return "settings" if write else "reports"
    if path == "/api/settings/depot":
        return "hub" if write else None
    if path.startswith("/api/settings"):
        return "settings" if write else None
    if path.startswith("/api/route-templates"):
        if path.endswith("/run"):
            return "runs.plan"
        return "routes.edit" if write else "runs.read"
    if path.startswith("/api/cod"):
        if path.endswith("/remit") or path.endswith("/settings"):
            return "cod.remit" if write else "cod.read"
        return "cod.write" if write else "cod.read"
    if path.startswith("/api/risk"):
        return "parcels.read"
    if path.startswith(("/api/disruptions", "/api/capacity")) or _PATTERNS["absent"].match(path):
        return "runs.plan" if write else "runs.read"
    if path == "/api/fuel-quota":
        return "settings" if write else "runs.read"
    if path.startswith("/api/warehouse"):
        return "warehouse.read"
    if _PATTERNS["loaded_parcel"].match(path) or _PATTERNS["loaded_run"].match(path):
        return "warehouse.load"
    if _PATTERNS["received"].match(path):
        return "warehouse.receive"
    if _PATTERNS["notes"].match(path):
        return "customers"
    if _PATTERNS["driver_queue"].match(path):
        return "runs.read"
    if path.startswith(("/api/vehicles", "/api/drivers")):
        return "registry.write" if write else "registry.read"
    if path == "/api/trips" and method == "DELETE":
        return "settings"
    if path.startswith("/api/parcels"):
        if write and _PATTERNS["review"].match(path):
            return "parcels.review"
        if write and _PATTERNS["operate"].match(path):
            return "runs.plan"
        if path.endswith("/fit"):
            return "runs.read"
        return "parcels.write" if write else "parcels.read"
    return "runs.plan" if write else "runs.read"


PUBLIC = (
    ("POST", re.compile(r"^/api/auth/login$")),
    ("POST", re.compile(r"^/api/auth/logout$")),
    ("GET", re.compile(r"^/api/auth/me$")),
    ("GET", re.compile(r"^/api/auth/demo-accounts$")),
    ("GET", re.compile(r"^/api/config$")),
    ("GET", re.compile(r"^/api/track/[^/]+$")),
    ("POST", re.compile(r"^/api/track/[^/]+/respond$")),
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
        if not has_perm(user["role"], required_perm(request.method, path)):
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
                                              "role": "admin", "roleLabel": ROLE_LABEL["admin"],
                                              "permissions": sorted(ROLE_PERMS["admin"]), "landing": "dashboard",
                                              "pages": ROLE_INFO["admin"]["pages"]}}
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    return {"authEnabled": True, "user": public_user(user)}


@router.get("/api/auth/demo-accounts")
async def demo_accounts():
    """For the demo sign-in page: the sample accounts and their password, listed only when the
    server was started with DEMO_ACCOUNTS=1 (never on a real deployment)."""
    if os.environ.get("DEMO_ACCOUNTS", "0") != "1":
        return {"enabled": False, "accounts": []}
    accounts = [u for u in db.all_entities("users") if u.get("demo") and u.get("active", True)]
    accounts.sort(key=lambda u: (-ROLES.get(u["role"], 0), u["username"]))
    return {"enabled": True, "password": os.environ.get("DEMO_PASSWORD", "demo-pass-123"),
            "accounts": [{"username": u["username"], "name": u["name"], "role": u["role"], "roleLabel": ROLE_LABEL[u["role"]],
                          "help": ROLE_INFO[u["role"]]["help"], "pages": ROLE_INFO[u["role"]]["pages"],
                          "landing": ROLE_INFO[u["role"]]["landing"]} for u in accounts]}


@router.get("/api/auth/roles")
async def list_roles():
    """Every role: label, what it is for, the pages it sees and what it may do (one source of truth for the UI)."""
    return [{"id": r, "label": ROLE_LABEL[r], "help": ROLE_INFO[r]["help"], "landing": ROLE_INFO[r]["landing"],
             "pages": ROLE_INFO[r]["pages"], "permissions": sorted(ROLE_PERMS[r])} for r in ROLES]


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
