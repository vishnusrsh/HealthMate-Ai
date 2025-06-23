from project import create_app, db
from project.models import User
from dotenv import load_dotenv # Import the function
import click

# Load environment variables from .env file
load_dotenv()

# Create the app instance using the application factory
app = create_app()

@app.cli.command("create-superuser")
@click.argument("email")
def create_superuser(email):
    """Sets an existing user as a superuser (admin)."""
    # The app context is automatically available for Flask CLI commands
    user = User.query.filter_by(email=email).first()
    if not user:
        print(f"Error: User with email '{email}' not found.")
        return
    
    if user.is_admin:
        print(f"User '{user.username}' is already an admin.")
        return
        
    user.is_admin = True
    db.session.commit()
    print(f"Success! User '{user.username}' ({email}) has been promoted to admin.")


# Run the app
if __name__ == '__main__':
    app.run(debug=True)