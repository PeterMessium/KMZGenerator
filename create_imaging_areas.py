# sso_kmz_streamlit.py
import streamlit as st
import simplekml
from shapely.geometry import Polygon
from shapely.affinity import rotate, translate
from pyproj import Transformer
import math

st.title("SSO KMZ Generator")

st.markdown("""
Enter centroids (latitude, longitude) separated by commas, one per line.
""")
centroids_input = st.text_area("Centroids (lat, lon)", "51.501758,-0.136343\n48.856613,2.352222")

length_km = st.number_input("Length along satellite track (km)", min_value=1.0, value=70.0)
width_km = st.number_input("Width perpendicular to track (km)", min_value=1.0, value=20.0)
direction = st.selectbox("Orbit direction", ["NE->SW", "SE->NW"])

# Function to calculate dynamic ground track angle
def sso_ground_track_angle(lat_deg, inclination_deg=97.8, direction="NE->SW"):
    """
    Approximate Sun-synchronous satellite ground track angle (degrees) at a given latitude.
    """
    # Clamp latitude to physically reachable values
    if abs(lat_deg) > inclination_deg:
        lat_deg = math.copysign(inclination_deg, lat_deg)
    
    lat_rad = math.radians(lat_deg)
    inc_rad = math.radians(inclination_deg)
    
    ratio = math.cos(inc_rad) / math.cos(lat_rad)
    # Clamp ratio to [-1, 1] to avoid math domain errors at high latitudes
    ratio = max(-1.0, min(1.0, ratio))
    
    angle_rad = math.asin(ratio)
    angle_deg = math.degrees(angle_rad)
    
    # Flip sign depending on orbit direction
    if direction == "NE->SW":
        return angle_deg
    else:
        return -angle_deg

if st.button("Generate KMZ"):
    try:
        # Parse centroids
        centroids = []
        for line in centroids_input.splitlines():
            lat_str, lon_str = line.strip().split(",")
            centroids.append((float(lat_str), float(lon_str)))

        kml = simplekml.Kml()

        for lat, lon in centroids:
            # Compute orbit angle dynamically
            orbit_angle = sso_ground_track_angle(lat, inclination_deg=97.8, direction=direction)

            # UTM transformation
            utm_zone = int((lon + 180) / 6) + 1
            is_northern = lat >= 0

            transformer_to_utm = Transformer.from_crs(
                f"EPSG:4326", f"EPSG:{32600 + utm_zone if is_northern else 32700 + utm_zone}", always_xy=True
            )
            transformer_to_latlon = Transformer.from_crs(
                f"EPSG:{32600 + utm_zone if is_northern else 32700 + utm_zone}", "EPSG:4326", always_xy=True
            )

            x, y = transformer_to_utm.transform(lon, lat)

            # Build rectangle polygon
            dx = width_km * 1000 / 2
            dy = length_km * 1000 / 2
            rect = Polygon([(-dx, -dy), (-dx, dy), (dx, dy), (dx, -dy)])
            rect_rot = rotate(rect, orbit_angle, origin=(0, 0), use_radians=False)
            rect_rot_trans = translate(rect_rot, xoff=x, yoff=y)
            coords_latlon = [transformer_to_latlon.transform(px, py) for px, py in rect_rot_trans.exterior.coords]

            # Add to KML
            poly = kml.newpolygon(
                name=f"{direction} @ {lat:.4f},{lon:.4f}",
                outerboundaryis=coords_latlon
            )
            poly.style.polystyle.color = simplekml.Color.changealphaint(100, simplekml.Color.green)

        kmz_path = "sso_polygons_dynamic.kmz"
        kml.savekmz(kmz_path)

        # Allow download
        with open(kmz_path, "rb") as f:
            st.download_button("Download KMZ", f, file_name=kmz_path)

        st.success("KMZ generated successfully!")

    except Exception as e:
        st.error(f"Error: {e}")

