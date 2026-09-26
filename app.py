import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import requests
from google.transit import gtfs_realtime_pb2
import os

# ==========================================
# 1. Configuration & Secrets
# ==========================================
st.set_page_config(page_title="NJT/PATH Commute", page_icon="🚆", layout="centered")

# Transfer Buffers (in minutes)
BUFFER_WTC_TO_MTA = 6
BUFFER_HBLR_TO_PATH = 4

# API Keys (Set these in Streamlit Cloud -> Settings -> Secrets)
# Example: NJT_API_KEY = "your_key_here"
NJT_API_KEY = st.secrets.get("NJT_API_KEY", "")

# ==========================================
# 2. Database Helper (Reverse Routing)
# ==========================================
def get_train_before(conn, origin_id, dest_id, arrive_by_time):
    """
    Finds the latest train that leaves origin_id and arrives at dest_id 
    BEFORE the arrive_by_time.
    """
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
    cur.execute(query, (origin_id, dest_id, arrive_by_time.strftime("%H:%M:%S")))
    result = cur.fetchone()
    
    if result:
        dep_str, arr_str, trip_id, route_id = result
        # Convert string times back to datetime objects for math
        today = datetime.now().date()
        dep_dt = datetime.strptime(dep_str, "%H:%M:%S").replace(year=today.year, month=today.month, day=today.day)
        arr_dt = datetime.strptime(arr_str, "%H:%M:%S").replace(year=today.year, month=today.month, day=today.day)
        return {"depart": dep_dt, "arrive": arr_dt, "trip_id": trip_id, "route": route_id}
    return None

# ==========================================
# 3. Live Delays (GTFS-RT)
# ==========================================
def get_live_delay(trip_id, agency):
    """
    Fetches live GTFS-RT delays. Returns delay in minutes.
    """
    if agency == "PATH" or not NJT_API_KEY:
        # PATH GTFS-RT is notoriously tricky, or API key is missing. 
        # Return 0 for the demo.
        return 0 
        
    try:
        url = "https://api.njtransit.com/gtfs/tripupdates"
        headers = {"Authorization": NJT_API_KEY}
        res = requests.get(url, headers=headers, timeout=5)
        
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(res.content)
        
        for entity in feed.entity:
            if entity.HasField('trip_update') and entity.trip_update.trip.trip_id == trip_id:
                # Find the delay for the first stop in the update
                delay_seconds = entity.trip_update.stop_time_update[0].departure.delay
                return int(delay_seconds / 60)
    except Exception as e:
        st.error(f"Live data error: {e}")
    return 0

# ==========================================
# 4. App UI & Logic
# ==========================================
st.title("Commute: LSP $\\rightarrow$ NYC")
st.markdown("Calculate the exact time you need to leave Liberty State Park.")

# User Inputs
col1, col2 = st.columns(2)
with col1:
    target_time = st.time_input("I need to arrive by:", value=datetime.strptime("09:00", "%H:%M").time())
with col2:
    destination = st.selectbox("MTA Destination:", ["Times Sq (1/2/3)", "Union Sq (4/5/6)", "Fulton St (A/C)"])

