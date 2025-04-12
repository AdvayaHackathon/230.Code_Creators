from flask import Flask, request, render_template, jsonify, redirect, url_for, session, flash
from pymongo import MongoClient
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from call_reminder import make_reminder_call
from datetime import datetime
import mysql.connector
import pytz

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Timezone
IST = pytz.timezone('Asia/Kolkata')

# --- MySQL connection ---
mysql_conn = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",
    database="mediswitch_users"
)
cursor = mysql_conn.cursor()

# --- MongoDB connection ---
client = MongoClient("mongodb://localhost:27017/")
db = client['medicare']
medicine_collection = db['medicines']
feedback_collection = db['feedbacks']

# --- Preprocess medicine data for recommendation ---
data = list(medicine_collection.find())
text_features = ["Composition", "Uses", "Side_effects"]
for entry in data:
    entry['combined_features'] = ' '.join([str(entry.get(field, '')) for field in text_features])

tfidf = TfidfVectorizer(stop_words='english')
tfidf_matrix = tfidf.fit_transform([entry['combined_features'] for entry in data])
cosine_sim = cosine_similarity(tfidf_matrix, tfidf_matrix)

def get_medicine_type(medicine_name):
    keywords = [
        "Tablet", "Syrup", "Cream", "Inhaler", "Suspension", "Capsule",
        "Injection", "Ointment", "Gel", "Solution", "Drops", "Powder",
        "Spray", "Lotion"
    ]
    for keyword in keywords:
        if keyword.lower() in medicine_name.lower():
            return keyword
    return None

def recommend_medicine(medicine_name):
    medicine = next((m for m in data if m['Medicine Name'].lower() == medicine_name.lower()), None)
    if not medicine:
        return []

    idx = data.index(medicine)
    medicine_type = get_medicine_type(medicine_name)
    composition = medicine['Composition']
    dosage = medicine.get('Dosage', None)

    sim_scores = sorted(list(enumerate(cosine_sim[idx])), key=lambda x: x[1], reverse=True)[1:]

    recommendations = []
    for i, score in sim_scores:
        recommended = data[i]
        if get_medicine_type(recommended['Medicine Name']) == medicine_type and recommended['Composition'] == composition:
            if dosage is None or recommended.get('Dosage') == dosage:
                recommendations.append({
                    'name': recommended['Medicine Name'],
                    'image_url': recommended.get('Image URL'),
                    'company': recommended.get('Company', 'Unknown'),
                    'excellent_review': recommended.get('Excellent Review %', 0),
                    'average_review': recommended.get('Average Review %', 0),
                    'poor_review': recommended.get('Poor Review %', 0),
                    'description': recommended.get('Description', 'No description')
                })
        if len(recommendations) >= 5:
            break

    return sorted(recommendations, key=lambda x: x['excellent_review'], reverse=True)

# ---------------- Routes ----------------

@app.route('/')
def home():
    if 'username' not in session:
        return redirect(url_for('login'))

    num_medicines = len(data)
    total_companies = len(set(m.get('Company', '') for m in data if 'Company' in m))
    if all(key in data[0] for key in ['Excellent Review %', 'Average Review %', 'Poor Review %']):
        avg_rating = sum(
            (m['Excellent Review %'] + m['Average Review %'] + m['Poor Review %']) / 3
            for m in data
        ) / num_medicines
    else:
        avg_rating = 0

    medicine_types = {get_medicine_type(m['Medicine Name']) for m in data if get_medicine_type(m['Medicine Name'])}
    return render_template('home.html', num_medicines=num_medicines, total_companies=total_companies, avg_rating=avg_rating, medicine_types=medicine_types)

@app.route('/recommend', methods=['GET', 'POST'])
def recommend():
    if 'username' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        medicine_to_search = request.form['medicine_name']
        recommendations = recommend_medicine(medicine_to_search)
        return render_template('recommend.html', recommendations=recommendations)
    return render_template('recommend.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/suggestions', methods=['GET'])
def suggestions():
    query = request.args.get('query', '').lower()
    suggestions = [m['Medicine Name'] for m in data if query in m['Medicine Name'].lower()]
    return jsonify(suggestions)

@app.route('/medicine/<name>')
def medicine_detail(name):
    if 'username' not in session:
        return redirect(url_for('login'))

    medicine = next((m for m in data if m['Medicine Name'].lower() == name.lower()), None)
    if medicine:
        return render_template('medicine_detail.html', medicine=medicine)
    return "Medicine not found", 404

@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    if 'username' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            feedback_collection.insert_one({
                'message': message,
                'created_at': datetime.utcnow()
            })
            return redirect(url_for('feedback'))

    feedbacks = list(feedback_collection.find().sort('created_at', -1))
    return render_template('feedback.html', feedbacks=feedbacks)

@app.route('/call')
def call():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('call.html')

@app.route('/payment')
def payment():
    if 'username' not in session:
        return redirect(url_for('login'))

    plan = request.args.get('plan')
    return render_template('payment.html', plan=plan)

@app.route('/set_reminder', methods=['GET', 'POST'])
def set_reminder():
    if 'username' not in session:
        flash("Please log in to set a reminder.")
        return redirect(url_for('login'))

    if request.method == 'POST':
        medicine = request.form['medicine']
        time_to_take = request.form['time_to_take']
        phone_number = request.form['phone_number']

        cursor.execute("SELECT id FROM users WHERE username = %s", (session['username'],))
        user_id = cursor.fetchone()[0]

        # Save reminder to DB
        cursor.execute(
            "INSERT INTO medicine_schedule (user_id, medicine_name, time_to_take) VALUES (%s, %s, %s)",
            (user_id, medicine, time_to_take)
        )
        mysql_conn.commit()

        # Parse datetime for reminder
        today = datetime.now(IST).date()
        reminder_datetime = IST.localize(datetime.combine(today, datetime.strptime(time_to_take, "%H:%M").time()))

        # Schedule Twilio call
        if reminder_datetime > datetime.now(IST):
            scheduler.add_job(
                func=make_reminder_call,
                trigger=DateTrigger(run_date=reminder_datetime),
                args=[phone_number, medicine],
                id=f"{phone_number}{medicine}{time_to_take}",
                replace_existing=True
            )

        flash("Reminder set successfully! You will receive a call at the scheduled time.")
        return redirect(url_for('home'))

    return render_template('set_reminder.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        cursor.execute("SELECT * FROM users WHERE username = %s AND password = %s", (username, password))
        user = cursor.fetchone()
        if user:
            session['username'] = username
            return redirect(url_for('home'))
        else:
            flash('Invalid credentials')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        existing_user = cursor.fetchone()
        if existing_user:
            flash('Username already exists. Please choose another one.')
            return redirect(url_for('signup'))

        cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, password))
        mysql_conn.commit()
        flash('Signup successful. Please log in.')
        return redirect(url_for('login'))

    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/nearby-hospital')
def nearby_hospital():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('nearby_hospital.html')

@app.route('/map')
def map_view():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('map.html')

# Start the background scheduler
scheduler = BackgroundScheduler(timezone=IST)
scheduler.start()

if __name__ == '__main__':
    app.run(debug=True)
