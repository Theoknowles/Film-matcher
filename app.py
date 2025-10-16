import os
import uuid
import json
import random
import sqlite3
import requests
from flask import Flask, render_template, request, jsonify, url_for
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app)

DB = 'sessions.db'
WATCHMODE_API_KEY = os.environ.get("WATCHMODE_API_KEY")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY")

# --- Database setup ---
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('''
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        user1_votes TEXT,
        user2_votes TEXT,
        movie_order TEXT,
        services TEXT
    )
    ''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS movies (
        id INTEGER PRIMARY KEY,
        title TEXT,
        poster_url TEXT,
        imdb_id TEXT,
        sources TEXT
    )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Populate movies if empty ---
def populate_movies(limit=10):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM movies")
    if c.fetchone()[0] > 0:
        conn.close()
        return  # already populated

    url = f"https://api.watchmode.com/v1/list-titles/?apiKey={WATCHMODE_API_KEY}&types=movie&limit={limit}"
    print("Fetching Watchmode titles from:", url)
    res = requests.get(url)
    if res.status_code != 200:
        print("Watchmode fetch failed:", res.status_code)
        conn.close()
        return

    data = res.json()
    titles = data.get('titles', data)
    added_count = 0

    for t in titles:
        movie_id = t['id']
        title = t['title']
        imdb_id = t.get('imdb_id')

        sources_url = f"https://api.watchmode.com/v1/title/{movie_id}/sources/?apiKey={WATCHMODE_API_KEY}"
        sources_res = requests.get(sources_url)
        if sources_res.status_code != 200:
            continue
        sources_data = sources_res.json()

        availability = {
            "netflix": any(s['name'] == 'Netflix' and s['region'] == 'GB' for s in sources_data),
            "prime": any(s['name'] == 'Amazon Prime Video' and s['region'] == 'GB' for s in sources_data),
            "disney_plus": any(s['name'] == 'Disney Plus' and s['region'] == 'GB' for s in sources_data),
            "iplayer": any(s['name'] == 'BBC iPlayer' and s['region'] == 'GB' for s in sources_data),
            "all4": any(s['name'] == 'All 4' and s['region'] == 'GB' for s in sources_data),
        }

        if not any(availability.values()):
            continue

        poster_url = None
        if imdb_id:
            omdb_url = f"http://www.omdbapi.com/?i={imdb_id}&apikey={OMDB_API_KEY}"
            omdb_res = requests.get(omdb_url)
            if omdb_res.status_code == 200:
                omdb_data = omdb_res.json()
                if omdb_data.get("Poster") and omdb_data["Poster"] != "N/A":
                    poster_url = omdb_data["Poster"]

        c.execute(
            "INSERT OR REPLACE INTO movies (id, title, poster_url, imdb_id, sources) VALUES (?, ?, ?, ?, ?)",
            (movie_id, title, poster_url, imdb_id, json.dumps(availability))
        )
        added_count += 1

    conn.commit()
    conn.close()
    print(f"Cache populated with {added_count} movies.")

# --- Populate at startup ---
populate_movies(limit=10)

# --- Routes ---
@app.route('/')
def index():
    return render_template('create.html', session_id=None, share_link=None)

@app.route('/create')
def create_session():
    services = request.args.get('services', 'netflix,prime,disney_plus,iplayer,all4').split(',')

    session_id = str(uuid.uuid4())

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT id FROM movies")
    movie_ids = [row[0] for row in c.fetchall()]
    random.shuffle(movie_ids)

    c.execute(
        "INSERT INTO sessions (session_id, user1_votes, user2_votes, movie_order, services) VALUES (?, ?, ?, ?, ?)",
        (session_id, "{}", "{}", json.dumps(movie_ids), json.dumps(services))
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
    c = conn.cursor()
    c.execute("SELECT user1_votes, user2_votes, movie_order, services FROM sessions WHERE session_id=?", (session_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify(None)

    votes1 = json.loads(row[0] or "{}")
    votes2 = json.loads(row[1] or "{}")
    movie_order = json.loads(row[2])
    allowed_services = json.loads(row[3])

    for film_id in movie_order:
        if str(film_id) in votes1 or str(film_id) in votes2:
            continue

        c.execute("SELECT title, poster_url, sources FROM movies WHERE id=?", (film_id,))
        movie = c.fetchone()
        if not movie:
            continue

        title, poster_url, sources_json = movie
        sources = json.loads(sources_json)

        # Filter by allowed streaming services
        if any(sources.get(s) for s in allowed_services):
            conn.close()
            return jsonify({
                "id": film_id,
                "title": title,
                "poster_url": poster_url,
                "sources": sources
            })

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

    return jsonify(success=True)

# --- Socket.IO ---
@socketio.on('join')
def on_join(data):
    session_id = data['session_id']
    join_room(session_id)

# --- Run ---
if __name__ == '__main__':
    import socket

    # Automatically find a free port
    s = socket.socket()
    s.bind(('', 0))
    free_port = s.getsockname()[1]
    s.close()

    print(f"✅ Starting Flask app on http://127.0.0.1:{free_port}")

    socketio.run(app, host='127.0.0.1', port=free_port, debug=True, use_reloader=False, allow_unsafe_werkzeug=True)
