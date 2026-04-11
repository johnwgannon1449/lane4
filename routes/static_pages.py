"""routes/static_pages.py — Page serve routes for the Lane4 SPA."""
from flask import Blueprint, send_from_directory

static_pages_bp = Blueprint('static_pages', __name__)


@static_pages_bp.route('/')
def index():
    resp = send_from_directory('static', 'index.html')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@static_pages_bp.route('/login', methods=['GET'])
def login_page():
    """Standalone login page for returning swimmers."""
    return send_from_directory('static', 'login.html')


@static_pages_bp.route('/debug-ui')
def debug_ui():
    return send_from_directory('static', 'debug_ui.html')
