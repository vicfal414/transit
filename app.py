import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import os
from geopy.geocoders import Nominatim
from geopy.distance import geodesic

# ==========================================
# 1. Configuration & Constants
# ==========================================
st.set_page_config(page_title="Commute Router", page_icon="🚆", layout="centered")

BUFFER_WTC_TO_MTA = 6
BUFFER_HBLR_TO_PATH = 4

# Initialize Geocoder for Address Lookups
geolocator = Nominatim(user_agent="my_personal_commute_app_v1")

# ==========================================
# 2. Database Routing Functions
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
        SELECT t1.departure_time, t2.arrival_time, t1.trip_id, t1.route_id
        FROM stop_times t1
        JOIN stop_times t2 ON t1.trip_id = t2.trip_id
        WHERE t1.stop_id = ? 
          AND t2.stop_id = ?
          AND t1.stop_sequence < t2.stop_sequence
          AND t2.arrival_time <= ?
        ORDER BY t2.arrival_time DESC
        LIMIT 1;
    """
    cur = conn.cursor()
    target_time_str = arrive_by_dt.strftime("%H:%M:%S")
    cur.execute(query, (origin_id, dest_id, target_time_str))
    result = cur.fetchone()
    
    if result:
        dep_str, arr_str, trip_id, route_id = result
        target_date = arrive_by_dt.date()
        return {
            "depart": parse_db_time(dep_str, target_date),
            "arrive": parse_db_time(arr_str, target_date),
            "trip_id": trip_id, "route": route_id
        }
    return None

def get_train_after(conn, origin_id, dest_id, depart_after_dt):
    """FORWARD ROUTING: Finds the earliest train departing AFTER the target time."""
    query = """
        SELECT t1.departure_time, t2.arrival_time, t1.trip_id, t1.route_id
        FROM stop_times t1
        JOIN stop_times t2 ON t1.trip_id = t2.trip_id
        WHERE t1.stop_id = ? 
          AND t2.stop_id = ?
          AND t1.stop_sequence < t2.stop_sequence
          AND t1.departure_time >= ?
        ORDER BY t1.departure_time ASC
        LIMIT 1;
    """
    cur = conn.cursor()
    target_time_str = depart_after_dt.strftime("%H:%M:%S")
    cur.execute(query, (origin_id, dest_id, target_time_str))
    result = cur.fetchone()
    
    if result:
        dep_str, arr_str, trip_id, route_id = result
        target_date = depart_after_dt.date()
        return {
            "depart": parse_db_time(dep_str, target_date),
            "arrive": parse_db_time(arr_str, target_date),
            "trip_id": trip_id, "route": route_id
        }
    return None

def get_live_delay(trip_id, agency, route_id=None):
    """
    Fetches live GTFS-RT delays for a specific trip. Returns delay in minutes.
    """
    delay_minutes = 0
    
    try:
        feed = gtfs_realtime_pb2.FeedMessage()
        
        if agency == "NJT":
            api_key = st.secrets.get("NJT_API_KEY", "")
            if not api_key or api_key == "your_future_njt_key_here":
                return 0 # Fail gracefully if key isn't set yet
                
            url = "https://api.njtransit.com/gtfs/tripupdates"
            headers = {"Authorization": api_key}
            res = requests.get(url, headers=headers, timeout=5)
            feed.ParseFromString(res.content)
            
        elif agency == "MTA":
            # No API key needed for MTA feeds anymore!
            
            # MTA splits feeds by line group. 
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
                return 0 # Fallback for unknown lines
                
            # Make the request without any headers
            res = requests.get(url, timeout=5)
            feed.ParseFromString(res.content)
            
        else:
            # PATH real-time is often unreliable or requires third-party aggregators
            return 0 

        # Loop through the live entities to find our specific train
        for entity in feed.entity:
            if entity.HasField('trip_update') and entity.trip_update.trip.trip_id == str(trip_id):
                # Get the delay of the first upcoming stop in the update
                if len(entity.trip_update.stop_time_update) > 0:
                    delay_seconds = entity.trip_update.stop_time_update[0].departure.delay
                    
                    # Some agencies omit delay but provide a new timestamp
                    if delay_seconds == 0 and entity.trip_update.stop_time_update[0].departure.time > 0:
                       # Advanced handling: compare live timestamp to scheduled timestamp
                       pass
                    
                    delay_minutes = int(delay_seconds / 60)
                    break
                    
    except Exception as e:
        # Silently fail and return 0 delay so the app doesn't crash if an API goes down
        print(f"Live data error for {agency}: {e}")
        
    return delay_minutes

# ==========================================
# 3. Geolocation Helpers
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
    
    # Estimate walk time (20 mins per mile ~ 3 mph)
    walk_mins = max(1, round(nearest['dist_miles'] * 20))
    
    return {
        "stop_id": nearest['stop_id'],
        "stop_name": nearest['stop_name'],
        "distance_miles": round(nearest['dist_miles'], 2),
        "walk_minutes": walk_mins
    }

# ==========================================
# 4. App UI & Input
# ==========================================
st.title("LSP $\\leftrightarrow$ NYC Commute")

# 1. Choose Direction
direction = st.radio("Trip Direction:", ["Going to NYC", "Going Home (to LSP)"], horizontal=True)

# 2. Choose Day and Destination Address
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
# 5. Routing Execution
# ==========================================
if st.button("Calculate Route", type="primary"):
    
    if not address_input.strip():
        st.error("Please enter a destination address.")
        st.stop()
        
    target_dt = datetime.combine(target_date, target_time)
    
    # Load DB
    if os.path.exists("timetable.sqlite"):
        conn = sqlite3.connect("timetable.sqlite")
    else:
        conn = build_mock_database()
        st.warning("Using mock timetable data. Upload `timetable.sqlite` for real data.")

    with st.spinner("Finding nearest station and calculating optimal route..."):
        
        # Geocode the address
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

        # Routing Math
        if direction == "Going to NYC":
            # Target Arrival at final building
            target_train_arrival = target_dt - timedelta(minutes=walk_time)
            
            mta_leg = get_train_before(conn, "WTC_MTA", mta_id, target_train_arrival)
            if not mta_leg: st.error("No MTA trains found."); st.stop()
                
            path_target = mta_leg["depart"] - timedelta(minutes=BUFFER_WTC_TO_MTA)
            path_leg = get_train_before(conn, "EXCHANGE_PATH", "WTC_PATH", path_target)
            if not path_leg: st.error("No PATH trains found."); st.stop()
                
            hblr_target = path_leg["depart"] - timedelta(minutes=BUFFER_HBLR_TO_PATH)
            hblr_leg = get_train_before(conn, "LSP_HBLR", "EXCHANGE_HBLR", hblr_target)
            if not hblr_leg: st.error("No HBLR trains found."); st.stop()
            
            # Render Going to NYC
            st.success(f"To arrive at your destination by {target_dt.strftime('%I:%M %p')}, leave home at **{hblr_leg['depart'].strftime('%I:%M %p')}**.")
            
            st.subheader("Step 1: NJ Transit Light Rail")
            c1, c2 = st.columns(2)
            c1.metric("Depart Liberty State Park", hblr_leg["depart"].strftime('%I:%M %p'))
            c2.metric("Arrive Exchange Place", hblr_leg["arrive"].strftime('%I:%M %p'))
            st.caption(f"🚶 *Walk {BUFFER_HBLR_TO_PATH} mins to the PATH platforms*")
            
            st.subheader("Step 2: PATH Train")
            c1, c2 = st.columns(2)
            c1.metric("Depart Exchange Place", path_leg["depart"].strftime('%I:%M %p'))
            c2.metric("Arrive WTC Oculus", path_leg["arrive"].strftime('%I:%M %p'))
            st.caption(f"🚶 *Walk {BUFFER_WTC_TO_MTA} mins through Oculus to the Subway*")
            
            st.subheader("Step 3: MTA Subway")
            st.info(f"🚆 Take the **{mta_leg['route']}** from the WTC/Fulton St complex.")
            c1, c2 = st.columns(2)
            c1.metric("Depart WTC / Fulton St", mta_leg["depart"].strftime('%I:%M %p'))
            c2.metric(f"Arrive {destination_name}", mta_leg["arrive"].strftime('%I:%M %p'))

        else:
            # direction == "Going Home (to LSP)"
            mta_leg_depart = target_dt + timedelta(minutes=walk_time)
            
            mta_leg = get_train_after(conn, mta_id, "WTC_MTA", mta_leg_depart)
            if not mta_leg: st.error("No MTA trains found."); st.stop()
                
            path_target = mta_leg["arrive"] + timedelta(minutes=BUFFER_WTC_TO_MTA)
            path_leg = get_train_after(conn, "WTC_PATH", "EXCHANGE_PATH", path_target)
            if not path_leg: st.error("No PATH trains found."); st.stop()
                
            hblr_target = path_leg["arrive"] + timedelta(minutes=BUFFER_HBLR_TO_PATH)
            hblr_leg = get_train_after(conn, "EXCHANGE_HBLR", "LSP_HBLR", hblr_target)
            if not hblr_leg: st.error("No HBLR trains found."); st.stop()
            
            # Render Going Home
            final_arrival = hblr_leg["arrive"]
            st.success(f"If you leave your location at {target_dt.strftime('%I:%M %p')}, you will be back at Liberty State Park by **{final_arrival.strftime('%I:%M %p')}**.")
            
            st.subheader("Step 1: MTA Subway")
            st.info(f"🚆 Take the **{mta_leg['route']}** going Downtown toward WTC/Fulton St.")
            c1, c2 = st.columns(2)
            c1.metric(f"Depart {destination_name}", mta_leg["depart"].strftime('%I:%M %p'))
            c2.metric("Arrive WTC / Fulton St", mta_leg["arrive"].strftime('%I:%M %p'))
            st.caption(f"🚶 *Walk {BUFFER_WTC_TO_MTA} mins through Oculus to PATH platforms*")
            
            st.subheader("Step 2: PATH Train")
            c1, c2 = st.columns(2)
            c1.metric("Depart WTC Oculus", path_leg["depart"].strftime('%I:%M %p'))
            c2.metric("Arrive Exchange Place", path_leg["arrive"].strftime('%I:%M %p'))
            st.caption(f"🚶 *Walk {BUFFER_HBLR_TO_PATH} mins up to the Light Rail tracks*")
            
            st.subheader("Step 3: NJ Transit Light Rail")
            c1, c2 = st.columns(2)
            c1.metric("Depart Exchange Place", hblr_leg["depart"].strftime('%I:%M %p'))
            c2.metric("Arrive Liberty State Park", hblr_leg["arrive"].strftime('%I:%M %p'))

# ==========================================
# 6. Dynamic Mock DB Generator
# ==========================================
def build_mock_database():
    """Generates mock schedules and coordinates for testing."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stop_times (trip_id TEXT, route_id TEXT, stop_id TEXT, stop_sequence INT, arrival_time TEXT, departure_time TEXT)")
    conn.execute("CREATE TABLE stops (stop_id TEXT, stop_name TEXT, stop_lat REAL, stop_lon REAL)")
    
    # Mock Stops Data (Includes real coords for midtown for testing)
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
    rows = []
    for hour in range(0, 24):
        for m in [0, 15, 30, 45]:
            dep = f"{hour:02d}:{m:02d}:00"
            
            # MTA
            m_arr = m + 12
            arr_mta = f"{hour:02d}:{m_arr:02d}:00" if m_arr < 60 else f"{hour+1:02d}:{m_arr%60:02d}:00"
            for mta_stop in mta_destinations:
                rows.extend([
                    (f'MTA_F_{hour}_{m}_{mta_stop}', 'A/C Line', 'WTC_MTA', 1, dep, dep),
                    (f'MTA_F_{hour}_{m}_{mta_stop}', 'A/C Line', mta_stop, 2, arr_mta, arr_mta),
                    (f'MTA_R_{hour}_{m}_{mta_stop}', 'A/C Line', mta_stop, 1, dep, dep),
                    (f'MTA_R_{hour}_{m}_{mta_stop}', 'A/C Line', 'WTC_MTA', 2, arr_mta, arr_mta),
                ])
            
            # PATH 
            p_arr = m + 5
            arr_path = f"{hour:02d}:{p_arr:02d}:00" if p_arr < 60 else f"{hour+1:02d}:{p_arr%60:02d}:00"
            rows.extend([
                (f'PATH_F_{hour}_{m}', 'NWK-WTC', 'EXCHANGE_PATH', 1, dep, dep),
                (f'PATH_F_{hour}_{m}', 'NWK-WTC', 'WTC_PATH', 2, arr_path, arr_path),
                (f'PATH_R_{hour}_{m}', 'WTC-NWK', 'WTC_PATH', 1, dep, dep),
                (f'PATH_R_{hour}_{m}', 'WTC-NWK', 'EXCHANGE_PATH', 2, arr_path, arr_path)
            ])
            
            # HBLR 
            h_arr = m + 8
            arr_hblr = f"{hour:02d}:{h_arr:02d}:00" if h_arr < 60 else f"{hour+1:02d}:{h_arr%60:02d}:00"
            rows.extend([
                (f'HBLR_F_{hour}_{m}', '8th St-Hoboken', 'LSP_HBLR', 1, dep, dep),
                (f'HBLR_F_{hour}_{m}', '8th St-Hoboken', 'EXCHANGE_HBLR', 2, arr_hblr, arr_hblr),
                (f'HBLR_R_{hour}_{m}', 'Hoboken-8th St', 'EXCHANGE_HBLR', 1, dep, dep),
                (f'HBLR_R_{hour}_{m}', 'Hoboken-8th St', 'LSP_HBLR', 2, arr_hblr, arr_hblr)
            ])
            
    conn.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    return conn
