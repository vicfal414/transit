import pandas as pd
import sqlite3
import os
import requests
import zipfile
import io

# URLs for the static GTFS zips
# Note: Ensure you use the direct download URLs
# FEEDS = {
#     'MTA': 'http://web.mta.info/developers/data/nyct/subway/google_transit.zip',
#     'NJT': 'https://s3.amazonaws.com/njtransit-open-data/lightrail_data.zip',
#     'PATH': 'https://transitfeeds.com/p/port-authority-of-ny-nj/251/latest/download'
# }
FEEDS = {
        'MTA': 'http://web.mta.info/developers/data/nyct/subway/google_transit.zip',
        'NJT': 'https://s3.amazonaws.com/njtransit-open-data/lightrail_data.zip',
        'PATH': 'https://data.ny.gov/api/views/y4mv-s2u6/files/1e626bf4-6481-4235-976e-ea78a4b6bf57?download=true&filename=path_gtfs.zip'
    }

files_to_load = ['stops.txt', 'routes.txt', 'trips.txt', 'stop_times.txt']

# Remove the old database if it exists
if os.path.exists('timetable.sqlite'):
    os.remove('timetable.sqlite')

conn = sqlite3.connect('timetable.sqlite')

for agency, url in FEEDS.items():
    print(f"Downloading {agency}...")
    try:
        r = requests.get(url)
        r.raise_for_status()
        # Extract the zip in memory
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for file_name in files_to_load:
                if file_name in z.namelist():
                    with z.open(file_name) as f:
                        df = pd.read_csv(f, dtype=str)
                        
                        # Prefix IDs
                        for col in df.columns:
                            if col.endswith('_id'):
                                df[col] = agency + '_' + df[col].astype(str)
                        
                        table_name = file_name.replace('.txt', '')
                        df.to_sql(table_name, conn, if_exists='append', index=False)
                        print(f"  - Loaded {table_name}")
    except Exception as e:
        print(f"Failed to process {agency}: {e}")

# Build indexes
conn.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_stop ON stop_times (stop_id);")
conn.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_trip ON stop_times (trip_id);")
conn.execute("CREATE INDEX IF NOT EXISTS idx_trips_trip ON trips (trip_id);")

conn.commit()
conn.close()
print("Database build complete.")
