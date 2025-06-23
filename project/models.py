from . import db
from flask_login import UserMixin

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    age = db.Column(db.Integer, nullable=True)
    activities = db.relationship('UserActivity', backref='user', lazy=True)

class BloodDonor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    blood_group = db.Column(db.String(10), nullable=False)
    contact_number = db.Column(db.String(15), nullable=False)
    email = db.Column(db.String(150), nullable=True)
    registration_date = db.Column(db.DateTime, nullable=False, default=db.func.current_timestamp())
    is_active = db.Column(db.Boolean, default=True, nullable=False)

class PillReminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    medicine_name = db.Column(db.String(100), nullable=False)
    dosage = db.Column(db.String(50), nullable=True)
    reminder_time = db.Column(db.Time, nullable=False)
    frequency = db.Column(db.String(50), nullable=False) # e.g., 'daily', 'every_6_hours', 'specific_days'
    start_date = db.Column(db.Date, nullable=False, default=db.func.current_date())
    end_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    user = db.relationship('User', backref=db.backref('reminders', lazy=True))

    def get_frequency_display(self):
        frequency_map = {
            'daily': 'Daily',
            'every_6_hours': 'Every 6 Hours',
            'every_12_hours': 'Every 12 Hours',
            'every_24_hours': 'Every 24 Hours',
            'specific_days': 'Specific Days' # This might need more complex handling if days are stored elsewhere
        }
        return frequency_map.get(self.frequency, self.frequency.replace('_', ' ').title())

class HealthChallenge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    challenge_type = db.Column(db.String(50), nullable=False)  # 'hydration', 'exercise', 'meditation'
    duration_days = db.Column(db.Integer, default=30, nullable=False)
    target_value = db.Column(db.String(50), nullable=False)  # e.g., '8 glasses', '30 minutes', '10 minutes'
    icon = db.Column(db.String(50), nullable=False)  # Bootstrap icon class
    color = db.Column(db.String(20), nullable=False)  # Bootstrap color class
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

class UserChallenge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    challenge_id = db.Column(db.Integer, db.ForeignKey('health_challenge.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False, default=db.func.current_date())
    end_date = db.Column(db.Date, nullable=False)
    current_streak = db.Column(db.Integer, default=0, nullable=False)
    longest_streak = db.Column(db.Integer, default=0, nullable=False)
    total_completed_days = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    user = db.relationship('User', backref=db.backref('user_challenges', lazy=True))
    challenge = db.relationship('HealthChallenge', backref=db.backref('participants', lazy=True))

class ChallengeProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_challenge_id = db.Column(db.Integer, db.ForeignKey('user_challenge.id'), nullable=False)
    date = db.Column(db.Date, nullable=False, default=db.func.current_date())
    completed = db.Column(db.Boolean, default=False, nullable=False)
    value = db.Column(db.String(50), nullable=True)  # e.g., '8 glasses', '35 minutes'
    notes = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    user_challenge = db.relationship('UserChallenge', backref=db.backref('progress_entries', lazy=True))

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    conversation_id = db.Column(db.String(36), nullable=False)
    message = db.Column(db.Text, nullable=False)
    response = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

    user = db.relationship('User', backref=db.backref('chat_messages', lazy=True))

    __table_args__ = (db.Index('ix_user_conversation', 'user_id', 'conversation_id'),)

class UserActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    activity = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    type = db.Column(db.String(20), nullable=False)  # 'Bug' or 'Suggestion'
    message = db.Column(db.Text, nullable=False)
    submitted_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    user = db.relationship('User', backref=db.backref('feedbacks', lazy=True))