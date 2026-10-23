"""Comptes du tableau de bord : mots de passe hachés (scrypt), session dans un cookie signé (HMAC-SHA256), blocage après échecs.

Bibliothèque standard uniquement. Les comptes et la clé de signature vivent dans un fichier JSON non versionné (par défaut
`users.json` à côté de la base) ; il est relu dès qu'il change, si bien que `printwatch users …` agit sans redémarrer le service.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SESSION_TTL = 12 * 3600               # session ordinaire : une journée de travail
REMEMBER_TTL = 90 * 86400             # « rester connecté » (écran mural) : prolongée tant que la page tourne
PASSWORD_MIN = 10
PASSWORD_MAX = 200
_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


class AuthError(Exception):
    """Refus lisible par l'utilisateur (message en français)."""


# Droits, du plus faible au plus fort. lecture : consulter ; ecriture : + synchroniser, incidents, imprimantes ;
# admin : + comptes et paramètres. Un compte sans rôle enregistré n'a que la lecture (jamais d'élévation par défaut).
ROLES = ("lecture", "ecriture", "admin")


def role_of(user: dict) -> str:
    r = user.get("role")
    return r if r in ROLES else "lecture"


def allows(role: str | None, needed: str) -> bool:
    return role in ROLES and ROLES.index(role) >= ROLES.index(needed)


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if len(e) > 254 or not _EMAIL.fullmatch(e):
        raise AuthError("adresse e-mail invalide")
    return e


def check_password_policy(password: str, email: str) -> None:
    if len(password) < PASSWORD_MIN:
        raise AuthError(f"le mot de passe doit faire au moins {PASSWORD_MIN} caractères")
    if len(password) > PASSWORD_MAX:
        raise AuthError(f"le mot de passe ne doit pas dépasser {PASSWORD_MAX} caractères")
    if password.strip().lower() == email:
        raise AuthError("le mot de passe ne peut pas être l'adresse e-mail")


def temporary_password() -> str:
    """Mot de passe provisoire ALÉATOIRE (jamais l'adresse : elle n'est pas un secret), à transmettre de vive voix.
    16 caractères sans ambiguïté de lecture (pas de 0/O, 1/l/I)."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode()[:1024], salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(_unb64(dk)))
        return hmac.compare_digest(got, _unb64(dk))
    except (ValueError, TypeError):
        return False


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


_DUMMY = hash_password(secrets.token_hex(8))


def _token_id(token: str) -> str:
    """Empreinte d'un jeton (jamais le jeton lui-même sur disque)."""
    return hashlib.sha256(token.encode()).hexdigest()[:32]       # vérification factice : même durée de réponse pour un compte inconnu


@dataclass
class Session:
    email: str
    remember: bool
    expires: float


