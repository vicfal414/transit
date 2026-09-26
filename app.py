import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import os
import requests
from google.transit import gtfs_realtime_pb2
from geopy.geocoders import Nominatim
from geopy.distance import geodesic

# ==========================================
# 1. Configuration & Constants
# ==========================================
st.set_page_config(page_title="Commute Router", page_icon="🚆", layout="centered")

BUFFER_WTC_TO_MTA = 6
BUFFER_HBLR_TO_PATH = 4

# --- UPDATE THESE IDS ONCE YOU SEE THE DEBUGGER TABLE AT THE BOTTOM ---
# Replace the mock IDs below with the real IDs found in your database.
STATION_WTC_MTA = "WTC_MTA"             # e.g., "MTA_E14" or "MTA_A38"
STATION_WTC_PATH = "WTC_PATH"           # e.g., "PATH_WTC"
STATION_EXCHANGE_PATH = "EXCHANGE_PATH" # e.g., "PATH_EXP"
STATION_EXCHANGE_HBLR = "EXCHANGE_HBLR" # e.g., "NJT_39504"
STATION_LSP_HBLR = "LSP_HBLR"           # e.g., "NJT_39502"

# Initialize Geocoder for Address Lookups
geolocator = Nominatim(user_agent="my_personal_commute_app_v1")

# ==========================================
# 2. Dynamic Mock DB Generator
# ==========================================
def build_mock_database():
    """Generates mock schedules and coordinates for testing."""
    conn = sqlite3.connect(":memory:")
    
    conn.execute("CREATE TABLE trips (trip_id TEXT, route_id TEXT, trip_headsign TEXT)")
    conn.execute("CREATE TABLE stop_times (trip_id TEXT, stop_id TEXT, stop_sequence INT, arrival_time TEXT, departure_time TEXT)")
    conn.execute("CREATE TABLE stops (stop_id TEXT, stop_name TEXT, stop_lat REAL, stop_lon REAL)")
    
    stops = [
        ('MTA_34_ST', '34 St - Herald Sq', 40.7497, -73.9878),
        ('MTA_42_ST', 'Times Sq - 42 St', 40.7552, -73.9874),
        ('MTA_14_ST', '14 St - Union Sq', 40.7346, -73.9904),
        ('WTC_MTA', 'WTC / Fulton Center', 40.7103, -74.0090),
        ('WTC_PATH', 'WTC Oculus', 40.7118, -74.0121),
        ('EXCHANGE_PATH', 'Exchange Place', 40.7169, -74.0326),
        ('EXCHANGE_HBLR', 'Exchange Place HBLR', 40.7171, -74.0321),
        ('LSP_HBLR', 'Liberty State Park', 40.7115, -74.0535)
    ]
    conn.executemany("INSERT INTO stops VALUES (?, ?, ?, ?)", stops)
    
    mta_destinations = ['MTA_34_ST', 'MTA_42_ST', 'MTA_14_ST']
    stop_times_rows = []
    trips_rows = []
    
    for hour in range(0, 24):
        for m in [0, 15, 30, 45]:
            dep = f"{hour:02d}:{m:02d}:00"
            
            # --- MTA ---
            m_arr = m + 12
            arr_mta = f"{hour:02d}:{m_arr:02d}:00" if m_arr < 60 else f"{hour+1:02d}:{m_arr%60:02d}:00"
            for mta_stop in mta_destinations:
                trip_f = f'MTA_F_{hour}_{m}_{mta_stop}..N'
                trips_rows.append((trip_f, 'A', 'Inwood-207 St'))
                stop_times_rows.extend([
                    (trip_f, 'WTC_MTA', 1, dep, dep),
                    (trip_f, mta_stop, 2, arr_mta, arr_mta),
                ])
                trip_r = f'MTA_R_{hour}_{m}_{mta_stop}..S'
                trips_rows.append((trip_r, 'A', 'Far Rockaway-Mott Av'))
                stop_times_rows.extend([
                    (trip_r, mta_stop, 1, dep, dep),
                    (trip_r, 'WTC_MTA', 2, arr_mta, arr_mta),
                ])
            
            # --- PATH ---
            p_arr = m + 5
            arr_path = f"{hour:02d}:{p_arr:02d}:00" if p_arr < 60 else f"{hour+1:02d}:{p_arr%60:02d}:00"
            trips_rows.extend([
                (f'PATH_F_{hour}_{m}', 'NWK-WTC', 'World Trade Center'),
                (f'PATH_R_{hour}_{m}', 'WTC-NWK', 'Newark Penn Station')
            ])
            stop_times_rows.extend([
                (f'PATH_F_{hour}_{m}', 'EXCHANGE_PATH', 1, dep, dep),
                (f'PATH_F_{hour}_{m}', 'WTC_PATH', 2, arr_path, arr_path),
                (f'PATH_R_{hour}_{m}', 'WTC_PATH', 1, dep, dep),
                (f'PATH_R_{hour}_{m}', 'EXCHANGE_PATH', 2, arr_path, arr_path)
            ])
            
            # --- HBLR ---
            h_arr = m + 8
            arr_hblr = f"{hour:02d}:{h_arr:02d}:00" if h_arr < 60 else f"{hour+1:02d}:{h_arr%60:02d}:00"
            trips_rows.extend([
                (f'HBLR_F_{hour}_{m}', '8th St-Hoboken', 'Hoboken Terminal'),
                (f'HBLR_R_{hour}_{m}', 'Hoboken-8th St', '8th Street')
            ])
            stop_times_rows.extend([
                (f'HBLR_F_{hour}_{m}', 'LSP_HBLR', 1, dep, dep),
                (f'HBLR_F_{hour}_{m}', 'EXCHANGE_HBLR', 2, arr_hblr, arr_hblr),
                (f'HBLR_R_{hour}_{m}', 'EXCHANGE_HBLR', 1, dep, dep),
                (f'HBLR_R_{hour}_{m}', 'LSP_HBLR', 2, arr_hblr, arr_hblr)
            ])
            
    conn.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", stop_times_rows)
    conn.executemany("INSERT INTO trips VALUES (?, ?, ?)", trips_rows)
    conn.commit()
    return conn

