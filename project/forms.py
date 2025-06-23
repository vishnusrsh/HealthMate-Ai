from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, TimeField, DateField, TextAreaField, SelectField
from wtforms.widgets import TimeInput, DateInput
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError
from .models import User

class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=150)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Sign Up')

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('That username is already taken.')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('That email is already registered.')

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class DonorRegistrationForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired(), Length(max=150)])
    blood_group = StringField('Blood Group (e.g., A+, O-)', validators=[DataRequired(), Length(max=10)])
    contact_number = StringField('Contact Number', validators=[DataRequired(), Length(max=15)])
    email = StringField('Email (Optional)', validators=[Email(), Length(max=150)])
    submit = SubmitField('Register as Donor')

class PillReminderForm(FlaskForm):
    medicine_name = StringField('Medicine Name', validators=[DataRequired(), Length(max=100)])
    dosage = StringField('Dosage (e.g., 1 tablet, 10ml)', validators=[Length(max=50)])
    reminder_time = TimeField('Reminder Time', format='%H:%M', validators=[DataRequired()], widget=TimeInput())
    frequency_choices = [
        ('daily', 'Daily'),
        ('every_6_hours', 'Every 6 Hours'),
        ('every_12_hours', 'Every 12 Hours'),
        ('before_meal', 'Before Meal'),
        ('after_meal', 'After Meal'),
        ('specific_days', 'Specific Days (Mon, Wed, Fri)'), # This would require more complex logic for actual scheduling
        ('custom', 'Custom (add details in notes)')
    ]
    frequency = SelectField('Frequency', choices=frequency_choices, validators=[DataRequired()])
    start_date = DateField('Start Date', format='%Y-%m-%d', validators=[DataRequired()], widget=DateInput())
    end_date = DateField('End Date (Optional)', format='%Y-%m-%d', validators=[], widget=DateInput(), default=None)
    notes = TextAreaField('Notes (Optional)', validators=[Length(max=200)])
    submit = SubmitField('Set Reminder')