class UserStore:
    """Fichier JSON : {"secret": clé de signature, "users": {email: {hash, sv, role, must_change, created_at, updated_at}},
    "revoked": {empreinte de jeton: échéance}}.

    `sv` (version de session) augmente à chaque changement de mot de passe : les cookies émis avant deviennent invalides.
    `revoked` : jetons fermés par une déconnexion (seule CETTE session est fermée), oubliés à leur échéance."""

    def __init__(self, path: str | Path, clock=time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.RLock()
        self._mtime: float | None = None
        self._data: dict = {}

    # --- fichier ---------------------------------------------------------------------------------------------------
    def _load(self) -> dict:
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime_ns
            except FileNotFoundError:
                if not self._data.get("secret"):
                    self._data = {"secret": secrets.token_hex(32), "users": {}}
                return self._data
            if mtime != self._mtime:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                data.setdefault("users", {})
                self._data, self._mtime = data, mtime
                if not data.get("secret"):
                    data["secret"] = secrets.token_hex(32)
                    self._save()
            return self._data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        self._mtime = self.path.stat().st_mtime_ns

    @property
    def secret(self) -> bytes:
        return bytes.fromhex(self._load()["secret"])        # sans fichier (aucun compte) : clé en mémoire, aucune session possible

    # --- comptes ---------------------------------------------------------------------------------------------------
    def list(self) -> list[dict]:
        users = self._load()["users"]
        return [{"email": e, "role": role_of(u), "must_change": bool(u.get("must_change")), "created_at": u.get("created_at"),
                 "updated_at": u.get("updated_at")} for e, u in sorted(users.items())]

    def role(self, email: str) -> str | None:
        u = self.get(email)
        return role_of(u) if u else None

    def _admins(self) -> list[str]:
        return [e for e, u in self._load()["users"].items() if role_of(u) == "admin"]

    def set_role(self, email: str, role: str) -> None:
        e = normalize_email(email)
        if role not in ROLES:
            raise AuthError(f"rôle inconnu : {role} (lecture, ecriture ou admin)")
        with self._lock:
            data = self._load()
            u = data["users"].get(e)
            if u is None:
                raise AuthError(f"compte inconnu : {e}")
            if role != "admin" and self._admins() == [e]:
                raise AuthError("il faut garder au moins un administrateur")
            u.update(role=role, updated_at=self.clock())
            self._save()

    def get(self, email: str) -> dict | None:
        try:
            return self._load()["users"].get(normalize_email(email))
        except AuthError:
            return None

    def add(self, email: str, password: str, *, must_change: bool = True, policy: bool = True, role: str = "ecriture") -> str:
        e = normalize_email(email)
        if role not in ROLES:
            raise AuthError(f"rôle inconnu : {role} (lecture, ecriture ou admin)")
        if policy:
            check_password_policy(password, e)
        with self._lock:
            data = self._load()
            if e in data["users"]:
                raise AuthError(f"le compte {e} existe déjà")
            now = self.clock()
            data["users"][e] = {"hash": hash_password(password), "sv": 1, "role": role, "must_change": must_change,
                                "created_at": now, "updated_at": now}
            self._save()
        return e

    def remove(self, email: str) -> None:
        e = normalize_email(email)
        with self._lock:
            data = self._load()
            if e not in data["users"]:
                raise AuthError(f"compte inconnu : {e}")
            if self._admins() == [e]:
                raise AuthError("impossible de retirer le dernier administrateur")
            del data["users"][e]
            self._save()

    def set_password(self, email: str, password: str, *, must_change: bool, policy: bool = True) -> None:
        e = normalize_email(email)
        if policy:
            check_password_policy(password, e)
        with self._lock:
            data = self._load()
            u = data["users"].get(e)
            if u is None:
                raise AuthError(f"compte inconnu : {e}")
            u.update(hash=hash_password(password), sv=int(u.get("sv", 1)) + 1, must_change=must_change, updated_at=self.clock())
            self._save()

    def authenticate(self, email: str, password: str) -> str | None:
        u = self.get(email)
        ok = verify_password(password, u["hash"] if u else _DUMMY)
        return normalize_email(email) if (u and ok) else None

    # --- cookie de session -------------------------------------------------------------------------------------------
    def issue(self, email: str, remember: bool) -> tuple[str, int]:
        """Jeton signé « charge.signature » et sa durée de vie (s)."""
        u = self.get(email)
        if u is None:
            raise AuthError("compte inconnu")
        ttl = REMEMBER_TTL if remember else SESSION_TTL
        payload = _b64(json.dumps({"u": normalize_email(email), "sv": u.get("sv", 1), "r": int(remember),
                                   "exp": int(self.clock() + ttl), "j": secrets.token_urlsafe(9)}, separators=(",", ":")).encode())
        return f"{payload}.{self._sign(payload)}", ttl

    def verify(self, token: str | None) -> Session | None:
        if not token or token.count(".") != 1:
            return None
        payload, sig = token.split(".")
        if not hmac.compare_digest(sig, self._sign(payload)):
            return None
        try:
            d = json.loads(_unb64(payload))
            email, sv, exp, remember = d["u"], d["sv"], float(d["exp"]), bool(d["r"])
        except (ValueError, KeyError, TypeError):
            return None
        u = self.get(email)
        if exp < self.clock() or u is None or u.get("sv", 1) != sv:
            return None
        if _token_id(token) in self._load().get("revoked", {}):
            return None
        return Session(email=email, remember=remember, expires=exp)

    def revoke(self, token: str | None) -> None:
        """Déconnexion : ce jeton précis cesse d'être accepté, même copié ailleurs. Les autres sessions du compte restent ouvertes."""
        session = self.verify(token)
        if session is None:
            return
        with self._lock:
            data = self._load()
            now = self.clock()
            revoked = {k: v for k, v in data.get("revoked", {}).items() if v > now}      # purge des jetons échus
            revoked[_token_id(token)] = session.expires                                  # type: ignore[arg-type]
            data["revoked"] = revoked
            self._save()

    def _sign(self, payload: str) -> str:
        return _b64(hmac.new(self.secret, payload.encode(), hashlib.sha256).digest())


class Throttle:
    """Blocage temporaire : `limit` échecs en `window` secondes pour une même clé (compte ou poste) → refus pendant `lock` secondes."""

    def __init__(self, limit: int = 5, window: float = 900.0, lock: float = 900.0, clock=time.time, max_keys: int = 10_000) -> None:
        self.limit, self.window, self.lock, self.clock, self.max_keys = limit, window, lock, clock, max_keys
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        self._mutex = threading.Lock()

    def retry_after(self, *keys: str) -> int:
        """Secondes d'attente restantes (0 : tentative autorisée)."""
        now = self.clock()
        with self._mutex:
            waits = [self._locked[k] - now for k in keys if self._locked.get(k, 0) > now]
        return int(max(waits)) + 1 if waits else 0

    def failure(self, *keys: str) -> None:
        now = self.clock()
        with self._mutex:
            if len(self._fails) > self.max_keys:
                self._prune(now)
            for k in keys:
                hits = [t for t in self._fails.get(k, []) if now - t < self.window] + [now]
                if len(hits) >= self.limit:
                    self._locked[k] = now + self.lock
                    hits = []
                self._fails[k] = hits

    def success(self, *keys: str) -> None:
        with self._mutex:
            for k in keys:
                self._fails.pop(k, None)

    def _prune(self, now: float) -> None:
        self._fails = {k: v for k, v in self._fails.items() if v and now - v[-1] < self.window}
        self._locked = {k: t for k, t in self._locked.items() if t > now}
