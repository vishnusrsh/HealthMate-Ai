from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import login_user, login_required, logout_user, current_user
from .models import User, BloodDonor, PillReminder, HealthChallenge, UserChallenge, ChallengeProgress, ChatMessage, Feedback
from .forms import RegistrationForm, LoginForm, DonorRegistrationForm, PillReminderForm
from . import db, login_manager
from datetime import datetime, timedelta
import random
from functools import wraps # For admin_required decorator
import google.generativeai as genai
from PIL import Image
import io
import base64
from sqlalchemy import extract, func
import uuid

bp = Blueprint('routes', __name__)


@bp.route('/')
def home():
    return redirect(url_for('routes.login'))

@bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('routes.dashboard'))
    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_pw = generate_password_hash(form.password.data)
        user = User(username=form.username.data, email=form.email.data, password=hashed_pw)
        db.session.add(user)
        db.session.commit()
        flash('Account created! You can now log in.', 'success')
        return redirect(url_for('routes.login'))
    return render_template('register.html', form=form)

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('routes.dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and check_password_hash(user.password, form.password.data):
            login_user(user)
            return redirect(url_for('routes.dashboard'))
        else:
            flash('Login unsuccessful. Please check email and password.', 'danger')
    return render_template('login.html', form=form)

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('routes.login'))

@bp.route('/dashboard')
@login_required
def dashboard():
    # Get user's active challenges for dashboard
    active_challenges = UserChallenge.query.filter_by(
        user_id=current_user.id, 
        is_active=True
    ).filter(UserChallenge.end_date >= datetime.now().date()).all()
    
    # Calculate stats
    today_str = datetime.now().strftime('%Y-%m-%d')
    bmi = getattr(current_user, 'bmi', None)

    water_count = 0
    steps = 0
    if hasattr(current_user, 'activities'):
        for act in current_user.activities:
            if act.timestamp.strftime('%Y-%m-%d') == today_str:
                if act.activity == 'Drank water':
                    water_count += 1
                elif act.activity.startswith('Steps:'):
                    try:
                        steps += int(act.activity.split(':')[1].strip())
                    except Exception:
                        pass

    challenge_progress = sum([uc.total_completed_days for uc in active_challenges]) if active_challenges else 0

    return render_template(
        'dashboard.html',
        user=current_user,
        active_challenges=active_challenges,
        bmi=bmi,
        water_count=water_count,
        steps=steps,
        challenge_progress=challenge_progress
    )

# This is needed to load the user
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Decorator for admin-only routes
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or (not current_user.is_admin and current_user.email != 'vishnu@gmail.com'):
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('routes.dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# --- ADD THIS ENTIRE NEW CHATBOT ROUTE AND PROMPT ---

SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]

SYSTEM_PROMPT = """
You are HealthMate AI, a friendly and helpful AI assistant.
Your goal is to provide general health information and support.
You are NOT a medical professional.
ALWAYS include the following disclaimer at the end of every response, exactly as written:
'Disclaimer: I am an AI assistant and not a medical professional. This information is for educational purposes only. Please consult with a qualified healthcare provider for any medical advice or diagnosis.'
Do not answer questions that are not related to health, wellness, or medicine. If asked a non-health question, politely decline and state that you can only answer health-related queries.
"""

@bp.route('/chatbot', defaults={'conversation_id': None}, methods=['GET', 'POST'])
@bp.route('/chatbot/<conversation_id>', methods=['GET', 'POST'])
@login_required
def chatbot(conversation_id):
    # If no conversation ID is provided, start a new chat by creating one
    if conversation_id is None:
        conversation_id = uuid.uuid4().hex
        return redirect(url_for('routes.chatbot', conversation_id=conversation_id))

    # Fetch the history for the current conversation
    chat_history = ChatMessage.query.filter_by(
        user_id=current_user.id,
        conversation_id=conversation_id
    ).order_by(ChatMessage.timestamp).all()

    # Fetch all past conversations for the sidebar history
    all_conversations = db.session.query(
        ChatMessage.conversation_id,
        func.max(ChatMessage.message).label('last_message')
    ).filter_by(user_id=current_user.id).group_by(ChatMessage.conversation_id).order_by(
        func.max(ChatMessage.timestamp).desc()
    ).all()

    ai_greeting = None
    if not chat_history:
        ai_greeting = "Hello! I'm your AI health assistant. How can I help you today?"

    if request.method == 'POST':
        user_message = request.form.get('message')
        if not user_message:
            return redirect(url_for('routes.chatbot', conversation_id=conversation_id))
            
        ai_response = get_ai_response(user_message)
        
        chat_msg = ChatMessage(
            user_id=current_user.id,
            conversation_id=conversation_id,
            message=user_message,
            response=ai_response
        )
        db.session.add(chat_msg)
        db.session.commit()
        return redirect(url_for('routes.chatbot', conversation_id=conversation_id))

    return render_template(
        'chatbot.html',
        chat_history=chat_history,
        ai_greeting=ai_greeting,
        conversation_id=conversation_id,
        all_conversations=all_conversations
    )

@bp.route('/blood_donor_registration', methods=['GET', 'POST'])
@login_required
def blood_donor_registration():
    form = DonorRegistrationForm()
    if form.validate_on_submit():
        try:
            donor = BloodDonor(
                name=form.name.data,
                blood_group=form.blood_group.data,
                contact_number=form.contact_number.data,
                email=form.email.data if form.email.data else None
            )
            db.session.add(donor)
            db.session.commit()
            flash('Thank you for registering as a blood donor!', 'success')
            return redirect(url_for('routes.active_blood_donors'))
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred: {str(e)}', 'danger')
            current_app.logger.error(f"Error in blood donor registration: {e}")
    return render_template('blood_donor_registration.html', title='Blood Donor Registration', form=form)

