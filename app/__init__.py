import os
from flask import Flask
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from config import Config

db = SQLAlchemy()
migrate = Migrate()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.secret_key = Config.SECRET_KEY

    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "flask_session"
    )
    app.config["SESSION_PERMANENT"] = True
    app.config["SESSION_USE_SIGNER"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    Session(app)
    db.init_app(app)
    migrate.init_app(app, db)

    from app.routes import main_bp
    app.register_blueprint(main_bp)

    from app import models  # noqa: F401

    with app.app_context():
        db.create_all()

    return app