# ==========================================
# 3. Database Routing Functions
# ==========================================
def parse_db_time(time_str, target_date):
    """Safely converts GTFS HH:MM:SS string to a datetime object for the selected day."""
    h, m, s = map(int, time_str.split(':'))
    extra_days = h // 24
    h = h % 24
    dt = datetime.combine(target_date, datetime.min.time()) + timedelta(days=extra_days, hours=h, minutes=m, seconds=s)
    return dt

def get_train_before(conn, origin_id, dest_id, arrive_by_dt):
    """REVERSE ROUTING: Finds the latest train arriving BEFORE the target time."""
    query = """
        SELECT t1.departure_time, t2.arrival_time, t1.trip_id, tr.route_id, tr.trip_headsign
        FROM stop_times t1
        JOIN stop_times t2 ON t1.trip_id = t2.trip_id
        JOIN trips tr ON t1.trip_id = tr.trip_id
        WHERE t1.stop_id LIKE ? 
          AND t2.stop_id LIKE ?
          AND t1.stop_sequence < t2.stop_sequence
          AND t2.arrival_time <= ?
        ORDER BY t2.arrival_time DESC
        LIMIT 1;
    """
    cur = conn.cursor()
    target_time_str = arrive_by_dt.strftime("%H:%M:%S")
    # Added % wildcards to fix MTA direction suffixes
    cur.execute(query, (origin_id + '%', dest_id + '%', target_time_str))
    result = cur.fetchone()
    
    if result:
        dep_str, arr_str, trip_id, route_id, headsign = result
        target_date = arrive_by_dt.date()
        return {
            "depart": parse_db_time(dep_str, target_date),
            "arrive": parse_db_time(arr_str, target_date),
            "trip_id": trip_id, "route": route_id, "headsign": headsign
        }
    return None

def get_train_after(conn, origin_id, dest_id, depart_after_dt):
    """FORWARD ROUTING: Finds the earliest train departing AFTER the target time."""
    query = """
        SELECT t1.departure_time, t2.arrival_time, t1.trip_id, tr.route_id, tr.trip_headsign
        FROM stop_times t1
        JOIN stop_times t2 ON t1.trip_id = t2.trip_id
        JOIN trips tr ON t1.trip_id = tr.trip_id
        WHERE t1.stop_id LIKE ? 
          AND t2.stop_id LIKE ?
          AND t1.stop_sequence < t2.stop_sequence
          AND t1.departure_time >= ?
        ORDER BY t1.departure_time ASC
        LIMIT 1;
    """
    cur = conn.cursor()
    target_time_str = depart_after_dt.strftime("%H:%M:%S")
    # Added % wildcards to fix MTA direction suffixes
    cur.execute(query, (origin_id + '%', dest_id + '%', target_time_str))
    result = cur.fetchone()
    
    if result:
        dep_str, arr_str, trip_id, route_id, headsign = result
        target_date = depart_after_dt.date()
        return {
            "depart": parse_db_time(dep_str, target_date),
            "arrive": parse_db_time(arr_str, target_date),
            "trip_id": trip_id, "route": route_id, "headsign": headsign
        }
    return None

