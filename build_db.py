import pandas as pd
import sqlite3
import os
import urllib.request
import zipfile
import io

# Updated, highly reliable URLs
FEEDS = {
    'MTA': 'http://web.mta.info/developers/data/nyct/subway/google_transit.zip',
    'NJT': 'https://njtransit-open-data.s3.amazonaws.com/lightrail_data.zip',
    'PATH': 'https://data.ny.gov/api/views/y4mv-s2u6/files/1e626bf4-6481-4235-976e-ea78a4b6bf57?download=true&filename=path_gtfs.zip'
}

files_to_load = ['stops.txt', 'routes.txt', 'trips.txt', 'stop_times.txt']

if os.path.exists('timetable.sqlite'):
    os.remove('timetable.sqlite')

conn = sqlite3.connect('timetable.sqlite')

# Hardened User-Agent to mimic a real human clicking the download link
req_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
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
        print(f"❌ CRITICAL FAILURE processing {agency}: {e}")
        # Force the GitHub Action to fail so you can see the error in the logs
        raise Exception(f"Failed to download {agency} data. See logs above.")

conn.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_stop ON stop_times (stop_id);")
conn.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_trip ON stop_times (trip_id);")
conn.execute("CREATE INDEX IF NOT EXISTS idx_trips_trip ON trips (trip_id);")
conn.commit()
conn.close()
print("Database build complete.")
