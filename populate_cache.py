import os
import json
import sqlite3
import requests

DB = 'sessions.db'
WATCHMODE_API_KEY = os.environ.get("WATCHMODE_API_KEY")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY")

conn = sqlite3.connect(DB)
c = conn.cursor()

# --- Fetch titles from Watchmode ---
url = f"https://api.watchmode.com/v1/list-titles/?apiKey={WATCHMODE_API_KEY}&types=movie&limit=5"
print("Fetching Watchmode titles from:", url)
res = requests.get(url)
if res.status_code != 200:
    print("Watchmode fetch failed:", res.status_code)
    exit()
data = res.json()
titles = data.get('titles', data)  # some endpoints return 'titles'

print(f"Number of titles fetched: {len(titles)}")

added_count = 0
for t in titles:
    movie_id = t['id']
    title = t['title']
    imdb_id = t.get('imdb_id')  # used for OMDb

    # Fetch sources
    sources_url = f"https://api.watchmode.com/v1/title/{movie_id}/sources/?apiKey={WATCHMODE_API_KEY}"
    sources_res = requests.get(sources_url)
    if sources_res.status_code != 200:
        print(f"Fetching sources for {title} ({movie_id}) - Status: {sources_res.status_code}")
        continue
    sources = sources_res.json()

    # Filter for Netflix, Prime, Disney+ (GB)
    availability = {
        "netflix": any(s['name'] == 'Netflix' and s['region'] == 'GB' for s in sources),
        "prime": any(s['name'] == 'Amazon Prime Video' and s['region'] == 'GB' for s in sources),
        "disney_plus": any(s['name'] == 'Disney Plus' and s['region'] == 'GB' for s in sources)
    }

    if not any(availability.values()):
        print(f"Skipping {title} - not available on Netflix, Prime, or Disney+ in GB")
        continue

    # Fetch poster from OMDb
    poster_url = None
    if imdb_id:
        omdb_url = f"http://www.omdbapi.com/?i={imdb_id}&apikey={OMDB_API_KEY}"
        omdb_res = requests.get(omdb_url)
        if omdb_res.status_code == 200:
            omdb_data = omdb_res.json()
            if omdb_data.get("Poster") and omdb_data["Poster"] != "N/A":
                poster_url = omdb_data["Poster"]
                print(f"OMDb poster found for {title}")
            else:
                print(f"OMDb poster not found for {title}")
        else:
            print(f"OMDb request failed for {title} - Status: {omdb_res.status_code}")

    # Insert into DB
    c.execute(
        "INSERT OR REPLACE INTO movies (id, title, poster, imdb_id, sources) VALUES (?, ?, ?, ?, ?)",
        (movie_id, title, poster_url, imdb_id, json.dumps(availability))
    )
    print(f"Added {title} - Poster: {'Yes' if poster_url else 'No'}, Availability: {availability}")
    added_count += 1

conn.commit()
conn.close()
print(f"Cache populated with {added_count} movies for testing.")