# ==========================================
# 4. Live Delay Data (GTFS-RT)
# ==========================================
def get_live_delay(trip_id, agency, route_id=None):
    """Fetches live GTFS-RT delays for a specific trip. Returns delay in minutes."""
    delay_minutes = 0
    try:
        feed = gtfs_realtime_pb2.FeedMessage()
        
        if agency == "NJT":
            api_key = st.secrets.get("NJT_API_KEY", "")
            if not api_key or api_key == "your_future_njt_key_here":
                return 0 
                
            url = "https://api.njtransit.com/gtfs/tripupdates"
            headers = {"Authorization": api_key}
            res = requests.get(url, headers=headers, timeout=5)
            feed.ParseFromString(res.content)
            
        elif agency == "MTA":
            if route_id in ['1', '2', '3', '4', '5', '6', 'S']:
                url = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs"
            elif route_id in ['A', 'C', 'E', 'H', 'FS']:
                url = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace"
            elif route_id in ['N', 'Q', 'R', 'W']:
                 url = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw"
            elif route_id in ['B', 'D', 'F', 'M']:
                 url = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm"
            elif route_id in ['L']:
                 url = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-l"
            elif route_id in ['G']:
                 url = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-g"
            elif route_id in ['J', 'Z']:
                 url = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-jz"
            elif route_id in ['7']:
                 url = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-7"
            else:
                return 0 
                
            res = requests.get(url, timeout=5)
            feed.ParseFromString(res.content)
            
        else:
            return 0 

        for entity in feed.entity:
            if entity.HasField('trip_update') and entity.trip_update.trip.trip_id == str(trip_id):
                if len(entity.trip_update.stop_time_update) > 0:
                    delay_seconds = entity.trip_update.stop_time_update[0].departure.delay
                    delay_minutes = int(delay_seconds / 60)
                    break
                    
    except Exception as e:
        print(f"Live data error for {agency}: {e}")
        
    return delay_minutes

# ==========================================
# 5. Geolocation Helpers
# ==========================================
def get_coordinates(address: str):
    """Converts a street address to (latitude, longitude)."""
    location = geolocator.geocode(f"{address}, New York, NY", timeout=10)
    if location:
        return location.latitude, location.longitude, location.address
    return None, None, None

def find_nearest_stop(conn, user_lat, user_lon):
    """Finds the closest MTA subway stop in the database to the given coordinates."""
    query = "SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops WHERE stop_id LIKE 'MTA_%'"
    stops_df = pd.read_sql(query, conn)
    
    if stops_df.empty:
        return None
        
    def calc_dist(row):
        return geodesic((user_lat, user_lon), (row['stop_lat'], row['stop_lon'])).miles
    
    stops_df['dist_miles'] = stops_df.apply(calc_dist, axis=1)
    nearest = stops_df.sort_values(by='dist_miles').iloc[0]
    
    walk_mins = max(1, round(nearest['dist_miles'] * 20))
    
    return {
        "stop_id": nearest['stop_id'],
        "stop_name": nearest['stop_name'],
        "distance_miles": round(nearest['dist_miles'], 2),
        "walk_minutes": walk_mins
    }

# ==========================================
# 6. App UI & Input
# ==========================================
st.title("LSP $\\leftrightarrow$ NYC Commute")

direction = st.radio("Trip Direction:", ["Going to NYC", "Going Home (to LSP)"], horizontal=True)

col1, col2 = st.columns(2)
with col1:
    target_date = st.date_input("Day of trip", value=datetime.today())
with col2:
    if direction == "Going to NYC":
        target_time = st.time_input("I want to **ARRIVE BY**:", value=datetime.strptime("09:00", "%H:%M").time())
    else:
        target_time = st.time_input("I want to **LEAVE AT**:", value=datetime.strptime("17:00", "%H:%M").time())

