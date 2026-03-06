from flask import (Blueprint, redirect, request,
                   session, url_for)
import msal
from config import Config

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


# ── Local Test Users — Development Only ──────────────────────
# Remove before production deployment

LOCAL_TEST_USERS = {
    'admin': {
        'name':               'Test Admin',
        'preferred_username': 'admin@test.local',
        'oid':                'test-admin-001',
        'role':               'admin'
    },
    'contributor': {
        'name':               'Test Contributor',
        'preferred_username': 'contributor@test.local',
        'oid':                'test-contributor-001',
        'role':               'contributor'
    },
    'operator': {
        'name':               'Test Operator',
        'preferred_username': 'operator@test.local',
        'oid':                'test-operator-001',
        'role':               'operator'
    },
    'reader': {
        'name':               'Test Reader',
        'preferred_username': 'reader@test.local',
        'oid':                'test-reader-001',
        'role':               'reader'
    }
}


def build_msal_app():
    """Creates an MSAL confidential client app"""
    return msal.ConfidentialClientApplication(
        Config.CLIENT_ID,
        authority=Config.AUTHORITY,
        client_credential=Config.CLIENT_SECRET
    )


@auth_bp.route('/login')
def login():
    """Redirects user to Microsoft login page"""
    msal_app = build_msal_app()

    auth_url = msal_app.get_authorization_request_url(
        Config.SCOPE,
        redirect_uri=Config.REDIRECT_URI,
        state=session.get('state', 'random_state')
    )
    return redirect(auth_url)


@auth_bp.route('/callback')
def callback():
    """Microsoft redirects back here after login"""
    code = request.args.get('code')

    if not code:
        return "Login failed — no code received", 400

    msal_app = build_msal_app()

    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=Config.SCOPE,
        redirect_uri=Config.REDIRECT_URI
    )

    if 'error' in result:
        return (
            f"Login error: "
            f"{result.get('error_description')}"
        ), 400

    # Store user info in session
    user_claims             = result.get('id_token_claims')
    session['user']         = user_claims
    session['access_token'] = result.get('access_token')

    # ── Get Azure RBAC role ───────────────────────────────────
    try:
        from app.vm.rbac_service import get_user_portal_role
        object_id   = user_claims.get('oid', '')
        user_email  = user_claims.get(
                          'preferred_username', ''
                      )
        portal_role = get_user_portal_role(
            object_id, user_email
        )
        session['portal_role'] = portal_role
        print(f"[OK] Portal role set: {portal_role}")
    except Exception as e:
        print(f"[WARN] Role lookup failed: {e}")
        session['portal_role'] = 'reader'
    # ─────────────────────────────────────────────────────────

    return redirect(url_for('admin.dashboard'))


@auth_bp.route('/logout')
def logout():
    """Clears session and logs out from Microsoft"""
    session.clear()

    logout_url = (
        f"{Config.AUTHORITY}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={Config.REDIRECT_URI}"
    )
    return redirect(logout_url)


@auth_bp.route('/test-login/<role>')
def test_login(role):
    """
    Local test login — bypasses Entra ID
    Development only — remove before production
    """
    if not Config.DEBUG:
        return "Test login disabled in production", 403

    user = LOCAL_TEST_USERS.get(role)
    if not user:
        return f"Unknown role: {role}", 400

    session['user']         = user
    session['portal_role']  = user['role']
    session['access_token'] = 'test-token'

    print(f"[OK] Test login: {user['name']} ({role})")

    return redirect(url_for('admin.dashboard'))

