import pandas as pd
import sqlite3
import os
import urllib.request
import zipfile
import io

# Active, verified GTFS feeds
FEEDS = {
    'MTA': 'http://web.mta.info/developers/data/nyct/subway/google_transit.zip',
    # Reliable public mirror for PATH (Port Authority Trans-Hudson)
    'PATH': 'https://transitfeeds.com/p/port-authority-of-ny-nj/251/latest/download'
}

files_to_load = ['stops.txt', 'routes.txt', 'trips.txt', 'stop_times.txt']

if os.path.exists('timetable.sqlite'):
    os.remove('timetable.sqlite')

conn = sqlite3.connect('timetable.sqlite')

req_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

for agency, url in FEEDS.items():
    print(f"Downloading {agency}...")
    try:
        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            zip_content = response.read()
            
        with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
            for file_name in files_to_load:
                if file_name in z.namelist():
                    with z.open(file_name) as f:
                        df = pd.read_csv(f, dtype=str)
                        
                        for col in df.columns:
                            if col.endswith('_id'):
                                df[col] = agency + '_' + df[col].astype(str)
                        
                        table_name = file_name.replace('.txt', '')
                        df.to_sql(table_name, conn, if_exists='append', index=False)
                        print(f"  - Loaded {table_name}")
    except Exception as e:
        print(f"⚠️ Warning: Could not download {agency} data ({e}). Skipping...")

# Build database indexes for fast querying
conn.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_stop ON stop_times (stop_id);")
conn.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_trip ON stop_times (trip_id);")
conn.execute("CREATE INDEX IF NOT EXISTS idx_trips_trip ON trips (trip_id);")
conn.commit()
conn.close()
print("Database build complete.")
