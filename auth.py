from flask import session, request, jsonify, redirect
from functools import wraps
from db import _is_user_admin

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Gate that requires an active admin session (email-based DB auth)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_email'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Not authenticated'}), 401
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return decorated

def user_admin_required(f):
    """Gate: user must be logged in AND appear in the admins table (active=TRUE)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Not authenticated'}), 401
            return redirect('/')
        if not _is_user_admin(session.get('email', '')):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Forbidden'}), 403
            return 'Forbidden', 403
        return f(*args, **kwargs)
    return decorated
