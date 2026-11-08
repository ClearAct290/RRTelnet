# -*- coding: utf-8 -*-
"""
Retro Recoil - player database (online.db, sqlite3).

Passwords are NEVER stored.  Only a salted, deliberately slow hash is kept, in a
self-describing string so the cost can be raised later without breaking old rows:

    scrypt$16384$8$1$<salt hex>$<hash hex>          (default, hashlib.scrypt)
    pbkdf2_sha256$600000$<salt hex>$<hash hex>      (fallback if scrypt is missing)

* the salt is 16 random bytes from ``secrets`` (OS CSPRNG), unique per account
* verification uses ``hmac.compare_digest`` (constant time)
* logging in with an unknown name still burns one hash, so response time does not
  reveal which usernames exist
* a hash made with weaker/older parameters is silently upgraded on the next
  successful login (this also migrates the very first release's pbkdf2 rows)

Schema:
  accounts(username PK nocase, pw, created_at, last_login, salt, pwhash)   salt/pwhash = legacy columns
  stats(username PK nocase, kills, deaths, reloads, games, last5_deaths "d,d,d,d,d" most recent last)

KDR is always kills / max(1, deaths), computed on read - never stored, so it cannot drift.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
PBKDF2_ITERS = 600_000

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,12}$")
RESERVED_RE = re.compile(r"^(bot\d*|you|system|server|admin|root)$", re.I)
PASSWORD_MIN, PASSWORD_MAX = 8, 64

_ADJ = ["Red", "Iron", "Neon", "Rapid", "Night", "Steel", "Shadow", "Pixel", "Retro", "Turbo",
        "Quick", "Grim", "Static", "Bright", "Last", "Lone"]
_NOUN = ["Fox", "Hawk", "Wolf", "Byte", "Striker", "Runner", "Sniper", "Ghost", "Raptor", "Ranger",
         "Viper", "Rebel", "Trooper", "Drifter", "Comet", "Ace"]
_rng = secrets.SystemRandom()


def random_username():
    return f"{_rng.choice(_ADJ)}{_rng.choice(_NOUN)}{_rng.randint(1, 99)}"[:12]


# ---------------------------------------------------------------- password hashing
def _scrypt(pw, salt, n, r, p):
    return hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32,
                          maxmem=128 * n * r * p * 2 + (1 << 20))


def hash_password(password):
    """-> self-describing hash string (never reversible, different every call)."""
    salt = secrets.token_bytes(16)
    if hasattr(hashlib, "scrypt"):
        try:
            dk = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
            return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${dk.hex()}"
        except (ValueError, MemoryError, OSError):
            pass
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERS)
    return f"pbkdf2_sha256${PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    try:
        parts = stored.split("$")
        if parts[0] == "scrypt" and len(parts) == 6:
            n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
            salt, want = bytes.fromhex(parts[4]), bytes.fromhex(parts[5])
            got = _scrypt(password, salt, n, r, p)
        elif parts[0] == "pbkdf2_sha256" and len(parts) == 4:
            salt, want = bytes.fromhex(parts[2]), bytes.fromhex(parts[3])
            got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(parts[1]))
        else:
            return False
        return hmac.compare_digest(got, want)
    except (ValueError, TypeError, MemoryError, OSError):
        return False


def needs_rehash(stored):
    parts = stored.split("$")
    try:
        if parts[0] == "scrypt":
            return (int(parts[1]), int(parts[2]), int(parts[3])) < (SCRYPT_N, SCRYPT_R, SCRYPT_P)
        if parts[0] == "pbkdf2_sha256":
            return hasattr(hashlib, "scrypt") or int(parts[1]) < PBKDF2_ITERS
    except (ValueError, IndexError):
        pass
    return True


def _legacy_check(password, salt, pwhash):
    """The first release stored pbkdf2-sha256 (100k) with a hex salt used as text."""
    try:
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
        return hmac.compare_digest(got, pwhash)
    except (ValueError, TypeError):
        return False


_DUMMY = None


def _dummy_hash():
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_hex(8))
    return _DUMMY


def validate_username(username):
    if not USERNAME_RE.match(username or ""):
        return "3-12 chars: letters, digits, _"
    if RESERVED_RE.match(username):
        return "That name is reserved"
    return ""


def validate_password(password):
    if len(password or "") < PASSWORD_MIN:
        return f"Password needs {PASSWORD_MIN}+ characters"
    if len(password) > PASSWORD_MAX:
        return f"Password is too long (max {PASSWORD_MAX})"
    return ""


# ---------------------------------------------------------------- database
SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    username   TEXT PRIMARY KEY COLLATE NOCASE,
    pw         TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT 0,
    last_login REAL NOT NULL DEFAULT 0,
    salt       TEXT NOT NULL DEFAULT '',
    pwhash     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS stats (
    username     TEXT PRIMARY KEY COLLATE NOCASE,
    kills        INTEGER NOT NULL DEFAULT 0,
    deaths       INTEGER NOT NULL DEFAULT 0,
    reloads      INTEGER NOT NULL DEFAULT 0,
    games        INTEGER NOT NULL DEFAULT 0,
    last5_deaths TEXT NOT NULL DEFAULT ''
);
"""