address_input = st.text_input("Enter NYC Destination Address:", placeholder="e.g. 350 5th Ave or Empire State Building")

# ==========================================
# 7. Routing Execution
# ==========================================

# Determine which database to load
if os.path.exists("timetable.sqlite"):
    conn = sqlite3.connect("timetable.sqlite")
    mock_mode = False
else:
    conn = build_mock_database()
    st.warning("Using mock timetable data. Upload `timetable.sqlite` for real data.")
    mock_mode = True

if st.button("Calculate Route", type="primary"):
    
    if not address_input.strip():
        st.error("Please enter a destination address.")
        st.stop()
        
    target_dt = datetime.combine(target_date, target_time)

    with st.spinner("Finding nearest station and calculating optimal route..."):
        
        dest_lat, dest_lon, matched_addr = get_coordinates(address_input)
        if not dest_lat:
            st.error("Could not find that address. Try adding more detail.")
            st.stop()
            
        nearest_mta = find_nearest_stop(conn, dest_lat, dest_lon)
        if not nearest_mta:
            st.error("Error finding nearest subway station.")
            st.stop()
            
        mta_id = nearest_mta["stop_id"]
        destination_name = nearest_mta["stop_name"]
        walk_time = nearest_mta["walk_minutes"]

        st.info(f"📍 Target Location: {matched_addr}\n\n" 
                f"🚶 Nearest Station: **{destination_name}** ({nearest_mta['distance_miles']} mi away, ~{walk_time} min walk)")

        if direction == "Going to NYC":
            target_train_arrival = target_dt - timedelta(minutes=walk_time)
            
            mta_leg = get_train_before(conn, STATION_WTC_MTA, mta_id, target_train_arrival)
            if not mta_leg: st.error("No MTA trains found."); st.stop()
                
            path_target = mta_leg["depart"] - timedelta(minutes=BUFFER_WTC_TO_MTA)
            path_leg = get_train_before(conn, STATION_EXCHANGE_PATH, STATION_WTC_PATH, path_target)
            if not path_leg: st.error("No PATH trains found."); st.stop()
                
            hblr_target = path_leg["depart"] - timedelta(minutes=BUFFER_HBLR_TO_PATH)
            hblr_leg = get_train_before(conn, STATION_LSP_HBLR, STATION_EXCHANGE_HBLR, hblr_target)
            if not hblr_leg: st.error("No HBLR trains found."); st.stop()
            
            # Fetch Delays
            hblr_delay = get_live_delay(hblr_leg["trip_id"], "NJT")
            mta_delay = get_live_delay(mta_leg["trip_id"], "MTA", mta_leg["route"])
            
            hblr_actual_depart = hblr_leg["depart"] + timedelta(minutes=hblr_delay)
            mta_actual_depart = mta_leg["depart"] + timedelta(minutes=mta_delay)
            
            st.success(f"To arrive at your destination by {target_dt.strftime('%I:%M %p')}, leave home at **{hblr_actual_depart.strftime('%I:%M %p')}**.")
            
            st.subheader("Step 1: NJ Transit Light Rail")
            c1, c2 = st.columns(2)
            c1.metric("Depart Liberty State Park", hblr_actual_depart.strftime('%I:%M %p'), delta=f"{hblr_delay} min late" if hblr_delay > 0 else "On time", delta_color="inverse")
            c2.metric("Arrive Exchange Place", (hblr_leg["arrive"] + timedelta(minutes=hblr_delay)).strftime('%I:%M %p'))
            st.caption(f"🚶 *Walk {BUFFER_HBLR_TO_PATH} mins to the PATH platforms*")
            
            st.subheader("Step 2: PATH Train")
            c1, c2 = st.columns(2)
            c1.metric("Depart Exchange Place", path_leg["depart"].strftime('%I:%M %p'))
            c2.metric("Arrive WTC Oculus", path_leg["arrive"].strftime('%I:%M %p'))
            st.caption(f"🚶 *Walk {BUFFER_WTC_TO_MTA} mins through Oculus to the Subway*")
            
            st.subheader("Step 3: MTA Subway")
            mta_direction = "Uptown" if mta_leg['trip_id'].endswith('N') else "Downtown"
            st.info(f"🚆 Take the **{mta_direction} {mta_leg['route']} Train** toward **{mta_leg['headsign']}**.")
            
            c1, c2 = st.columns(2)
            c1.metric("Depart WTC / Fulton St", mta_actual_depart.strftime('%I:%M %p'), delta=f"{mta_delay} min late" if mta_delay > 0 else "On time", delta_color="inverse")
            c2.metric(f"Arrive {destination_name}", (mta_leg["arrive"] + timedelta(minutes=mta_delay)).strftime('%I:%M %p'))

        else:
            # direction == "Going Home"
            mta_leg_depart = target_dt + timedelta(minutes=walk_time)
            
            mta_leg = get_train_after(conn, mta_id, STATION_WTC_MTA, mta_leg_depart)
            if not mta_leg: st.error("No MTA trains found."); st.stop()
                
            path_target = mta_leg["arrive"] + timedelta(minutes=BUFFER_WTC_TO_MTA)
            path_leg = get_train_after(conn, STATION_WTC_PATH, STATION_EXCHANGE_PATH, path_target)
            if not path_leg: st.error("No PATH trains found."); st.stop()
                
            hblr_target = path_leg["arrive"] + timedelta(minutes=BUFFER_HBLR_TO_PATH)
            hblr_leg = get_train_after(conn, STATION_EXCHANGE_HBLR, STATION_LSP_HBLR, hblr_target)
            if not hblr_leg: st.error("No HBLR trains found."); st.stop()
            
            # Fetch Delays
            mta_delay = get_live_delay(mta_leg["trip_id"], "MTA", mta_leg["route"])
            hblr_delay = get_live_delay(hblr_leg["trip_id"], "NJT")
            
            mta_actual_depart = mta_leg["depart"] + timedelta(minutes=mta_delay)
            hblr_actual_depart = hblr_leg["depart"] + timedelta(minutes=hblr_delay)
            final_arrival = hblr_leg["arrive"] + timedelta(minutes=hblr_delay)
            
            st.success(f"If you leave your location at {target_dt.strftime('%I:%M %p')}, you will be back at Liberty State Park by **{final_arrival.strftime('%I:%M %p')}**.")
            
            st.subheader("Step 1: MTA Subway")
            mta_direction = "Downtown" if mta_leg['trip_id'].endswith('S') else "Uptown"
            st.info(f"🚆 Take the **{mta_direction} {mta_leg['route']} Train** toward **{mta_leg['headsign']}**.")
            
            c1, c2 = st.columns(2)
            c1.metric(f"Depart {destination_name}", mta_actual_depart.strftime('%I:%M %p'), delta=f"{mta_delay} min late" if mta_delay > 0 else "On time", delta_color="inverse")
            c2.metric("Arrive WTC / Fulton St", (mta_leg["arrive"] + timedelta(minutes=mta_delay)).strftime('%I:%M %p'))
            st.caption(f"🚶 *Walk {BUFFER_WTC_TO_MTA} mins through Oculus to PATH platforms*")
            
            st.subheader("Step 2: PATH Train")
            c1, c2 = st.columns(2)
            c1.metric("Depart WTC Oculus", path_leg["depart"].strftime('%I:%M %p'))
            c2.metric("Arrive Exchange Place", path_leg["arrive"].strftime('%I:%M %p'))
            st.caption(f"🚶 *Walk {BUFFER_HBLR_TO_PATH} mins up to the Light Rail tracks*")
            
            st.subheader("Step 3: NJ Transit Light Rail")
            c1, c2 = st.columns(2)
            c1.metric("Depart Exchange Place", hblr_actual_depart.strftime('%I:%M %p'), delta=f"{hblr_delay} min late" if hblr_delay > 0 else "On time", delta_color="inverse")
            c2.metric("Arrive Liberty State Park", final_arrival.strftime('%I:%M %p'))

# ==========================================
# 8. Database Debugger (Find your IDs)
# ==========================================
# Only show this table if we are using the real database
if not mock_mode:
    st.divider()
    st.subheader("🛠 Database Debugger")
    st.markdown("Find the exact `stop_id` for your stations in this table, and copy them into the **Configuration** section at the very top of `app.py`.")
    debug_query = """
    SELECT stop_id, stop_name FROM stops 
    WHERE stop_name LIKE '%Exchange Place%' 
       OR stop_name LIKE '%World Trade Center%' 
       OR stop_name LIKE '%Liberty State Park%'
       OR stop_name LIKE '%Fulton%'
    """
    st.dataframe(pd.read_sql(debug_query, conn))
