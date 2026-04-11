import os
try:
    import psycopg2
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False
from werkzeug.security import generate_password_hash

# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------
def get_db():
    if not _HAS_PSYCOPG2:
        raise RuntimeError('psycopg2 not available — admin auth disabled')
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set — admin auth disabled')
    return psycopg2.connect(db_url, connect_timeout=10)

def _init_db():
    """Create tables if they don't exist (safe to run on every startup)."""
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
        conn.commit()


def _bootstrap_initial_admin():
    """Ensure johngannon@pacesupply.com is always an active admin (idempotent).
    Also sets a password_hash if ADMIN_PASSWORD env var is provided (for the
    legacy /admin/curate login system).  Safe to call on every startup.
    """
    bootstrap_email = 'johngannon@pacesupply.com'
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Ensure the seed admin always exists and is active
                cur.execute("""
                    INSERT INTO admins (email, active, created_by)
                    VALUES (%s, TRUE, 'bootstrap')
                    ON CONFLICT (email) DO UPDATE SET active = TRUE
                """, (bootstrap_email,))
                # Optionally attach a password for the legacy curator login
                initial_password = os.environ.get('ADMIN_PASSWORD', '')
                if initial_password:
                    pw_hash = generate_password_hash(initial_password)
                    cur.execute(
                        'UPDATE admins SET password_hash = %s WHERE email = %s AND password_hash IS NULL',
                        (pw_hash, bootstrap_email)
                    )
                print(f'[admin bootstrap] Seed admin verified: {bootstrap_email}')
            conn.commit()
    except Exception as e:
        print(f'[admin bootstrap] Error: {e}')


def _is_user_admin(email: str) -> bool:
    """Return True if the email is an active admin in the admins table."""
    if not email:
        return False
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1 FROM admins WHERE email = %s AND active = TRUE', (email,))
                return cur.fetchone() is not None
    except Exception:
        return False
