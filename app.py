import os
import uuid
import json
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
        user2_votes TEXT
    )
    ''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS movies (
        id INTEGER PRIMARY KEY,
        title TEXT,
        poster TEXT,
        imdb_id TEXT,
        sources TEXT
    )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Routes ---
@app.route('/')
def index():
    return render_template('create.html', session_id=None, share_link=None)


@app.route('/create')
def create_session():
    session_id = str(uuid.uuid4())
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # Save empty votes for the session
    c.execute(
        "INSERT INTO sessions (session_id, user1_votes, user2_votes) VALUES (?, ?, ?)",
        (session_id, "{}", "{}")
    )
    conn.commit()
    conn.close()

    share_link = url_for('session_page', session_id=session_id, user='user2', _external=True)
    return render_template('create.html', session_id=session_id, share_link=share_link)


@app.route('/session/<session_id>/<user>')
def session_page(session_id, user):
    return render_template('swipe_socket.html', session_id=session_id, user=user)


@app.route('/next_film/<session_id>/<user>')
def next_film(session_id, user):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT user1_votes, user2_votes FROM sessions WHERE session_id=?", (session_id,))
    row = c.fetchone()
    votes1 = json.loads(row["user1_votes"] or "{}")
    votes2 = json.loads(row["user2_votes"] or "{}")

    # Fetch movies in order
    c.execute("SELECT * FROM movies ORDER BY id")
    movies = c.fetchall()
    for film_row in movies:
        film_id = str(film_row["id"])
        if film_id not in votes1 and film_id not in votes2:
            film = dict(film_row)
            film["sources"] = json.loads(film["sources"])
            film["poster"] = film.get("poster") or ""
            conn.close()
            return jsonify(film)

    conn.close()
    return jsonify(None)


@app.route('/vote/<session_id>/<user>', methods=['POST'])
def vote(session_id, user):
    data = request.json
    film_id = str(data.get('film_id'))
    vote_value = bool(data.get('vote'))

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

    # Calculate matches
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM movies")
    movies = c.fetchall()
    matches = []
    for film_row in movies:
        film_id_str = str(film_row["id"])
        if votes1.get(film_id_str) and votes2.get(film_id_str):
            film = dict(film_row)
            film["sources"] = json.loads(film["sources"])
            film["poster"] = film.get("poster") or ""
            matches.append(film)
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
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
