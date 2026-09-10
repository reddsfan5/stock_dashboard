"""本地多用户账户与会话（SQLite）。

交互 Web 用 HTTP-only Cookie 会话；密码使用 stdlib hashlib.scrypt。
首次无用户时自动创建管理员 xiaodong（密码来自 STOCK_ADMIN_PASSWORD 或一次性随机打印）。
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "state" / "users.sqlite3"

SESSION_COOKIE = "stock_session"
SESSION_DAYS = 14
ROLES = ("admin", "member")
BOOTSTRAP_USERNAME = "xiaodong"

# scrypt 参数：本地单机够用；勿随意改动已有哈希格式。
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds")


def hash_password(password: str) -> str:
    text = str(password or "")
    if len(text) < 6:
        raise ValueError("密码至少 6 位")
    if len(text) > 200:
        raise ValueError("密码过长")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        text.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "scrypt$%d$%d$%d$%s$%s" % (
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, n_s, r_s, p_s, salt_b64, hash_b64 = str(password_hash or "").split("$", 5)
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(hash_b64.encode("ascii"))
        digest = hashlib.scrypt(
            str(password or "").encode("utf-8"),
            salt=salt,
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
        )
        return secrets.compare_digest(digest, expected)
    except Exception:
        return False


def require_user_id(user_id) -> int:
    try:
        value = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("user_id 必须是整数") from exc
    if value <= 0:
        raise ValueError("user_id 无效")
    return value


def table_has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    names = {row[1] if not isinstance(row, sqlite3.Row) else row["name"] for row in rows}
    return column in names


def ensure_user_id_column(
    connection: sqlite3.Connection,
    table: str,
    admin_user_id: int,
    *,
    index_name: str | None = None,
) -> None:
    """幂等：为个人表补 user_id 并回填到管理员。"""
    if not table_has_column(connection, table, "user_id"):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
    connection.execute(
        f"UPDATE {table} SET user_id=? WHERE user_id IS NULL OR user_id=0",
        (int(admin_user_id),),
    )
    idx = index_name or f"idx_{table}_user_id"
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS {idx} ON {table}(user_id)"
    )


class UserRepository:
    """用户与会话仓储。"""

    def __init__(self, path=DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user
                    ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_expires
                    ON sessions(expires_at);
                """
            )

    @staticmethod
    def _decode_user(row: sqlite3.Row) -> dict:
        return {
            "id": int(row["id"]),
            "username": row["username"],
            "display_name": row["display_name"] or row["username"],
            "role": row["role"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
        }

    def count_users(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def list_users(self, *, enabled_only: bool = False) -> list[dict]:
        clauses = []
        if enabled_only:
            clauses.append("enabled=1")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM users{where} ORDER BY id"
            ).fetchall()
        return [self._decode_user(row) for row in rows]

    def get_user(self, user_id: int = None, *, username: str = None) -> dict:
        with self._connect() as connection:
            if username is not None:
                row = connection.execute(
                    "SELECT * FROM users WHERE username=?",
                    (str(username).strip().lower(),),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM users WHERE id=?",
                    (int(user_id),),
                ).fetchone()
        if row is None:
            raise LookupError("用户不存在")
        return self._decode_user(row)

    def get_first_admin_id(self) -> Optional[int]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM users WHERE role='admin' AND enabled=1 ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                return int(row["id"])
            row = connection.execute(
                "SELECT id FROM users ORDER BY id LIMIT 1"
            ).fetchone()
            return int(row["id"]) if row else None

    def create_user(
        self,
        *,
        username: str,
        password: str,
        display_name: str = "",
        role: str = "member",
        enabled: bool = True,
    ) -> dict:
        username = str(username or "").strip().lower()
        if not username or len(username) > 40:
            raise ValueError("用户名无效")
        if not all(ch.isalnum() or ch in "._-" for ch in username):
            raise ValueError("用户名仅允许字母数字与 ._-")
        role = str(role or "member").strip()
        if role not in ROLES:
            raise ValueError(f"角色必须是 {', '.join(ROLES)}")
        display_name = str(display_name or username).strip()[:80]
        password_hash = hash_password(password)
        stamp = _iso()
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    """INSERT INTO users
                       (username,display_name,password_hash,role,enabled,created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        username,
                        display_name,
                        password_hash,
                        role,
                        1 if enabled else 0,
                        stamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("用户名已存在") from exc
            user_id = cursor.lastrowid
        return self.get_user(user_id)

    def authenticate(self, username: str, password: str) -> dict:
        username = str(username or "").strip().lower()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
        if row is None or not row["enabled"]:
            raise LookupError("用户名或密码错误")
        if not verify_password(password, row["password_hash"]):
            raise LookupError("用户名或密码错误")
        return self._decode_user(row)

    def create_session(self, user_id: int, *, days: int = SESSION_DAYS) -> dict:
        user_id = require_user_id(user_id)
        token = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(days=max(1, int(days)))
        with self._connect() as connection:
            user = connection.execute(
                "SELECT id,enabled FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if user is None or not user["enabled"]:
                raise LookupError("用户不存在或已禁用")
            connection.execute(
                """INSERT INTO sessions (id,user_id,created_at,expires_at,last_seen_at)
                   VALUES (?,?,?,?,?)""",
                (token, user_id, _iso(now), _iso(expires), _iso(now)),
            )
        return {
            "token": token,
            "user_id": user_id,
            "expires_at": _iso(expires),
        }

    def resolve_session(self, token: str) -> Optional[dict]:
        token = str(token or "").strip()
        if not token:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """SELECT s.id AS session_id, s.expires_at, s.user_id,
                          u.username, u.display_name, u.role, u.enabled
                   FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.id=?""",
                (token,),
            ).fetchone()
            if row is None:
                return None
            try:
                expires = datetime.fromisoformat(row["expires_at"])
            except ValueError:
                connection.execute("DELETE FROM sessions WHERE id=?", (token,))
                return None
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=_now().tzinfo)
            if expires < _now() or not row["enabled"]:
                connection.execute("DELETE FROM sessions WHERE id=?", (token,))
                return None
            connection.execute(
                "UPDATE sessions SET last_seen_at=? WHERE id=?",
                (_iso(), token),
            )
        return {
            "session_id": row["session_id"],
            "id": int(row["user_id"]),
            "username": row["username"],
            "display_name": row["display_name"] or row["username"],
            "role": row["role"],
        }

    def revoke_session(self, token: str) -> None:
        token = str(token or "").strip()
        if not token:
            return
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE id=?", (token,))

    def ensure_bootstrap_admin(self) -> dict:
        """若无用户则创建管理员 xiaodong；返回 {user, generated_password|None}。"""
        if self.count_users() > 0:
            admin_id = self.get_first_admin_id()
            return {"user": self.get_user(admin_id), "generated_password": None, "created": False}
        password = os.environ.get("STOCK_ADMIN_PASSWORD", "").strip()
        generated = None
        if not password:
            generated = secrets.token_urlsafe(12)
            password = generated
        user = self.create_user(
            username=BOOTSTRAP_USERNAME,
            password=password,
            display_name="小东",
            role="admin",
        )
        if generated:
            print(
                f"✓ 已创建默认管理员「{BOOTSTRAP_USERNAME}」，一次性密码："
                f"{generated}\n  请立即登录后自行保管；密码不会写入仓库。"
                " 也可事先设置环境变量 STOCK_ADMIN_PASSWORD。"
            )
        else:
            print(
                f"✓ 已创建默认管理员「{BOOTSTRAP_USERNAME}」"
                "（密码来自环境变量 STOCK_ADMIN_PASSWORD）"
            )
        return {"user": user, "generated_password": generated, "created": True}


def bootstrap_admin(path=DEFAULT_DB_PATH) -> dict:
    return UserRepository(path).ensure_bootstrap_admin()


def migrate_personal_data_to_admin(admin_user_id: int | None = None, *, users_path=DEFAULT_DB_PATH) -> dict:
    """幂等：给各个人库表补 user_id，并把历史行归到管理员。"""
    users = UserRepository(users_path)
    if admin_user_id is None:
        boot = users.ensure_bootstrap_admin()
        admin_user_id = boot["user"]["id"]
    admin_user_id = require_user_id(admin_user_id)
    state = PROJECT_DIR / "state"
    summary = {"admin_user_id": admin_user_id, "tables": []}

    def _migrate(db_name: str, tables: list[str], extra=None):
        path = state / db_name
        if not path.exists():
            # 触发仓储建表后再迁移
            return
        connection = sqlite3.connect(path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            if extra:
                extra(connection, admin_user_id)
            for table in tables:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not exists:
                    continue
                ensure_user_id_column(connection, table, admin_user_id)
                summary["tables"].append(f"{db_name}:{table}")
            connection.commit()
        finally:
            connection.close()

    def _watchlist_unique(connection, admin_id):
        # 重建 track_result 唯一约束为含 user_id
        exists = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='track_result'"
        ).fetchone()
        if not exists:
            return
        ensure_user_id_column(connection, "track_result", admin_id)
        sql = exists[0] or ""
        if "user_id" in sql and "UNIQUE(user_id" in sql.replace(" ", ""):
            return
        # 若旧 UNIQUE(code,screen_date,track_date) 仍在，重建表
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS track_result_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item_id INTEGER REFERENCES watch_item(id),
                code TEXT NOT NULL,
                screen_date TEXT NOT NULL,
                track_date TEXT NOT NULL,
                screen_close REAL,
                track_close REAL,
                return_pct REAL,
                pattern_failed INTEGER,
                source_module TEXT NOT NULL DEFAULT '',
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(user_id, code, screen_date, track_date)
            );
            INSERT OR IGNORE INTO track_result_v2
                (id,user_id,item_id,code,screen_date,track_date,screen_close,track_close,
                 return_pct,pattern_failed,source_module,meta_json,created_at)
            SELECT id,
                   COALESCE(NULLIF(user_id,0), %d),
                   item_id,code,screen_date,track_date,screen_close,track_close,
                   return_pct,pattern_failed,source_module,meta_json,created_at
            FROM track_result;
            DROP TABLE track_result;
            ALTER TABLE track_result_v2 RENAME TO track_result;
            CREATE INDEX IF NOT EXISTS idx_track_date ON track_result(track_date, screen_date);
            CREATE INDEX IF NOT EXISTS idx_track_result_user_id ON track_result(user_id);
            """
            % int(admin_id)
        )

    _migrate("watchlist.sqlite3", ["watch_item"], extra=_watchlist_unique)
    # track_result handled in extra; also ensure watch_item done
    _migrate("stock_journal.sqlite3", ["journal_case", "journal_entry"])
    _migrate("training_sessions.sqlite3", ["training_run"])
    _migrate("hypotheses.sqlite3", ["hypothesis"])
    _migrate("market_news.sqlite3", ["market_news_impact"])
    return summary


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    result = {}
    for part in str(cookie_header or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def session_cookie_header(token: str, *, max_age: int = SESSION_DAYS * 86400) -> str:
    return (
        f"{SESSION_COOKIE}={token}; HttpOnly; Path=/; SameSite=Lax; Max-Age={int(max_age)}"
    )


def clear_session_cookie_header() -> str:
    return f"{SESSION_COOKIE}=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0"