class Db:
    """One connection guarded by a lock (sqlite3 objects are not thread-safe and the
    slow password hashing is run in worker threads by the server)."""

    def __init__(self, path="online.db"):
        self.path = path
        self._lock = threading.Lock()
        fresh = not os.path.exists(path)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(accounts)")}
        if "pw" not in cols:                       # database made by the first release
            self.conn.execute("ALTER TABLE accounts ADD COLUMN pw TEXT NOT NULL DEFAULT ''")
        if "last_login" not in cols:
            self.conn.execute("ALTER TABLE accounts ADD COLUMN last_login REAL NOT NULL DEFAULT 0")
        self.conn.commit()
        if fresh:
            try:
                os.chmod(path, 0o600)               # holds password hashes: owner only (no-op on Windows)
            except OSError:
                pass

    def close(self):
        with self._lock:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass

    # ---- accounts
    def username_taken(self, username):
        with self._lock:
            cur = self.conn.execute("SELECT 1 FROM accounts WHERE username=? COLLATE NOCASE", (username,))
            return cur.fetchone() is not None

    def unique_random_username(self):
        for _ in range(60):
            u = random_username()
            if not self.username_taken(u):
                return u
        return (random_username()[:8] + str(secrets.randbelow(9000) + 1000))[:12]

    def create_account(self, username, password):
        """-> (ok, error message).  Hashing happens outside the lock."""
        err = validate_username(username) or validate_password(password)
        if err:
            return False, err
        pw = hash_password(password)
        with self._lock:
            try:
                self.conn.execute("INSERT INTO accounts(username,pw,created_at,last_login) VALUES (?,?,?,?)",
                                  (username, pw, time.time(), time.time()))
                self.conn.execute("INSERT OR IGNORE INTO stats(username) VALUES (?)", (username,))
                self.conn.commit()
                return True, ""
            except sqlite3.IntegrityError:
                return False, "Username taken"

    def authenticate(self, username, candidates):
        """Check username + any of the candidate passwords (a PETSCII client may type
        a password in a different letter case than the PC it was created on).
        Returns the account's stored username (original capitalisation) or None."""
        with self._lock:
            row = self.conn.execute("SELECT username, pw, salt, pwhash FROM accounts WHERE username=? COLLATE NOCASE",
                                    (username,)).fetchone()
        if row is None:
            verify_password("x", _dummy_hash())     # equal work: no username oracle
            return None
        uname, pw, salt, pwhash = row
        ok_pw = None
        if pw:
            for cand in candidates:
                if verify_password(cand, pw):
                    ok_pw = cand
                    break
        elif salt and pwhash:                       # legacy row, upgraded below
            for cand in candidates:
                if _legacy_check(cand, salt, pwhash):
                    ok_pw = cand
                    break
        else:
            verify_password("x", _dummy_hash())
        if ok_pw is None:
            return None
        new_hash = hash_password(ok_pw) if (not pw or needs_rehash(pw)) else None
        with self._lock:
            if new_hash:
                self.conn.execute("UPDATE accounts SET pw=?, salt='', pwhash='' WHERE username=? COLLATE NOCASE",
                                  (new_hash, uname))
            self.conn.execute("UPDATE accounts SET last_login=? WHERE username=? COLLATE NOCASE", (time.time(), uname))
            self.conn.commit()
        return uname

    # ---- stats
    def get_stats(self, username):
        with self._lock:
            row = self.conn.execute(
                "SELECT kills, deaths, reloads, games, last5_deaths FROM stats WHERE username=? COLLATE NOCASE",
                (username,)).fetchone()
        if not row:
            return dict(kills=0, deaths=0, reloads=0, games=0, last5=[])
        kills, deaths, reloads, games, last5 = row
        return dict(kills=kills, deaths=deaths, reloads=reloads, games=games,
                    last5=[int(x) for x in last5.split(",") if x != ""])

    def record_match(self, username, kills, deaths, reloads):
        """Add one match's totals onto the player's lifetime stats."""
        with self._lock:
            row = self.conn.execute(
                "SELECT kills, deaths, reloads, games, last5_deaths FROM stats WHERE username=? COLLATE NOCASE",
                (username,)).fetchone()
            if row is None:
                self.conn.execute("INSERT OR IGNORE INTO stats(username) VALUES (?)", (username,))
                k0 = d0 = r0 = g0 = 0
                l5 = ""
            else:
                k0, d0, r0, g0, l5 = row
            last5 = [int(x) for x in l5.split(",") if x != ""] + [int(deaths)]
            self.conn.execute(
                "UPDATE stats SET kills=?, deaths=?, reloads=?, games=?, last5_deaths=? WHERE username=? COLLATE NOCASE",
                (k0 + kills, d0 + deaths, r0 + reloads, g0 + 1, ",".join(str(x) for x in last5[-5:]), username))
            self.conn.commit()

    def _ranking(self):
        with self._lock:
            rows = self.conn.execute("SELECT username, kills, deaths FROM stats WHERE kills>0 OR deaths>0").fetchall()
        out = [(u, k, d, k / max(1, d)) for u, k, d in rows]
        out.sort(key=lambda r: (-r[3], -r[1], r[0].lower()))     # higher KDR first, then more kills
        return out

    def leaderboard_top(self, n=5):
        return self._ranking()[:n]

    def leaderboard_rank(self, username):
        """(rank 1-based or None, total ranked players, full ranking rows)."""
        rows = self._ranking()
        for i, r in enumerate(rows):
            if r[0].lower() == username.lower():
                return i + 1, len(rows), rows
        return None, len(rows), rows
