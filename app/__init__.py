from flask import Flask, session, redirect, url_for
from flask_session import Session
from config import Config
from models import db

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Sessions
    app.config['SESSION_TYPE'] = 'filesystem'
    Session(app)

    # Database
    db.init_app(app)

    # Register Blueprints
    from app.auth.routes import auth_bp
    app.register_blueprint(auth_bp)

    from app.vm.routes import vm_bp
    app.register_blueprint(vm_bp)

    # Create DB tables on first run
    with app.app_context():
        db.create_all()

    @app.route('/')
    def home():
        if 'user' not in session:
            return redirect(url_for('auth.login'))
        return redirect(url_for('vm.dashboard'))

    return app