@bp.route('/active_blood_donors')
@login_required
def active_blood_donors():
    try:
        donors = BloodDonor.query.filter_by(is_active=True).order_by(BloodDonor.registration_date.desc()).all()
    except Exception as e:
        donors = []
        flash('Could not retrieve donor list at this time.', 'warning')
        current_app.logger.error(f"Error fetching active blood donors: {e}")
    return render_template('active_blood_donors.html', title='Active Blood Donors', donors=donors)



@bp.route('/bmi-calculator', methods=['GET', 'POST'])
@login_required
def bmi_calculator():
    bmi = None
    category = None
    category_class = None
    age_disclaimer = None
    ai_recommendations = None  # Initialize AI recommendations variable

    if request.method == 'POST':
        # Retrieve all form data
        units = request.form.get('units')
        weight_str = request.form.get('weight')
        height_str = request.form.get('height')
        age_str = request.form.get('age')
        gender = request.form.get('gender')

        try:
            # Convert string inputs to numeric types
            weight = float(weight_str)
            height = float(height_str)
            age = int(age_str)

            # --- Input Validation ---
            if weight <= 0 or height <= 0 or age <= 0:
                flash('Weight, height, and age must be positive values.', 'danger')
                return redirect(url_for('routes.bmi_calculator'))
            
            if age < 2:
                flash('BMI calculation is not suitable for children under 2.', 'warning')
                return redirect(url_for('routes.bmi_calculator'))
            
            # --- BMI Calculation ---
            if units == 'metric':
                # Formula: kg / (m^2)
                # Convert height from cm to meters before calculating
                height_m = height / 100
                bmi = weight / (height_m ** 2)
            elif units == 'imperial':
                # Formula: (lbs / (in^2)) * 703
                bmi = (weight / (height ** 2)) * 703
            
            # --- Result Categorization ---
            # These categories apply to adults (age 20+)
            if bmi < 18.5:
                category = "Underweight"
                category_class = "alert-warning"
            elif 18.5 <= bmi < 25:
                category = "Normal weight"
                category_class = "alert-success"
            elif 25 <= bmi < 30:
                category = "Overweight"
                category_class = "alert-warning"
            else: # bmi >= 30
                category = "Obesity"
                category_class = "alert-danger"

            # --- Context-Aware Disclaimer ---
            # Add a specific, crucial disclaimer for users under 20
            if age < 20:
                age_disclaimer = "For individuals under 20, BMI is interpreted using age- and gender-specific percentile charts, not the standard adult categories shown above. Please consult a healthcare provider for an accurate assessment."

            # --- AI-Powered Recommendations ---
            try:
                ai_recommendations = get_bmi_recommendations(bmi, category, age, gender, weight, height, units)
            except Exception as e:
                current_app.logger.error(f"Error getting AI recommendations: {e}")
                ai_recommendations = None

        except (ValueError, TypeError):
            # This handles cases where input is not a valid number (e.g., text)
            flash('Please enter valid numbers for weight, height, and age.', 'danger')
        except ZeroDivisionError:
            # This handles the unlikely case of height being 0
            flash('Height cannot be zero.', 'danger')

    # Render the page, passing all result variables to the template.
    # On a GET request, these variables will be None.
    return render_template('bmi_calculator.html', 
                         bmi=bmi, 
                         category=category, 
                         category_class=category_class, 
                         age_disclaimer=age_disclaimer,
                         ai_recommendations=ai_recommendations)

def get_bmi_recommendations(bmi, category, age, gender, weight, height, units):
    """Get AI-powered health recommendations based on BMI results"""
    try:
        api_key = current_app.config.get('GOOGLE_API_KEY')
        if not api_key:
            return None
        
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Create comprehensive prompt for BMI recommendations
        prompt = f"""
You are a health and wellness expert providing personalized recommendations based on BMI analysis.

Patient Information:
- BMI: {bmi:.1f}
- BMI Category: {category}
- Age: {age} years
- Gender: {gender}
- Weight: {weight} {'kg' if units == 'metric' else 'lbs'}
- Height: {height} {'cm' if units == 'metric' else 'inches'}

Please provide personalized health recommendations in the following format:

**DIET RECOMMENDATIONS:**
- 3-4 specific dietary suggestions tailored to their BMI category and age
- Include portion control, food choices, and meal timing advice

**EXERCISE RECOMMENDATIONS:**
- 3-4 exercise recommendations appropriate for their fitness level
- Include frequency, duration, and intensity suggestions
- Consider age-appropriate activities

**LIFESTYLE RECOMMENDATIONS:**
- 2-3 lifestyle changes that can support their health goals
- Include sleep, stress management, and daily habits

**HEALTH MONITORING:**
- 2-3 suggestions for tracking progress
- Recommended follow-up frequency

**PRECAUTIONS:**
- Any specific warnings or considerations for their age/category

Format your response with clear sections and bullet points. Keep recommendations practical and actionable.
Always emphasize consulting healthcare professionals for personalized medical advice.

Remember: This is for educational purposes only. Always recommend consulting healthcare professionals for medical advice.
"""
        
        response = model.generate_content(prompt, safety_settings=SAFETY_SETTINGS)
        return response.text
        
    except Exception as e:
        current_app.logger.error(f"Error in get_bmi_recommendations: {e}")
        return None

# --- Pill Reminder Routes ---
@bp.route('/pill_reminders')
@login_required
def pill_reminders():
    reminders = PillReminder.query.filter_by(user_id=current_user.id, is_active=True).order_by(PillReminder.reminder_time).all()
    return render_template('pill_reminders.html', title='Your Pill Reminders', reminders=reminders)

