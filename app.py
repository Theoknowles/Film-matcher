import os
import uuid
import json
import csv
import random
import sqlite3
from flask import Flask, render_template, request, jsonify, url_for
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app)

DB = 'sessions.db'

# --- Database setup ---
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('''
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        user1_votes TEXT,
        user2_votes TEXT,
        movie_order TEXT
    )
    ''')
    conn.commit()
    conn.close()

# --- Load movies ---
def load_movies():
    movies = []
    with open('movies.csv') as f:
        reader = csv.DictReader(f)
        for row in reader:
            movies.append({"id": int(row["id"]), "title": row["title"]})
    return movies

MOVIES = load_movies()
init_db()

# --- Routes ---
@app.route('/')
def index():
    return render_template('create.html', session_id=None, share_link=None)


@app.route('/create')
def create_session():
    # Generate unique session ID
    session_id = str(uuid.uuid4())

    # Randomize movie order
    movie_order = [film['id'] for film in MOVIES]
    random.shuffle(movie_order)

    # Save session in DB
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute(
        "INSERT INTO sessions (session_id, user1_votes, user2_votes, movie_order) VALUES (?, ?, ?, ?)",
        (session_id, "{}", "{}", json.dumps(movie_order))
    )
    conn.commit()
    conn.close()

    # Build shareable link for the second user
    share_link = url_for('session_page', session_id=session_id, user='user2', _external=True)

    # Render template with session info
    return render_template('create.html', session_id=session_id, share_link=share_link)


@app.route('/session/<session_id>/<user>')
def session_page(session_id, user):
    return render_template('swipe_socket.html', session_id=session_id, user=user)

@app.route('/next_film/<session_id>/<user>')
def next_film(session_id, user):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT user1_votes, user2_votes, movie_order FROM sessions WHERE session_id=?", (session_id,))
    row = c.fetchone()
    votes1 = json.loads(row[0] or "{}")
    votes2 = json.loads(row[1] or "{}")
    movie_order = json.loads(row[2])

    voted_ids = set(votes1.keys()).union(set(votes2.keys()))

    for film_id in movie_order:
        if str(film_id) not in voted_ids:
            film = next(f for f in MOVIES if f['id'] == film_id)
            conn.close()
            return jsonify(film)

    conn.close()
    return jsonify(None)

@app.route('/vote/<session_id>/<user>', methods=['POST'])
def vote(session_id, user):
    data = request.json
    film_id = str(data.get('film_id'))
    vote_value = data.get('vote')
    vote_value = bool(vote_value) if not isinstance(vote_value, str) else vote_value.lower() == 'true'

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT user1_votes, user2_votes FROM sessions WHERE session_id=?", (session_id,))
    row = c.fetchone()
    votes1 = json.loads(row[0] or "{}")
    votes2 = json.loads(row[1] or "{}")

    if user == 'user1':
        votes1[film_id] = vote_value
    else:
        votes2[film_id] = vote_value

    c.execute("UPDATE sessions SET user1_votes=?, user2_votes=? WHERE session_id=?",
              (json.dumps(votes1), json.dumps(votes2), session_id))
    conn.commit()
    conn.close()

    matches = [film for film in MOVIES if votes1.get(str(film['id'])) and votes2.get(str(film['id']))]
    socketio.emit('update_matches', matches, room=session_id)
    return jsonify(success=True)

# --- Socket.IO ---
@socketio.on('join')
def on_join(data):
    session_id = data['session_id']
    join_room(session_id)

# --- Run ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    socketio.run(app, host='0.0.0.0', port=port)
