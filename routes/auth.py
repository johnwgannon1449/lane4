"""routes/auth.py — User authentication endpoints."""
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Blueprint, request, jsonify, session
from db import get_db, get_dict_cursor, using_sqlite
from db import _is_user_admin

auth_bp = Blueprint('auth', __name__)


def _is_duplicate_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return 'unique' in msg or 'duplicate' in msg


@auth_bp.route('/api/auth/register', methods=['POST'])
def auth_register():
    body = request.get_json(silent=True) or {}
    email    = (body.get('email') or '').strip().lower()
    password = (body.get('password') or '').strip()
    if not email or '@' not in email:
        return jsonify({'error': 'A valid email is required.'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters.'}), 400
    pw_hash = generate_password_hash(password)
    try:
        with get_db() as conn:
            if using_sqlite():
                with conn:
                    cur = conn.execute(
                        'INSERT INTO users (email, password_hash) VALUES (?, ?)',
                        (email, pw_hash)
                    )
                    user_id = cur.lastrowid
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        'INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id',
                        (email, pw_hash)
                    )
                    user_id = cur.fetchone()[0]
        session['user_id'] = user_id
        session['email']   = email
        return jsonify({'ok': True, 'email': email})
    except Exception as e:
        if _is_duplicate_error(e):
            return jsonify({'error': 'An account with that email already exists.'}), 409
        return jsonify({'error': str(e)}), 500


@auth_bp.route('/api/auth/login', methods=['POST'])
def auth_login():
    body = request.get_json(silent=True) or {}
    email    = (body.get('email') or '').strip().lower()
    password = (body.get('password') or '').strip()
    try:
        with get_db() as conn:
            with get_dict_cursor(conn) as cur:
                sql = (
                    'SELECT id, password_hash FROM users WHERE email = ?'
                    if using_sqlite()
                    else 'SELECT id, password_hash FROM users WHERE email = %s'
                )
                cur.execute(sql, (email,))
                row = cur.fetchone()
        if not row or not check_password_hash(row['password_hash'], password):
            return jsonify({'error': 'Incorrect email or password.'}), 401
        session['user_id'] = row['id']
        session['email']   = email
        return jsonify({'ok': True, 'email': email})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@auth_bp.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return jsonify({'ok': True})


@auth_bp.route('/api/auth/me', methods=['GET'])
def auth_me():
    if 'user_id' not in session:
        return jsonify({'authenticated': False}), 401
    email = session.get('email', '')
    return jsonify({
        'authenticated': True,
        'email':         email,
        'user_id':       session['user_id'],
        'is_admin':      _is_user_admin(email),
    })
