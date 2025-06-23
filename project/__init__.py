from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import os
import mistune

# Initialize extensions so we can import them in other files
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'routes.login'
login_manager.login_message_category = 'info'


def create_app():
    """Construct the core application."""
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') # Now loads from .env
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
    
    # --- ADD THIS LINE TO LOAD THE GEMINI KEY ---
    app.config['GOOGLE_API_KEY'] = os.environ.get('GOOGLE_API_KEY')
    
    # Initialize extensions with the app
    db.init_app(app)
    login_manager.init_app(app)
    
    with app.app_context():
        # Import parts of our application
        from . import routes
        from . import models

        # Register Blueprints
        app.register_blueprint(routes.bp)
        
        # Create database tables for our models
        db.create_all()

        # Add custom markdown filter to Jinja2
        @app.template_filter('markdown')
        def markdown_filter(s):
            return mistune.html(s)

        return app

@login_manager.user_loader
def load_user(user_id):
    from .models import User
    return User.query.get(int(user_id))