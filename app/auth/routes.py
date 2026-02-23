from flask import Blueprint, redirect, request, session, url_for
import msal
from config import Config

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

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
    
    # Exchange the code for a token
    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=Config.SCOPE,
        redirect_uri=Config.REDIRECT_URI
    )
    
    if 'error' in result:
        return f"Login error: {result.get('error_description')}", 400
    
    # Store user info in session
    session['user'] = result.get('id_token_claims')
    session['access_token'] = result.get('access_token')
    
    return redirect(url_for('home'))

@auth_bp.route('/logout')
def logout():
    """Clears session and logs out from Microsoft"""
    session.clear()
    
    logout_url = (
        f"{Config.AUTHORITY}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri=http://localhost:5000"
    )
    return redirect(logout_url)