@bp.route('/pill_reminder/add', methods=['GET', 'POST'])
@login_required
def add_pill_reminder():
    form = PillReminderForm()
    if form.validate_on_submit():
        try:
            reminder = PillReminder(
                user_id=current_user.id,
                medicine_name=form.medicine_name.data,
                dosage=form.dosage.data,
                reminder_time=form.reminder_time.data,
                frequency=form.frequency.data,
                start_date=form.start_date.data,
                end_date=form.end_date.data if form.end_date.data else None,
                notes=form.notes.data
            )
            db.session.add(reminder)
            db.session.commit()
            flash('Pill reminder added successfully!', 'success')
            return redirect(url_for('routes.pill_reminders'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding reminder: {str(e)}', 'danger')
            current_app.logger.error(f"Error adding pill reminder: {e}")
    return render_template('add_edit_pill_reminder.html', title='Add New Pill Reminder', form=form, action='Add')

@bp.route('/pill_reminder/edit/<int:reminder_id>', methods=['GET', 'POST'])
@login_required
def edit_pill_reminder(reminder_id):
    reminder = PillReminder.query.get_or_404(reminder_id)
    if reminder.user_id != current_user.id:
        flash('You are not authorized to edit this reminder.', 'danger')
        return redirect(url_for('routes.pill_reminders'))

    form = PillReminderForm(obj=reminder)
    if form.validate_on_submit():
        try:
            reminder.medicine_name = form.medicine_name.data
            reminder.dosage = form.dosage.data
            reminder.reminder_time = form.reminder_time.data
            reminder.frequency = form.frequency.data
            reminder.start_date = form.start_date.data
            reminder.end_date = form.end_date.data if form.end_date.data else None
            reminder.notes = form.notes.data
            db.session.commit()
            flash('Pill reminder updated successfully!', 'success')
            return redirect(url_for('routes.pill_reminders'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating reminder: {str(e)}', 'danger')
            current_app.logger.error(f"Error updating pill reminder: {e}")
    return render_template('add_edit_pill_reminder.html', title='Edit Pill Reminder', form=form, action='Edit')

@bp.route('/pill_reminder/delete/<int:reminder_id>', methods=['POST'])
@login_required
def delete_pill_reminder(reminder_id):
    reminder = PillReminder.query.get_or_404(reminder_id)
    if reminder.user_id != current_user.id:
        flash('You are not authorized to delete this reminder.', 'danger')
        return redirect(url_for('routes.pill_reminders'))
    try:
        # Instead of deleting, we can mark as inactive
        # db.session.delete(reminder)
        reminder.is_active = False
        db.session.commit()
        flash('Pill reminder deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting reminder: {str(e)}', 'danger')
        current_app.logger.error(f"Error deleting pill reminder: {e}")
    return redirect(url_for('routes.pill_reminders'))

@bp.route('/pill_reminder/toggle_active/<int:reminder_id>', methods=['POST'])
@login_required
def toggle_pill_reminder_active(reminder_id):
    reminder = PillReminder.query.get_or_404(reminder_id)
    if reminder.user_id != current_user.id:
        flash('You are not authorized to modify this reminder.', 'danger')
        return redirect(url_for('routes.pill_reminders'))
    try:
        reminder.is_active = not reminder.is_active
        db.session.commit()
        status = "activated" if reminder.is_active else "deactivated"
        flash(f'Pill reminder {status} successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error toggling reminder status: {str(e)}', 'danger')
        current_app.logger.error(f"Error toggling pill reminder active status: {e}")
    return redirect(url_for('routes.pill_reminders'))

    # On a successful POST, they will be populated.
    return render_template('bmi_calculator.html', bmi=bmi, category=category, category_class=category_class, age_disclaimer=age_disclaimer)

# --- Health Tips Route ---
@bp.route('/health_tips')
@login_required
def health_tips():
    tips = [
        {"text": "Drink at least 8 glasses of water a day to stay hydrated.", "author": "Hydration Experts"},
        {"text": "Aim for 7-9 hours of quality sleep each night for optimal health.", "author": "Sleep Foundation"},
        {"text": "Incorporate at least 30 minutes of moderate exercise into your daily routine.", "author": "WHO"},
        {"text": "Eat a balanced diet rich in fruits, vegetables, and whole grains.", "author": "Dietitians Association"},
        {"text": "Practice mindfulness or meditation for 10-15 minutes daily to reduce stress.", "author": "Mindfulness Institute"},
        {"text": "Take short breaks to stretch and move if you sit for long periods.", "author": "Ergonomics Today"},
        {"text": "Wash your hands frequently with soap and water for at least 20 seconds.", "author": "CDC"},
        {"text": "Limit processed foods, sugary drinks, and excessive saturated fats.", "author": "Heart Health Org"},
        {"text": "Schedule regular check-ups with your doctor and dentist.", "author": "General Practitioners Guide"},
        {"text": "Connect with loved ones regularly to maintain strong social bonds.", "author": "Social Wellness Network"}
    ]
    selected_tip = random.choice(tips)
    return render_template('health_tips.html', tip=selected_tip)

@bp.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    return render_template('admin_dashboard.html')

@bp.route('/admin/manage_users', methods=['GET'])
@login_required
@admin_required
def manage_users():
    search = request.args.get('search', '').strip()
    query = User.query
    if search:
        query = query.filter((User.username.ilike(f'%{search}%')) | (User.email.ilike(f'%{search}%')))
    users = query.order_by(User.id).all()
    # Add recent_activities property to each user
    for user in users:
        user.recent_activities = sorted(user.activities, key=lambda a: a.timestamp, reverse=True)[:5]
    return render_template('manage_users.html', users=users)

@bp.route('/admin/edit_user/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if not username or not email:
        msg = 'Username and email are required.'
        if is_ajax:
            return {'success': False, 'message': msg}, 400
        flash(msg, 'danger')
        return redirect(url_for('routes.manage_users'))
    # Check for unique username/email
    if User.query.filter(User.username == username, User.id != user_id).first():
        msg = 'Username already taken.'
        if is_ajax:
            return {'success': False, 'message': msg}, 400
        flash(msg, 'danger')
        return redirect(url_for('routes.manage_users'))
    if User.query.filter(User.email == email, User.id != user_id).first():
        msg = 'Email already registered.'
        if is_ajax:
            return {'success': False, 'message': msg}, 400
        flash(msg, 'danger')
        return redirect(url_for('routes.manage_users'))
    user.username = username
    user.email = email
    db.session.commit()
    msg = 'User details updated.'
    if is_ajax:
        return {'success': True, 'message': msg, 'username': username, 'email': email}
    flash(msg, 'success')
    return redirect(url_for('routes.manage_users'))

@bp.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user == current_user:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('routes.manage_users'))
    db.session.delete(user)
    db.session.commit()
    flash('User deleted.', 'success')
    return redirect(url_for('routes.manage_users'))

@bp.route('/admin/toggle_user_active/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def toggle_user_active(user_id):
    user = User.query.get_or_404(user_id)
    if user == current_user:
        flash('You cannot deactivate your own account.', 'danger')
        return redirect(url_for('routes.manage_users'))
    user.is_active = not user.is_active
    db.session.commit()
    status = 'activated' if user.is_active else 'deactivated'
    flash(f'User {status}.', 'success')
    return redirect(url_for('routes.manage_users'))

@bp.route('/admin/toggle_admin/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def toggle_admin(user_id):
    user = User.query.get_or_404(user_id)
    if user == current_user:
        flash('You cannot change your own admin status.', 'danger')
        return redirect(url_for('routes.manage_users'))
    user.is_admin = not user.is_admin
    db.session.commit()
    status = 'promoted to admin' if user.is_admin else 'demoted from admin'
    flash(f'User {user.username} {status}.', 'success')
    return redirect(url_for('routes.manage_users'))

@bp.route('/admin/create_user', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        phone = request.form.get('phone', '').strip()
        age = request.form.get('age', '').strip()
        is_admin = request.form.get('is_admin') == 'on'
        is_active = request.form.get('is_active') == 'on'
        
        # Validation
        if not username or not email or not password:
            flash('Username, email, and password are required.', 'danger')
            return redirect(url_for('routes.create_user'))
        
        # Check for unique username/email
        if User.query.filter_by(username=username).first():
            flash('Username already taken.', 'danger')
            return redirect(url_for('routes.create_user'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('routes.create_user'))
        
        # Create new user
        try:
            new_user = User(
                username=username,
                email=email,
                password=generate_password_hash(password),
                phone=phone if phone else None,
                age=int(age) if age and age.isdigit() else None,
                is_admin=is_admin,
                is_active=is_active
            )
            db.session.add(new_user)
            db.session.commit()
            flash(f'User {username} created successfully.', 'success')
            return redirect(url_for('routes.manage_users'))
        except Exception as e:
            db.session.rollback()
            flash('Error creating user. Please try again.', 'danger')
            return redirect(url_for('routes.create_user'))
    
    return render_template('create_user.html')

@bp.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        age = request.form.get('age', '').strip()
        current_password = request.form.get('current_password', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        
        # Validation
        if not username or not email:
            flash('Username and email are required.', 'danger')
            return redirect(url_for('routes.edit_profile'))
        
        # Check for unique username/email
        if User.query.filter(User.username == username, User.id != current_user.id).first():
            flash('Username already taken.', 'danger')
            return redirect(url_for('routes.edit_profile'))
        
        if User.query.filter(User.email == email, User.id != current_user.id).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('routes.edit_profile'))
        
        # Update basic info
        current_user.username = username
        current_user.email = email
        current_user.phone = phone if phone else None
        current_user.age = int(age) if age and age.isdigit() else None
        
        # Handle password change
        if current_password and new_password:
            if not check_password_hash(current_user.password, current_password):
                flash('Current password is incorrect.', 'danger')
                return redirect(url_for('routes.edit_profile'))
            
            if new_password != confirm_password:
                flash('New passwords do not match.', 'danger')
                return redirect(url_for('routes.edit_profile'))
            
            if len(new_password) < 6:
                flash('New password must be at least 6 characters long.', 'danger')
                return redirect(url_for('routes.edit_profile'))
            
            current_user.password = generate_password_hash(new_password)
        
        try:
            db.session.commit()
            flash('Profile updated successfully.', 'success')
            return redirect(url_for('routes.dashboard'))
        except Exception as e:
            db.session.rollback()
            flash('Error updating profile. Please try again.', 'danger')
            return redirect(url_for('routes.edit_profile'))
    
    return render_template('edit_profile.html')

@bp.route('/body-map')
@login_required
def body_map():
    return render_template('body_map.html')

@bp.route('/body-map-analyze', methods=['POST'])
@login_required
def body_map_analyze():
    try:
        data = request.get_json()
        body_part = data.get('bodyPart')
        symptoms = data.get('symptoms', [])
        
        if not symptoms:
            return jsonify({'success': False, 'error': 'No symptoms provided'})
        
        # Create a comprehensive prompt for the AI
        symptoms_text = ', '.join(symptoms)
        body_part_names = {
            'head': 'head',
            'neck': 'neck',
            'chest': 'chest',
            'leftArm': 'left arm',
            'rightArm': 'right arm',
            'abdomen': 'abdomen',
            'pelvis': 'pelvis',
            'leftLeg': 'left leg',
            'rightLeg': 'right leg',
            'back': 'back'
        }
        
        body_part_name = body_part_names.get(body_part, body_part)
        
        enhanced_prompt = f"""
You are analyzing symptoms for a patient experiencing issues in their {body_part_name} area.

Patient's symptoms: {symptoms_text}

Please provide a comprehensive analysis including:
1. A brief summary of the symptoms and their potential significance
2. 2-4 possible conditions that could be associated with these symptoms (with brief descriptions)
3. 3-5 recommended actions the patient should take

Format your response as a structured analysis with clear sections for summary, potential conditions, and recommendations.

Remember: This is for educational purposes only. Always emphasize consulting healthcare professionals for proper diagnosis.
"""
        
        # Use the existing AI integration
        api_key = current_app.config.get('GOOGLE_API_KEY')
        if not api_key:
            return jsonify({'success': False, 'error': 'AI service not configured'})
        
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        response = model.generate_content(enhanced_prompt, safety_settings=SAFETY_SETTINGS)
        ai_response = response.text
        
        # Parse the AI response into structured format
        analysis = parse_ai_response(ai_response, body_part_name, symptoms_text)
        
        return jsonify({
            'success': True,
            'analysis': analysis
        })
        
    except Exception as e:
        print(f"Error in body map analysis: {e}")
        return jsonify({'success': False, 'error': 'Failed to analyze symptoms'})

def parse_ai_response(response_text, body_part, symptoms):
    """Parse AI response into structured format"""
    try:
        # Simple parsing - you can enhance this based on AI response format
        lines = response_text.split('\n')
        
        summary = ""
        conditions = []
        recommendations = []
        
        current_section = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            if 'summary' in line.lower() or 'symptoms' in line.lower():
                current_section = 'summary'
                continue
            elif 'condition' in line.lower() or 'possible' in line.lower():
                current_section = 'conditions'
                continue
            elif 'recommend' in line.lower() or 'action' in line.lower():
                current_section = 'recommendations'
                continue
            elif line.startswith('-') or line.startswith('•') or line.startswith('*'):
                line = line[1:].strip()
                if current_section == 'conditions':
                    conditions.append({'name': line.split(':')[0] if ':' in line else line, 
                                     'description': line.split(':')[1] if ':' in line else 'Associated with the reported symptoms'})
                elif current_section == 'recommendations':
                    recommendations.append(line)
            elif current_section == 'summary':
                summary += line + " "
        
        # If parsing fails, provide a fallback
        if not summary:
            summary = f"Analysis of {symptoms} in the {body_part} area. Please consult a healthcare professional for proper diagnosis."
        
        if not conditions:
            conditions = [{'name': 'General Assessment', 'description': 'Symptoms require professional medical evaluation'}]
        
        if not recommendations:
            recommendations = [
                'Schedule an appointment with your healthcare provider',
                'Monitor symptoms and note any changes',
                'Avoid self-diagnosis and self-treatment'
            ]
        
        return {
            'summary': summary.strip(),
            'conditions': conditions,
            'recommendations': recommendations
        }
        
    except Exception as e:
        print(f"Error parsing AI response: {e}")
        return {
            'summary': f"Analysis of symptoms in the {body_part} area. Please consult a healthcare professional.",
            'conditions': [{'name': 'Medical Consultation Required', 'description': 'Professional evaluation needed'}],
            'recommendations': ['Consult with a healthcare provider', 'Monitor symptoms', 'Seek immediate care if symptoms worsen']
        }

# --- Health Challenges Routes ---
@bp.route('/health-challenges')
@login_required
def health_challenges():
    # Get available challenges
    available_challenges = HealthChallenge.query.filter_by(is_active=True).all()
    
    # Get user's active challenges
    active_challenges = UserChallenge.query.filter_by(
        user_id=current_user.id, 
        is_active=True
    ).filter(UserChallenge.end_date >= datetime.now().date()).all()
    
    # Get user's completed challenges
    completed_challenges = UserChallenge.query.filter_by(
        user_id=current_user.id, 
        is_active=False
    ).filter(UserChallenge.end_date < datetime.now().date()).all()
    
    # Add additional data for active challenges
    for user_challenge in active_challenges:
        # Calculate days remaining
        days_remaining = (user_challenge.end_date - datetime.now().date()).days
        user_challenge.days_remaining = max(0, days_remaining)
        
        # Check today's progress
        today_progress = ChallengeProgress.query.filter_by(
            user_challenge_id=user_challenge.id,
            date=datetime.now().date()
        ).first()
        user_challenge.today_progress = today_progress
    
    return render_template('health_challenges.html', 
                         available_challenges=available_challenges,
                         active_challenges=active_challenges,
                         completed_challenges=completed_challenges)

@bp.route('/join-challenge/<int:challenge_id>', methods=['POST'])
@login_required
def join_challenge(challenge_id):
    challenge = HealthChallenge.query.get_or_404(challenge_id)
    
    # Check if user is already participating
    existing = UserChallenge.query.filter_by(
        user_id=current_user.id,
        challenge_id=challenge_id,
        is_active=True
    ).first()
    
    if existing:
        flash('You are already participating in this challenge!', 'warning')
        return redirect(url_for('routes.health_challenges'))
    
    # Create new user challenge
    end_date = datetime.now().date() + timedelta(days=challenge.duration_days)
    user_challenge = UserChallenge(
        user_id=current_user.id,
        challenge_id=challenge_id,
        end_date=end_date
    )
    
    db.session.add(user_challenge)
    db.session.commit()
    
    flash(f'Successfully joined the {challenge.name} challenge!', 'success')
    return redirect(url_for('routes.health_challenges'))

@bp.route('/mark-challenge-complete/<int:user_challenge_id>', methods=['POST'])
@login_required
def mark_challenge_complete(user_challenge_id):
    user_challenge = UserChallenge.query.get_or_404(user_challenge_id)
    
    if user_challenge.user_id != current_user.id:
        flash('Unauthorized', 'danger')
        return redirect(url_for('routes.health_challenges'))
    
    value = request.form.get('value', '')
    today = datetime.now().date()
    
    # Check if already completed today
    existing_progress = ChallengeProgress.query.filter_by(
        user_challenge_id=user_challenge_id,
        date=today
    ).first()
    
    if existing_progress:
        flash('You have already completed today\'s challenge!', 'warning')
        return redirect(url_for('routes.health_challenges'))
    
    # Create progress entry
    progress = ChallengeProgress(
        user_challenge_id=user_challenge_id,
        date=today,
        completed=True,
        value=value
    )
    
    db.session.add(progress)
    
    # Update user challenge stats
    user_challenge.total_completed_days += 1
    user_challenge.current_streak += 1
    
    if user_challenge.current_streak > user_challenge.longest_streak:
        user_challenge.longest_streak = user_challenge.current_streak
    
    # Check if challenge is completed
    if user_challenge.total_completed_days >= user_challenge.challenge.duration_days:
        user_challenge.is_active = False
        flash(f'Congratulations! You have completed the {user_challenge.challenge.name} challenge!', 'success')
    else:
        flash('Great job! Today\'s challenge completed.', 'success')
    
    db.session.commit()
    return redirect(url_for('routes.health_challenges'))

@bp.route('/quit-challenge/<int:user_challenge_id>', methods=['POST'])
@login_required
def quit_challenge(user_challenge_id):
    user_challenge = UserChallenge.query.get_or_404(user_challenge_id)
    
    if user_challenge.user_id != current_user.id:
        flash('Unauthorized', 'danger')
        return redirect(url_for('routes.health_challenges'))
    
    user_challenge.is_active = False
    db.session.commit()
    
    flash(f'You have quit the {user_challenge.challenge.name} challenge.', 'info')
    return redirect(url_for('routes.health_challenges'))

@bp.route('/challenge-details/<int:user_challenge_id>')
@login_required
def challenge_details(user_challenge_id):
    user_challenge = UserChallenge.query.get_or_404(user_challenge_id)
    
    if user_challenge.user_id != current_user.id:
        flash('Unauthorized', 'danger')
        return redirect(url_for('routes.health_challenges'))
    
    # Get all progress entries
    progress_entries = ChallengeProgress.query.filter_by(
        user_challenge_id=user_challenge_id
    ).order_by(ChallengeProgress.date).all()
    
    # Calculate statistics
    total_days = (user_challenge.end_date - user_challenge.start_date).days + 1
    completion_rate = (user_challenge.total_completed_days / total_days) * 100
    
    # Calculate days remaining
    days_remaining = 0
    if user_challenge.is_active:
        days_remaining = max(0, (user_challenge.end_date - datetime.now().date()).days)
    
    # Create calendar data for template
    calendar_data = []
    start_date = user_challenge.start_date
    end_date = user_challenge.end_date
    
    current_date = start_date
    week_data = []
    
    while current_date <= end_date:
        # Find progress for this date
        progress = next((p for p in progress_entries if p.date == current_date), None)
        
        day_info = {
            'date': current_date,
            'day_number': current_date.day,
            'completed': progress is not None and progress.completed,
            'value': progress.value if progress else None,
            'is_today': current_date == datetime.now().date(),
            'is_future': current_date > datetime.now().date(),
            'is_past': current_date < datetime.now().date()
        }
        
        week_data.append(day_info)
        
        # Start new week if we have 7 days or it's the end
        if len(week_data) == 7 or current_date == end_date:
            calendar_data.append(week_data)
            week_data = []
        
        current_date += timedelta(days=1)
    
    return render_template('challenge_details.html',
                         user_challenge=user_challenge,
                         progress_entries=progress_entries,
                         calendar_data=calendar_data,
                         total_days=total_days,
                         completion_rate=completion_rate,
                         days_remaining=days_remaining)

# Initialize default challenges
def initialize_challenges():
    """Create default challenges if they don't exist"""
    challenges_data = [
        {
            'name': 'Hydration Hero',
            'description': 'Drink 8 glasses of water daily for 30 days to improve hydration and overall health.',
            'challenge_type': 'hydration',
            'target_value': '8 glasses',
            'icon': 'droplet-fill',
            'color': 'info'
        },
        {
            'name': 'Fitness Warrior',
            'description': 'Exercise for 30 minutes daily to build strength, endurance, and healthy habits.',
            'challenge_type': 'exercise',
            'target_value': '30 minutes',
            'icon': 'lightning-fill',
            'color': 'warning'
        },
        {
            'name': 'Mindful Moments',
            'description': 'Practice meditation for 10 minutes daily to reduce stress and improve mental clarity.',
            'challenge_type': 'meditation',
            'target_value': '10 minutes',
            'icon': 'peace-fill',
            'color': 'success'
        }
    ]
    
    for challenge_data in challenges_data:
        existing = HealthChallenge.query.filter_by(name=challenge_data['name']).first()
        if not existing:
            challenge = HealthChallenge(**challenge_data)
            db.session.add(challenge)
    
    db.session.commit()

# --- (The load_user function remains at the end, unchanged) ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@bp.route('/prescription-analyzer', methods=['GET', 'POST'])
@login_required
def prescription_analyzer():
    medicines = []
    error = None
    if request.method == 'POST':
        file = request.files.get('image')
        med_name = request.form.get('medicine_name', '').strip()
        genai.configure(api_key=current_app.config.get('GOOGLE_API_KEY'))
        model = genai.GenerativeModel('gemini-1.5-flash')
        # 1. If image is uploaded, analyze it
        if file and file.filename:
            try:
                image_bytes = file.read()
                image = Image.open(io.BytesIO(image_bytes))
                buffered = io.BytesIO()
                image.save(buffered, format="PNG")
                img_str = base64.b64encode(buffered.getvalue()).decode()
                prompt = (
                    "This is a photo of a medical prescription. "
                    "Extract a list of all medicine names mentioned in the prescription. "
                    "For each medicine, provide a short description of its uses. "
                    "Return the result as a JSON array of objects with 'name' and 'uses'."
                )
                response = model.generate_content([
                    prompt,
                    {
                        'mime_type': 'image/png',
                        'data': img_str
                    }
                ])
                import json
                import re
                match = re.search(r'\[.*\]', response.text, re.DOTALL)
                if match:
                    medicines += json.loads(match.group(0))
                else:
                    error = 'Could not extract medicines from the prescription.'
            except Exception as e:
                error = f'Error analyzing prescription: {e}'
        # 2. If medicine name is provided, analyze it
        if med_name:
            try:
                prompt = (
                    f"What is the use of the medicine '{med_name}'? "
                    "Give a short, clear description for a patient. "
                    "Return the result as a JSON object with 'name' and 'uses'."
                )
                response = model.generate_content(prompt)
                import json
                import re
                match = re.search(r'\{.*\}', response.text, re.DOTALL)
                if match:
                    med_obj = json.loads(match.group(0))
                    # Avoid duplicates
                    if not any(m['name'].lower() == med_obj['name'].lower() for m in medicines):
                        medicines.append(med_obj)
                else:
                    error = (error or '') + f' Could not extract info for {med_name}.'
            except Exception as e:
                error = (error or '') + f' Error analyzing medicine name: {e}'
        if not medicines and not error:
            error = 'Please upload a prescription image or enter a medicine name.'
    return render_template('prescription_analyzer.html', medicines=medicines if medicines else None, error=error)

def get_ai_response(user_message):
    import google.generativeai as genai
    api_key = current_app.config.get('GOOGLE_API_KEY')
    genai.configure(api_key=api_key)
    
    # Update the system prompt to ask for concise answers
    concise_system_prompt = SYSTEM_PROMPT + """
    Keep your answers friendly, concise, and to the point. 
    Aim for 1-3 sentences unless the user explicitly asks for more detail.
    """
    
    # Use a ChatSession for better conversation flow
    model = genai.GenerativeModel(
        'gemini-1.5-flash',
        system_instruction=concise_system_prompt,
        safety_settings=SAFETY_SETTINGS
    )
    
    # For a stateless response like this, we don't need to manage history here,
    # just send the user message. The system prompt guides the single response.
    response = model.generate_content(user_message)
    
    return response.text

@bp.route('/admin/content-management')
@login_required
@admin_required
def content_management():
    return render_template('content_management.html')

@bp.route('/admin/site-analytics')
@login_required
@admin_required
def site_analytics():
    from .models import User, HealthChallenge, PillReminder, UserChallenge, ChatMessage
    from sqlalchemy import extract, func
    import datetime

    # User stats
    total_users = User.query.count()
    active_users = User.query.filter_by(is_active=True).count()
    # Challenge stats
    total_challenges = HealthChallenge.query.count()
    # Health tips (from the hardcoded list in health_tips route)
    health_tips_list = [
        "Drink at least 8 glasses of water a day to stay hydrated.",
        "Aim for 7-9 hours of quality sleep each night for optimal health.",
        "Incorporate at least 30 minutes of moderate exercise into your daily routine.",
        "Eat a balanced diet rich in fruits, vegetables, and whole grains.",
        "Practice mindfulness or meditation for 10-15 minutes daily to reduce stress.",
        "Take short breaks to stretch and move if you sit for long periods.",
        "Wash your hands frequently with soap and water for at least 20 seconds.",
        "Limit processed foods, sugary drinks, and excessive saturated fats.",
        "Schedule regular check-ups with your doctor and dentist.",
        "Connect with loved ones regularly to maintain strong social bonds."
    ]
    total_health_tips = len(health_tips_list)

    # Analytics graph data (last 12 months)
    today = datetime.date.today()
    months = []
    user_counts = []
    activity_counts = []
    for i in range(11, -1, -1):
        month = (today.replace(day=1) - datetime.timedelta(days=30*i)).replace(day=1)
        label = month.strftime('%b %Y')
        months.append(label)
        # User registrations
        user_count = User.query.filter(
            extract('year', User.id) == month.year,
            extract('month', User.id) == month.month
        ).count()
        # Activities: pill reminders, challenges joined, chat messages
        pill_count = PillReminder.query.filter(
            extract('year', PillReminder.created_at) == month.year,
            extract('month', PillReminder.created_at) == month.month
        ).count()
        challenge_count = UserChallenge.query.filter(
            extract('year', UserChallenge.created_at) == month.year,
            extract('month', UserChallenge.created_at) == month.month
        ).count()
        chat_count = ChatMessage.query.filter(
            extract('year', ChatMessage.timestamp) == month.year,
            extract('month', ChatMessage.timestamp) == month.month
        ).count()
        user_counts.append(user_count)
        activity_counts.append(pill_count + challenge_count + chat_count)

    return render_template('site_analytics.html',
        total_users=total_users,
        active_users=active_users,
        total_challenges=total_challenges,
        total_health_tips=total_health_tips,
        months=months,
        user_counts=user_counts,
        activity_counts=activity_counts
    )

@bp.route('/submit-feedback', methods=['POST'])
def submit_feedback():
    feedback_type = request.form.get('type')
    message = request.form.get('feedback')
    user_id = current_user.id if current_user.is_authenticated else None

    if not feedback_type or not message:
        flash('Please fill out all fields.', 'danger')
        return redirect(request.referrer or url_for('routes.dashboard'))

    feedback = Feedback(type=feedback_type, message=message, user_id=user_id)
    db.session.add(feedback)
    db.session.commit()
    flash('Thank you for your feedback!', 'success')
    return redirect(request.referrer or url_for('routes.dashboard'))

@bp.route('/admin/feedback')
@login_required
@admin_required
def admin_feedback():
    feedback_list = Feedback.query.order_by(Feedback.submitted_at.desc()).all()
    return render_template('admin_feedback.html', feedback_list=feedback_list)

@bp.route('/pill_reminder/check_due', methods=['GET'])
@login_required
def check_due_reminders():
    """Check for due pill reminders and return them as JSON"""
    from datetime import datetime, time, timedelta
    
    # Get current time
    now = datetime.now()
    current_time = now.time()
    current_date = now.date()
    
    # Get all active reminders for the current user
    active_reminders = PillReminder.query.filter_by(
        user_id=current_user.id, 
        is_active=True
    ).all()
    
    due_reminders = []
    
    for reminder in active_reminders:
        # Check if reminder is within the date range
        if (reminder.start_date <= current_date and 
            (reminder.end_date is None or reminder.end_date >= current_date)):
            
            reminder_time = reminder.reminder_time
            reminder_hour = reminder_time.hour
            reminder_minute = reminder_time.minute
            current_hour = current_time.hour
            current_minute = current_time.minute
            
            # Calculate time difference in minutes
            current_total_minutes = current_hour * 60 + current_minute
            reminder_total_minutes = reminder_hour * 60 + reminder_minute
            
            # Check if we're within 30 minutes of the reminder time
            time_diff = abs(current_total_minutes - reminder_total_minutes)
            
            # For debugging, let's be more lenient and check if we're within 2 hours
            # This will help us test the system
            if time_diff <= 120:  # 2 hours for testing, change to 30 for production
                
                # Check frequency
                should_show = False
                
                if reminder.frequency == 'daily':
                    should_show = True
                elif reminder.frequency == 'every_6_hours':
                    # Show if current hour matches the reminder hour pattern
                    should_show = (reminder_hour % 6 == 0 and 
                                 current_hour >= reminder_hour and 
                                 current_hour < reminder_hour + 6)
                elif reminder.frequency == 'every_12_hours':
                    # Show if current hour matches the reminder hour pattern
                    should_show = (reminder_hour % 12 == 0 and 
                                 current_hour >= reminder_hour and 
                                 current_hour < reminder_hour + 12)
                elif reminder.frequency == 'before_meal':
                    # Show at common meal times (7, 12, 18)
                    should_show = reminder_hour in [7, 12, 18]
                elif reminder.frequency == 'after_meal':
                    # Show after common meal times (9, 14, 20)
                    should_show = reminder_hour in [9, 14, 20]
                elif reminder.frequency == 'specific_days':
                    # For specific days, show daily for now
                    should_show = True
                elif reminder.frequency == 'custom':
                    # For custom frequency, show daily for now
                    should_show = True
                else:
                    # Default to daily
                    should_show = True
                
                if should_show:
                    due_reminders.append({
                        'id': reminder.id,
                        'medicine_name': reminder.medicine_name,
                        'dosage': reminder.dosage,
                        'time': reminder.reminder_time.strftime('%I:%M %p'),
                        'notes': reminder.notes,
                        'frequency': reminder.get_frequency_display(),
                        'debug_info': {
                            'current_time': current_time.strftime('%H:%M'),
                            'reminder_time': reminder_time.strftime('%H:%M'),
                            'time_diff_minutes': time_diff,
                            'frequency': reminder.frequency
                        }
                    })
    
    # Add debug information
    debug_info = {
        'current_time': current_time.strftime('%H:%M'),
        'current_date': current_date.strftime('%Y-%m-%d'),
        'total_active_reminders': len(active_reminders),
        'due_reminders_count': len(due_reminders)
    }
    
    return jsonify({
        'due_reminders': due_reminders,
        'count': len(due_reminders),
        'debug_info': debug_info
    })

@bp.route('/pill_reminder/mark_taken/<int:reminder_id>', methods=['POST'])
@login_required
def mark_reminder_taken(reminder_id):
    """Mark a pill reminder as taken"""
    reminder = PillReminder.query.get_or_404(reminder_id)
    if reminder.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        # You could add a field to track when reminders were taken
        # For now, we'll just return success
        return jsonify({'success': True, 'message': 'Reminder marked as taken'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/pill_reminder/test', methods=['POST'])
@login_required
def create_test_reminder():
    """Create a test reminder for the current time"""
    from datetime import datetime, timedelta
    
    try:
        # Create a test reminder for 2 minutes from now
        test_time = datetime.now() + timedelta(minutes=2)
        
        test_reminder = PillReminder(
            user_id=current_user.id,
            medicine_name='Test Medicine',
            dosage='1 tablet',
            reminder_time=test_time.time(),
            frequency='daily',
            start_date=datetime.now().date(),
            end_date=datetime.now().date() + timedelta(days=1),
            notes='This is a test reminder for debugging',
            is_active=True
        )
        
        db.session.add(test_reminder)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Test reminder created for {test_time.strftime("%I:%M %p")}',
            'reminder_id': test_reminder.id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500