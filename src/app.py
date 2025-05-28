from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
import threading
from paho.mqtt.client import Client as MqttClient
import time

# Flask app – using templates/ and secret key for flashing messages
app = Flask(__name__, template_folder='templates')
app.secret_key = 'supersecretkey'
DB = 'rfid.db'

# MQTT Settings
MQTT_BROKER = 'localhost'
TOPIC_SCAN = 'rfid/scan'

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
    conn.commit()
    conn.close()

# MQTT client thread
def mqtt_thread():
    client = MqttClient()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER)
    client.loop_forever()

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

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        conn = sqlite3.connect(DB)
        c = conn.cursor()
        c.execute('SELECT card FROM cards WHERE username=?', (username,))
        row = c.fetchone()
        conn.close()
        if not row:
            flash('Username not registered.')
            return redirect(url_for('login'))
        card_expected = row[0]
        flash('Please scan your card within 10 seconds.')
        scanned = wait_for_card(10, lambda c: c == card_expected)
        if scanned:
            return redirect(url_for('dashboard', user=username))
        else:
            flash('Card scan failed or did not match. Try again.')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        attempts = 0
        while attempts < 3:
            flash(f'Attempt {attempts+1}: Please scan a new card within 10 seconds.')
            scanned = wait_for_card(10, lambda c: True)
            if scanned:
                conn = sqlite3.connect(DB)
                c = conn.cursor()
                try:
                    c.execute('INSERT INTO cards(card,username) VALUES(?,?)', (scanned, username))
                    conn.commit()
                    conn.close()
                    flash('Registration successful!')
                    return redirect(url_for('index'))
                except sqlite3.IntegrityError:
                    flash('Card already registered. Try a different card.')
                    conn.close()
                    return redirect(url_for('register'))
            attempts += 1
        flash('Registration failed after 3 attempts.')
        return redirect(url_for('index'))
    return render_template('register.html')

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
