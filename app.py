import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import os

# ==========================================
# 1. Configuration & Constants
# ==========================================
st.set_page_config(page_title="Commute Router", page_icon="🚆", layout="centered")

BUFFER_WTC_TO_MTA = 6
BUFFER_HBLR_TO_PATH = 4

MTA_STOPS = {
    "Times Sq (1/2/3)": "MTA_TIMES_SQ",
    "Union Sq (4/5/6)": "MTA_UNION_SQ",
    "Fulton St (A/C)": "MTA_FULTON"
}

# ==========================================
# 2. Database Routing Functions
# ==========================================
def parse_db_time(time_str, target_date):
    """Safely converts GTFS HH:MM:SS string to a datetime object for the selected day."""
    # GTFS times can exceed 24:00:00 for late-night trains, so we handle standard formats first.
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

# ==========================================
# 3. App UI & Input
# ==========================================
st.title("LSP $\\leftrightarrow$ NYC Commute")

# 1. Choose Direction
direction = st.radio("Trip Direction:", ["Going to NYC", "Going Home (to LSP)"], horizontal=True)

# 2. Choose Day and Destination
col1, col2 = st.columns(2)
with col1:
    target_date = st.date_input("Day of trip", value=datetime.today())
with col2:
    destination_name = st.selectbox("MTA Station:", list(MTA_STOPS.keys()))
    mta_id = MTA_STOPS[destination_name]

# 3. Dynamic Time Input based on Direction
if direction == "Going to NYC":
    target_time = st.time_input("I want to **ARRIVE BY**:", value=datetime.strptime("09:00", "%H:%M").time())
else:
    target_time = st.time_input("I want to **LEAVE AT**:", value=datetime.strptime("17:00", "%H:%M").time())

# ==========================================
# 4. Routing Logic
# ==========================================
if st.button("Calculate Route", type="primary"):
    target_dt = datetime.combine(target_date, target_time)
    
    # Load Real DB or Mock DB
    if os.path.exists("timetable.sqlite"):
        conn = sqlite3.connect("timetable.sqlite")
    else:
        conn = build_mock_database()
        st.warning("Using mock timetable data (Trains run every 15 mins). Upload `timetable.sqlite` for real data.")

    with st.spinner("Calculating optimal route..."):
        
        if direction == "Going to NYC":
            # REVERSE ROUTING (Working backward from Arrival Time)
            mta_leg = get_train_before(conn, "WTC_MTA", mta_id, target_dt)
            if not mta_leg: st.error("No MTA trains found."); st.stop()
                
            path_target = mta_leg["depart"] - timedelta(minutes=BUFFER_WTC_TO_MTA)
            path_leg = get_train_before(conn, "EXCHANGE_PATH", "WTC_PATH", path_target)
            if not path_leg: st.error("No PATH trains found."); st.stop()
                
            hblr_target = path_leg["depart"] - timedelta(minutes=BUFFER_HBLR_TO_PATH)
            hblr_leg = get_train_before(conn, "LSP_HBLR", "EXCHANGE_HBLR", hblr_target)
            if not hblr_leg: st.error("No HBLR trains found."); st.stop()
            
            # Render Going to NYC
            st.success(f"To arrive at {destination_name} by {target_dt.strftime('%I:%M %p')}, leave home at **{hblr_leg['depart'].strftime('%I:%M %p')}**.")
            
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
            # FORWARD ROUTING (Working forward from Departure Time)
            mta_leg = get_train_after(conn, mta_id, "WTC_MTA", target_dt)
            if not mta_leg: st.error("No MTA trains found."); st.stop()
                
            path_target = mta_leg["arrive"] + timedelta(minutes=BUFFER_WTC_TO_MTA)
            path_leg = get_train_after(conn, "WTC_PATH", "EXCHANGE_PATH", path_target)
            if not path_leg: st.error("No PATH trains found."); st.stop()
                
            hblr_target = path_leg["arrive"] + timedelta(minutes=BUFFER_HBLR_TO_PATH)
            hblr_leg = get_train_after(conn, "EXCHANGE_HBLR", "LSP_HBLR", hblr_target)
            if not hblr_leg: st.error("No HBLR trains found."); st.stop()
            
            # Render Going Home
            final_arrival = hblr_leg["arrive"]
            st.success(f"If you leave {destination_name} at {target_dt.strftime('%I:%M %p')}, you will be back at Liberty State Park by **{final_arrival.strftime('%I:%M %p')}**.")
            
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
# 5. Dynamic Mock DB Generator
# ==========================================
def build_mock_database():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stop_times (trip_id TEXT, route_id TEXT, stop_id TEXT, stop_sequence INT, arrival_time TEXT, departure_time TEXT)")
    
    rows = []
    for hour in range(0, 24):
        for m in [0, 15, 30, 45]:
            dep = f"{hour:02d}:{m:02d}:00"
            
            # MTA (12 min trip)
            m_arr = m + 12
            arr_mta = f"{hour:02d}:{m_arr:02d}:00" if m_arr < 60 else f"{hour+1:02d}:{m_arr%60:02d}:00"
            for mta_stop in MTA_STOPS.values():
                rows.extend([
                    (f'MTA_F_{hour}_{m}', '1/2/3 Line', 'WTC_MTA', 1, dep, dep),
                    (f'MTA_F_{hour}_{m}', '1/2/3 Line', mta_stop, 2, arr_mta, arr_mta),
                    (f'MTA_R_{hour}_{m}', '1/2/3 Line', mta_stop, 1, dep, dep),
                    (f'MTA_R_{hour}_{m}', '1/2/3 Line', 'WTC_MTA', 2, arr_mta, arr_mta),
                ])
            
            # PATH (5 min trip)
            p_arr = m + 5
            arr_path = f"{hour:02d}:{p_arr:02d}:00" if p_arr < 60 else f"{hour+1:02d}:{p_arr%60:02d}:00"
            rows.extend([
                (f'PATH_F_{hour}_{m}', 'NWK-WTC', 'EXCHANGE_PATH', 1, dep, dep),
                (f'PATH_F_{hour}_{m}', 'NWK-WTC', 'WTC_PATH', 2, arr_path, arr_path),
                (f'PATH_R_{hour}_{m}', 'WTC-NWK', 'WTC_PATH', 1, dep, dep),
                (f'PATH_R_{hour}_{m}', 'WTC-NWK', 'EXCHANGE_PATH', 2, arr_path, arr_path)
            ])
            
            # HBLR (8 min trip)
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