if st.button("Calculate Route"):
    # Target Arrival Datetime
    target_dt = datetime.combine(datetime.now().date(), target_time)
    
    # Connect to DB (Use real SQLite file if uploaded, otherwise build a mock one)
    if os.path.exists("timetable.sqlite"):
        conn = sqlite3.connect("timetable.sqlite")
    else:
        conn = build_mock_database()
        st.info("Using mock timetable data. Upload `timetable.sqlite` to use real GTFS data.")

    with st.spinner("Calculating Arrive-By Route..."):
        # Step 1: MTA Subway (Final Leg)
        mta_leg = get_train_before(conn, "WTC_MTA", destination, target_dt)
        if not mta_leg:
            st.error("No MTA trains found matching that time.")
            st.stop()
            
        # Step 2: WTC Transfer Penalty
        path_target = mta_leg["depart"] - timedelta(minutes=BUFFER_WTC_TO_MTA)
        
        # Step 3: PATH (Middle Leg)
        path_leg = get_train_before(conn, "EXCHANGE_PATH", "WTC_PATH", path_target)
        if not path_leg:
            st.error("No PATH trains found matching that time.")
            st.stop()
            
        # Step 4: Exchange Place Transfer Penalty
        hblr_target = path_leg["depart"] - timedelta(minutes=BUFFER_HBLR_TO_PATH)
        
        # Step 5: HBLR (First Leg)
        hblr_leg = get_train_before(conn, "LSP_HBLR", "EXCHANGE_HBLR", hblr_target)
        
        # Step 6: Check Live Delays
        hblr_delay = get_live_delay(hblr_leg["trip_id"], "NJT")
        hblr_actual_depart = hblr_leg["depart"] + timedelta(minutes=hblr_delay)

    # --- Render the Itinerary ---
    st.success(f"Leave Liberty State Park at **{hblr_actual_depart.strftime('%I:%M %p')}**")
    
    # HBLR Leg
    st.subheader("1. NJ Transit Light Rail")
    c1, c2, c3 = st.columns(3)
    c1.metric("Depart LSP", hblr_actual_depart.strftime('%I:%M %p'), f"{hblr_delay} min delay" if hblr_delay > 0 else "On Time", delta_color="inverse")
    c2.metric("Arrive Exch. Pl", (hblr_leg["arrive"] + timedelta(minutes=hblr_delay)).strftime('%I:%M %p'))
    c3.metric("Train ID", hblr_leg["trip_id"])
    
    st.caption(f"🚶 *Walk {BUFFER_HBLR_TO_PATH} mins down to PATH platforms*")
    st.divider()
    
    # PATH Leg
    st.subheader("2. PATH Train")
    c1, c2, c3 = st.columns(3)
    c1.metric("Depart Exch. Pl", path_leg["depart"].strftime('%I:%M %p'))
    c2.metric("Arrive WTC", path_leg["arrive"].strftime('%I:%M %p'))
    c3.metric("Direction", path_leg["route"])
    
    st.caption(f"🚶 *Walk {BUFFER_WTC_TO_MTA} mins through Oculus to Subway*")
    st.divider()
    
    # MTA Leg
    st.subheader("3. MTA Subway")
    c1, c2, c3 = st.columns(3)
    c1.metric("Depart WTC/Fulton", mta_leg["depart"].strftime('%I:%M %p'))
    c2.metric(f"Arrive {destination}", mta_leg["arrive"].strftime('%I:%M %p'))
    c3.metric("Line", mta_leg["route"])

# ==========================================
# 5. Mock DB Generator (Runs if no SQLite file found)
# ==========================================
def build_mock_database():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stop_times (trip_id TEXT, route_id TEXT, stop_id TEXT, stop_sequence INT, arrival_time TEXT, departure_time TEXT)")
    
    mock_data = [
        # MTA: WTC to Times Sq
        ('MTA_1', '1 Line', 'WTC_MTA', 1, '08:40:00', '08:40:00'),
        ('MTA_1', '1 Line', 'Times Sq (1/2/3)', 5, '08:56:00', '08:56:00'),
        ('MTA_2', '1 Line', 'WTC_MTA', 1, '08:50:00', '08:50:00'),
        ('MTA_2', '1 Line', 'Times Sq (1/2/3)', 5, '09:05:00', '09:05:00'),
        
        # PATH: Exchange Place to WTC
        ('PATH_1', 'NWK-WTC', 'EXCHANGE_PATH', 1, '08:26:00', '08:27:00'),
        ('PATH_1', 'NWK-WTC', 'WTC_PATH', 2, '08:31:00', '08:31:00'),
        ('PATH_2', 'NWK-WTC', 'EXCHANGE_PATH', 1, '08:38:00', '08:39:00'),
        ('PATH_2', 'NWK-WTC', 'WTC_PATH', 2, '08:43:00', '08:43:00'),
        
        # HBLR: LSP to Exchange Place
        ('HBLR_1', '8th St-Hoboken', 'LSP_HBLR', 1, '08:12:00', '08:12:00'),
        ('HBLR_1', '8th St-Hoboken', 'EXCHANGE_HBLR', 4, '08:20:00', '08:20:00'),
        ('HBLR_2', '8th St-Hoboken', 'LSP_HBLR', 1, '08:24:00', '08:24:00'),
        ('HBLR_2', '8th St-Hoboken', 'EXCHANGE_HBLR', 4, '08:32:00', '08:32:00'),
    ]
    conn.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, ?)", mock_data)
    conn.commit()
    return conn
