from flask import Flask
import os
from project import create_app, db
from project.models import User, BloodDonor, PillReminder
from project.routes import initialize_challenges

app = create_app()

# Initialize challenges when app starts
with app.app_context():
    db.create_all()
    initialize_challenges()

if __name__ == '__main__':
    app.run(debug=True) 