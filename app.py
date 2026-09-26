from geopy.geocoders import Nominatim
from geopy.distance import geodesic

# Initialize free geocoder with a custom user_agent string
geolocator = Nominatim(user_agent="my_commute_app_personal")

def get_coordinates(address: str):
    """Converts a street address to (latitude, longitude)."""
    # Restrict search context to NY/NJ for better accuracy
    location = geolocator.geocode(f"{address}, New York, NY", timeout=10)
    if location:
        return location.latitude, location.longitude, location.address
    return None, None, None

def find_nearest_stop(conn, user_lat, user_lon, max_stops=1):
    """
    Finds the closest MTA subway stop in the database to the given coordinates.
    Assumes average walking speed of 3 mph (~80 meters/min).
    """
    # Query all candidate MTA stops that have coordinates
    query = """
        SELECT stop_id, stop_name, stop_lat, stop_lon 
        FROM stops 
        WHERE stop_id LIKE 'MTA_%' OR stop_id LIKE '1%' OR stop_id LIKE 'R%'
    """
    stops_df = pd.read_sql(query, conn)
    
    # Calculate distance from the user's destination to every stop
    def calc_dist(row):
        return geodesic((user_lat, user_lon), (row['stop_lat'], row['stop_lon'])).miles
    
    stops_df['dist_miles'] = stops_df.apply(calc_dist, axis=1)
    nearest = stops_df.sort_values(by='dist_miles').iloc[0]
    
    # 3 mph = 20 mins per mile
    walk_mins = max(1, round(nearest['dist_miles'] * 20))
    
    return {
        "stop_id": nearest['stop_id'],
        "stop_name": nearest['stop_name'],
        "distance_miles": round(nearest['dist_miles'], 2),
        "walk_minutes": walk_mins
    }
