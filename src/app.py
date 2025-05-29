from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
import threading
from paho.mqtt.client import Client as MqttClient
import time

app = Flask(__name__, template_folder='templates')
app.secret_key = 'supersecretkey'
app.config['SESSION_PERMANENT'] = False
DB = 'rfid.db'

# MQTT Settings
MQTT_BROKER = 'localhost'
TOPIC_SCAN = 'rfid/scan'
TOPIC_CONTROL = 'rfid/control'

# In-memory mapping of pending scans: list of (card_id, timestamp)
pending = []
PENDING_TTL = 5  # seconds to keep a pending scan
_pending_lock = threading.Lock()

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    client.subscribe(TOPIC_SCAN)

def on_message(client, userdata, msg):
    card_id = msg.payload.decode()
    now = time.time()
    with _pending_lock:
        pending.append((card_id, now))

# init DB
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS cards(
        card TEXT PRIMARY KEY,
        username TEXT UNIQUE
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS banned(
        card TEXT PRIMARY KEY
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS attempts(
        id INTEGER PRIMARY KEY,
        card TEXT,
        successful BOOLEAN,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
    )''')
    conn.commit()
    conn.close()

# MQTT client thread
mqtt_client = MqttClient()
def mqtt_thread():
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect(MQTT_BROKER)
    mqtt_client.loop_forever()

# Helper to clean expired pending entries and find a matching card
def wait_for_card(timeout, predicate):
    start = time.time()
    while time.time() - start < timeout:
        now = time.time()
        with _pending_lock:
            # remove expired
            pending[:] = [(c, t) for c, t in pending if now - t <= PENDING_TTL]
            # check for matches
            for idx, (c, t) in enumerate(pending):
                if predicate(c):
                    pending.pop(idx)
                    return c
        time.sleep(0.1)
    return None

# Publish control messages to RPi
def set_scan_state(enabled: bool):
    state = 'start' if enabled else 'stop'
    print("changing the state", state)
    mqtt_client.publish(TOPIC_CONTROL, state, qos=1)

@app.route('/')
def index():
    session.pop('reg_attempts', None)
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'reg_attempts' not in session:
        session['reg_attempts'] = 0

    if request.method == 'POST':
        username = request.form['username'].strip()
        if not username:
            flash('Username is required.')
            return redirect(url_for('register'))

        # signal RPi to start scanning
        set_scan_state(True)
        flash('Please scan a new card within 3 seconds.')
        card = wait_for_card(3, lambda c: True)
        # signal RPi to stop publishing
        set_scan_state(False)

        session['reg_attempts'] += 1
        if not card:
            flash(f'No card detected. Attempt {session["reg_attempts"]} of 3.')
        else:
            conn = sqlite3.connect(DB)
            c = conn.cursor()
            try:
                c.execute('INSERT INTO cards(card, username) VALUES(?, ?)', (card, username))
                conn.commit()
                flash('Registration successful!')
                session.pop('reg_attempts', None)
                conn.close()
                return redirect(url_for('index'))
            except sqlite3.IntegrityError:
                flash(f'Card or username already registered. Attempt {session["reg_attempts"]} of 3.')
                conn.close()

        if session['reg_attempts'] >= 3:
            flash('Registration failed after 3 attempts.')
            session.pop('reg_attempts', None)
            return redirect(url_for('index'))
        return redirect(url_for('register'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # start scan
        set_scan_state(True)
        flash('Please scan your card within 3 seconds.')
        card = wait_for_card(3, lambda c: True)
        # stop scan
        set_scan_state(False)

        if card:
            conn = sqlite3.connect(DB)
            c = conn.cursor()
            c.execute('SELECT 1 FROM banned WHERE card = ?', (card,))
            banned_row = c.fetchone()
            if banned_row:
                c.execute('INSERT INTO attempts(card, successful) VALUES(?, ?)', (card,0))
                conn.commit()
                flash('You shall not pass because you are banned.', 'banned')
                conn.close()
                return redirect(url_for('index'))
            c.execute('SELECT username FROM cards WHERE card = ?', (card,))
            row = c.fetchone()
            if row:
                c.execute('INSERT INTO attempts(card, successful) VALUES(?, ?)', (card,1))
                conn.commit()
                conn.close()
                return redirect(url_for('dashboard', user=row[0]))
            else:
                c.execute('INSERT INTO attempts(card, successful) VALUES(?, ?)', (card,0))
                conn.commit()
                conn.close()
                flash('Card not recognized.', 'banned')
        else:
            flash('No card detected.', 'banned')
        return redirect(url_for('index'))

    session.pop('_flashes', None)
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    user = request.args.get('user', '')
    return render_template('dashboard.html', user=user)

@app.route('/logout')
def logout():
    flash('You have been logged out.')
    return redirect(url_for('index'))

if __name__ == '__main__':
    init_db()
    t = threading.Thread(target=mqtt_thread)
    t.daemon = True
    t.start()
    app.run(host='0.0.0.0', port=5000)
