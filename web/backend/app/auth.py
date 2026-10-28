"""The dashboard login (HTTP Basic).

WEB_BASIC_AUTH=user:password in the environment is the original login. The password can be changed from the Settings
page: the new one is kept as a salted scrypt hash in a small file in the web service's data volume and wins over the
environment until it is reset (then the original works again). Changing needs the current password again, and only
works when a login exists at all: an open dashboard must not let anyone on the network lock the owner out.
"""
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("solis-web")

MIN_LENGTH, MAX_LENGTH = 8, 128
SCRYPT = {"n": 2 ** 13, "r": 8, "p": 1}            # ~15 ms and 8 MiB per check
ATTEMPTS, WINDOW_S = 5, 300                        # wrong "current password" guesses allowed per window
CACHE_SIZE = 64


class LoginError(ValueError):
    """Something the user can fix: wrong current password, a weak new one."""


class TooManyAttempts(LoginError):
    def __init__(self, retry_s: int):
        super().__init__(f"Too many wrong passwords. Try again in {max(1, retry_s // 60 + (1 if retry_s % 60 else 0))} min.")
        self.retry_s = retry_s


def check_new_password(password, current=None) -> str:
    if not isinstance(password, str):
        raise LoginError("The new password must be text.")
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        raise LoginError(f"The new password needs {MIN_LENGTH} to {MAX_LENGTH} characters.")
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise LoginError("The new password cannot contain control characters.")
    if password != password.strip():
        raise LoginError("The new password cannot start or end with a space.")
    if current is not None and password == current:
        raise LoginError("The new password is the same as the current one.")
    return password


def _scrypt(password: str, salt: bytes, params: dict) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=params["n"], r=params["r"], p=params["p"], dklen=32, maxmem=64 * 1024 * 1024)


class LoginStore:
    def __init__(self, env_basic_auth: str, path: str | None = None, clock=time.time):
        user, _, password = (env_basic_auth or "").partition(":")      # the password may contain colons
        self.env_user, self.env_password = user, password
        self.path, self.clock = path or None, clock
        self.lock = threading.RLock()
        self._saved: dict | None = None
        self._stamp = object()                                          # nothing loaded yet
        self._accepted: dict[str, bool] = {}                            # sha256 of Basic headers already verified
        self._wrong: list[float] = []
        self._refresh()

    # ------------------------------------------------------------------ the saved login
    def _refresh(self) -> None:
        """Pick up a changed or removed file (also one removed by `solis-monitor reset-login`) without a restart."""
        try:
            stamp = os.stat(self.path).st_mtime_ns if self.path else None
        except OSError:
            stamp = None
        with self.lock:
            if stamp == self._stamp:
                return
            self._stamp, self._saved = stamp, self._load() if stamp is not None else None
            self._accepted.clear()

    def _load(self) -> dict | None:
        try:
            with open(self.path) as f:
                raw = json.load(f)
            salt, digest = bytes.fromhex(raw["salt"]), bytes.fromhex(raw["hash"])
            params = {k: int(raw[k]) for k in ("n", "r", "p")}
            if not isinstance(raw["user"], str) or not raw["user"] or len(digest) != 32:
                raise ValueError("incomplete")
            return {"user": raw["user"], "salt": salt, "hash": digest, "params": params, "changed_at": raw.get("changed_at")}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.error("login file %s is unreadable (%s): the WEB_BASIC_AUTH password applies", self.path, exc)
            return None

    @property
    def user(self) -> str:
        self._refresh()
        return self._saved["user"] if self._saved else self.env_user

    def source(self) -> str:
        self._refresh()
        return "saved" if self._saved else "env"

    def info(self) -> dict:
        self._refresh()
        return {"user": self.user, "source": self.source(), "changed_at": self._saved["changed_at"] if self._saved else None,
                "min_length": MIN_LENGTH, "max_length": MAX_LENGTH}

    # ------------------------------------------------------------------ checking
    def check(self, user: str, password: str) -> bool:
        self._refresh()
        saved = self._saved
        if saved:
            ok_user = hmac.compare_digest(user.encode(), saved["user"].encode())
            if not ok_user:
                return False
            return hmac.compare_digest(_scrypt(password, saved["salt"], saved["params"]), saved["hash"])
        return hmac.compare_digest(f"{user}:{password}".encode(), f"{self.env_user}:{self.env_password}".encode())

    def verify_header(self, header: str) -> bool:
        """Is this Authorization header a valid login? Results are remembered (by hash) until the login changes."""
        if not header or header[:6].lower() != "basic ":
            return False
        key = hashlib.sha256(header.encode(errors="replace")).hexdigest()
        self._refresh()
        if key in self._accepted:
            return True
        try:
            user, sep, password = base64.b64decode(header[6:], validate=True).decode("utf-8").partition(":")
        except (binascii.Error, ValueError):
            return False
        if not sep or not self.check(user, password):
            return False
        with self.lock:
            if len(self._accepted) >= CACHE_SIZE:
                self._accepted.pop(next(iter(self._accepted)))
            self._accepted[key] = True
        return True

    # ------------------------------------------------------------------ changing
    def _throttle(self) -> None:
        now = self.clock()
        self._wrong = [t for t in self._wrong if now - t < WINDOW_S]
        if len(self._wrong) >= ATTEMPTS:
            raise TooManyAttempts(int(WINDOW_S - (now - self._wrong[0])))

    def _require_current(self, current) -> None:
        with self.lock:
            self._throttle()
            if not isinstance(current, str) or not self.check(self.user, current):
                self._wrong.append(self.clock())
                raise LoginError("The current password is not right.")
            self._wrong.clear()

    def change(self, current, new) -> dict:
        """Save a new password (checked), or new=None to go back to the WEB_BASIC_AUTH one. Needs the current password."""
        if not self.env_password and not self.env_user:
            raise LoginError("There is no login to change.")
        with self.lock:
            self._require_current(current)
            if new is None:
                if self.path and os.path.exists(self.path):
                    os.remove(self.path)
            else:
                check_new_password(new, current)
                if not self.path:
                    raise LoginError("This dashboard cannot save a new password.")
                salt = os.urandom(16)
                payload = {"user": self.user, "algo": "scrypt", **SCRYPT, "salt": salt.hex(),
                           "hash": _scrypt(new, salt, SCRYPT).hex(), "changed_at": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()}
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                tmp = self.path + ".tmp"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(payload, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)                              # a crash never leaves half a file
            self._stamp = object()                                      # force the reload below
            self._refresh()
            self._accepted.clear()                                      # the old password stops working at once
        log.warning("dashboard password %s", "reset to the original" if new is None else "changed")
        return self.info()
