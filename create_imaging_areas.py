# sso_kmz_streamlit.py
import streamlit as st
import simplekml
from shapely.geometry import Polygon
from shapely.affinity import rotate, translate
from pyproj import Transformer

st.title("SSO KMZ Generator")

st.markdown("""
Enter centroids (latitude, longitude) separated by commas, one per line.
""")
centroids_input = st.text_area("Centroids (lat, lon)", "54.9783, -1.6178\n51.5074, -0.1278")

length_km = st.number_input("Length along satellite track (km)", min_value=1.0, value=30.0)
width_km = st.number_input("Width perpendicular to track (km)", min_value=1.0, value=20.0)
direction = st.selectbox("Orbit direction", ["NE->SW", "SE->NW"])

if st.button("Generate KMZ"):
    try:
        # Parse centroids
        centroids = []
        for line in centroids_input.splitlines():
            lat_str, lon_str = line.strip().split(",")
            centroids.append((float(lat_str), float(lon_str)))

        # Orbit angle
        orbit_angle = -7 if direction == "NE->SW" else 7

        kml = simplekml.Kml()

        for lat, lon in centroids:
            utm_zone = int((lon + 180) / 6) + 1
            is_northern = lat >= 0

            transformer_to_utm = Transformer.from_crs(
                f"EPSG:4326", f"EPSG:{32600 + utm_zone if is_northern else 32700 + utm_zone}", always_xy=True
            )
            transformer_to_latlon = Transformer.from_crs(
                f"EPSG:{32600 + utm_zone if is_northern else 32700 + utm_zone}", "EPSG:4326", always_xy=True
            )

            x, y = transformer_to_utm.transform(lon, lat)
            dx = width_km * 1000 / 2
            dy = length_km * 1000 / 2
            rect = Polygon([(-dx, -dy), (-dx, dy), (dx, dy), (dx, -dy)])
            rect_rot = rotate(rect, orbit_angle, origin=(0, 0), use_radians=False)
            rect_rot_trans = translate(rect_rot, xoff=x, yoff=y)
            coords_latlon = [transformer_to_latlon.transform(px, py) for px, py in rect_rot_trans.exterior.coords]

            poly = kml.newpolygon(
                name=f"{direction} @ {lat:.4f},{lon:.4f}",
                outerboundaryis=coords_latlon
            )
            poly.style.polystyle.color = simplekml.Color.changealphaint(100, simplekml.Color.green)

        kmz_path = "sso_polygons_vertical.kmz"
        kml.savekmz(kmz_path)

        # Allow download
        with open(kmz_path, "rb") as f:
            st.download_button("Download KMZ", f, file_name=kmz_path)

        st.success("KMZ generated successfully!")

    except Exception as e:
        st.error(f"Error: {e}")
