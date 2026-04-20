import os
import sqlite3
from contextlib import contextmanager
try:
    import psycopg2
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False
from werkzeug.security import generate_password_hash

_SQLITE_PATH = os.path.join(os.path.dirname(__file__), 'lane4_local.db')


def using_sqlite() -> bool:
    return not bool(os.environ.get('DATABASE_URL'))


@contextmanager
def get_dict_cursor(conn):
    if using_sqlite():
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()
        return
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        yield cur

# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------
def get_db():
    db_url = os.environ.get('DATABASE_URL')
    if db_url:
        if not _HAS_PSYCOPG2:
            raise RuntimeError('psycopg2 not available — database auth disabled')
        return psycopg2.connect(db_url, connect_timeout=10)

    conn = sqlite3.connect(_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

def _init_db():
    """Create tables if they don't exist (safe to run on every startup)."""
    if using_sqlite():
        with get_db() as conn:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id            INTEGER PRIMARY KEY AUTOINCREMENT,
                        email         TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        created_at    TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sync_data (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id    INTEGER NOT NULL,
                        data_key   TEXT NOT NULL,
                        data_value TEXT,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (user_id, data_key),
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS admins (
                        id            INTEGER PRIMARY KEY AUTOINCREMENT,
                        email         TEXT UNIQUE NOT NULL,
                        password_hash TEXT,
                        created_by    TEXT,
                        created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                        active        INTEGER NOT NULL DEFAULT 1
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS school_content_cache (
                        id               INTEGER PRIMARY KEY AUTOINCREMENT,
                        school_name      TEXT UNIQUE NOT NULL,
                        known_for        TEXT,
                        campus_life_main TEXT,
                        campus_life_more TEXT,
                        generated_at     TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # Cost Layer 1 columns — added via ALTER TABLE so existing DBs migrate safely.
                # SQLite does not support ADD COLUMN IF NOT EXISTS; catch duplicate errors.
                for _col in [
                    'ALTER TABLE school_content_cache ADD COLUMN coa INTEGER',
                    'ALTER TABLE school_content_cache ADD COLUMN merit_offered INTEGER',
                    'ALTER TABLE school_content_cache ADD COLUMN merit_range_low INTEGER',
                    'ALTER TABLE school_content_cache ADD COLUMN merit_range_high INTEGER',
                    'ALTER TABLE school_content_cache ADD COLUMN merit_notes TEXT',
                    'ALTER TABLE school_content_cache ADD COLUMN need_based_headline TEXT',
                ]:
                    try:
                        conn.execute(_col)
                    except Exception:
                        pass  # column already exists
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS program_content_cache (
                        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                        school_name            TEXT NOT NULL,
                        major                  TEXT NOT NULL,
                        minor                  TEXT NOT NULL DEFAULT '',
                        academic_program_main  TEXT,
                        academic_program_more  TEXT,
                        minor_content          TEXT,
                        generated_at           TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at             TEXT DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (school_name, major, minor)
                    )
                """)
                for _col in [
                    'ALTER TABLE school_content_cache ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP',
                    'ALTER TABLE program_content_cache ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP',
                ]:
                    try:
                        conn.execute(_col)
                    except Exception:
                        pass  # column already exists
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS minor_content_cache (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        school_name  TEXT NOT NULL,
                        minor_name   TEXT NOT NULL,
                        minor_content TEXT,
                        generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (school_name, minor_name)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS school_swim_cache (
                        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                        school_name           TEXT UNIQUE NOT NULL,
                        conference            TEXT,
                        division              TEXT,
                        program_strength_desc TEXT,
                        is_super_powerhouse   INTEGER,
                        top_event_benchmarks  TEXT,
                        roster_depth_info     TEXT,
                        generated_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at            TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
        return

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            SERIAL PRIMARY KEY,
                    email         TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at    TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sync_data (
                    id         SERIAL PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    data_key   TEXT NOT NULL,
                    data_value JSONB,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (user_id, data_key)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    id            SERIAL PRIMARY KEY,
                    email         TEXT UNIQUE NOT NULL,
                    password_hash TEXT,
                    created_by    TEXT,
                    created_at    TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Safe migrations for existing deployments
            cur.execute("""
                ALTER TABLE admins
                    ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE
            """)
            cur.execute("""
                ALTER TABLE admins
                    ALTER COLUMN password_hash DROP NOT NULL
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS school_content_cache (
                    id               SERIAL PRIMARY KEY,
                    school_name      TEXT UNIQUE NOT NULL,
                    known_for        TEXT,
                    campus_life_main TEXT,
                    campus_life_more TEXT,
                    generated_at     TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Cost Layer 1 columns — ADD COLUMN IF NOT EXISTS is safe on PostgreSQL
            for _col in [
                'ALTER TABLE school_content_cache ADD COLUMN IF NOT EXISTS coa INTEGER',
                'ALTER TABLE school_content_cache ADD COLUMN IF NOT EXISTS merit_offered BOOLEAN',
                'ALTER TABLE school_content_cache ADD COLUMN IF NOT EXISTS merit_range_low INTEGER',
                'ALTER TABLE school_content_cache ADD COLUMN IF NOT EXISTS merit_range_high INTEGER',
                'ALTER TABLE school_content_cache ADD COLUMN IF NOT EXISTS merit_notes TEXT',
                'ALTER TABLE school_content_cache ADD COLUMN IF NOT EXISTS need_based_headline TEXT',
            ]:
                cur.execute(_col)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS program_content_cache (
                    id                     SERIAL PRIMARY KEY,
                    school_name            TEXT NOT NULL,
                    major                  TEXT NOT NULL,
                    minor                  TEXT NOT NULL DEFAULT '',
                    academic_program_main  TEXT,
                    academic_program_more  TEXT,
                    minor_content          TEXT,
                    generated_at           TIMESTAMPTZ DEFAULT NOW(),
                    updated_at             TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (school_name, major, minor)
                )
            """)
            for _col in [
                'ALTER TABLE school_content_cache ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()',
                'ALTER TABLE program_content_cache ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()',
            ]:
                cur.execute(_col)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS minor_content_cache (
                    id            SERIAL PRIMARY KEY,
                    school_name   TEXT NOT NULL,
                    minor_name    TEXT NOT NULL,
                    minor_content TEXT,
                    generated_at  TIMESTAMPTZ DEFAULT NOW(),
                    updated_at    TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (school_name, minor_name)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS school_swim_cache (
                    id                    SERIAL PRIMARY KEY,
                    school_name           TEXT UNIQUE NOT NULL,
                    conference            TEXT,
                    division              TEXT,
                    program_strength_desc TEXT,
                    is_super_powerhouse   BOOLEAN,
                    top_event_benchmarks  JSONB,
                    roster_depth_info     JSONB,
                    generated_at          TIMESTAMPTZ DEFAULT NOW(),
                    updated_at            TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        conn.commit()


def _bootstrap_initial_admin():
    """Ensure johngannon@pacesupply.com is always an active admin (idempotent).
    Also sets a password_hash if ADMIN_PASSWORD env var is provided (for the
    legacy /admin/curate login system).  Safe to call on every startup.
    """
    bootstrap_email = 'johngannon@pacesupply.com'
    try:
        with get_db() as conn:
            if using_sqlite():
                with conn:
                    conn.execute("""
                        INSERT INTO admins (email, active, created_by)
                        VALUES (?, 1, 'bootstrap')
                        ON CONFLICT(email) DO UPDATE SET active = 1
                    """, (bootstrap_email,))
                    initial_password = os.environ.get('ADMIN_PASSWORD', '')
                    if initial_password:
                        pw_hash = generate_password_hash(initial_password)
                        conn.execute(
                            'UPDATE admins SET password_hash = ? WHERE email = ? AND password_hash IS NULL',
                            (pw_hash, bootstrap_email)
                        )
            else:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO admins (email, active, created_by)
                        VALUES (%s, TRUE, 'bootstrap')
                        ON CONFLICT (email) DO UPDATE SET active = TRUE
                    """, (bootstrap_email,))
                    initial_password = os.environ.get('ADMIN_PASSWORD', '')
                    if initial_password:
                        pw_hash = generate_password_hash(initial_password)
                        cur.execute(
                            'UPDATE admins SET password_hash = %s WHERE email = %s AND password_hash IS NULL',
                            (pw_hash, bootstrap_email)
                        )
                    conn.commit()
            print(f'[admin bootstrap] Seed admin verified: {bootstrap_email}')
    except Exception as e:
        print(f'[admin bootstrap] Error: {e}')


def _is_user_admin(email: str) -> bool:
    """Return True if the email is an active admin in the admins table."""
    if not email:
        return False
    try:
        with get_db() as conn:
            if using_sqlite():
                cur = conn.cursor()
                cur.execute('SELECT 1 FROM admins WHERE email = ? AND active = 1', (email,))
                return cur.fetchone() is not None
            with conn.cursor() as cur:
                cur.execute('SELECT 1 FROM admins WHERE email = %s AND active = TRUE', (email,))
                return cur.fetchone() is not None
    except Exception:
        return False
