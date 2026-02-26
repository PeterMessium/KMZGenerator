import streamlit as st
import simplekml
from shapely.geometry import Polygon
from shapely.affinity import rotate, translate
from pyproj import Transformer
import math

# ----------------------------
# App config
# ----------------------------
st.title("KMZ Generator")

MODE_COUNTRY = "Generate batch of polygons"
MODE_MANUAL = "Coordinates-based input"

mode = st.radio(
    "Polygon generation mode",
    [MODE_COUNTRY, MODE_MANUAL]
)

# ----------------------------
# Country centroids (approx)
# ----------------------------
COUNTRY_CENTROIDS = {
    "UK": (54.5, -2.5),
    "France": (46.5, 2.5),
    "Germany": (51.0, 10.0),
    "Poland": (52.0, 19.0),
    "Romania": (45.8, 24.9),
    "Bulgaria": (42.7, 25.5),
    "Hungary": (47.1, 19.5),
    "Spain": (40.3, -3.7),
    "Italy": (43.0, 12.5),
    "Austria": (47.6, 14.1),
    "Czech Republic": (49.8, 15.5),
    "Netherlands": (52.2, 5.3),
}

# ----------------------------
# Inputs
# ----------------------------
if mode == MODE_MANUAL:
    st.markdown("Enter centroids (latitude, longitude), one per line.")
    centroids_input = st.text_area(
        "Centroids (lat, lon)",
        "0.00,0.00"
    )
else:
    country = st.selectbox(
        "Country",
        list(COUNTRY_CENTROIDS.keys())
    )
    n_polygons = st.slider(
        "Number of polygons",
        min_value=0,
        max_value=50,
        value=10
    )

length_km = st.number_input(
    "Length along satellite track (km)",
    min_value=1.0,
    value=70.0
)
width_km = st.number_input(
    "Width perpendicular to track (km)",
    min_value=1.0,
    value=20.0
)
direction = st.selectbox(
    "Orbit direction",
    ["NE->SW", "SE->NW"]
)

# ----------------------------
# Orbit geometry
# ----------------------------
def sso_ground_track_angle(lat_deg, inclination_deg=97.8, direction="NE->SW"):
    """
    Approximate Sun-synchronous satellite ground track angle (degrees)
    at a given latitude.
    """
    if abs(lat_deg) > inclination_deg:
        lat_deg = math.copysign(inclination_deg, lat_deg)

    lat_rad = math.radians(lat_deg)
    inc_rad = math.radians(inclination_deg)

    ratio = math.cos(inc_rad) / math.cos(lat_rad)
    ratio = max(-1.0, min(1.0, ratio))

    angle_rad = math.asin(ratio)
    angle_deg = math.degrees(angle_rad)

    return angle_deg if direction == "NE->SW" else -angle_deg


# ----------------------------
# Helpers
# ----------------------------
def build_polygon(lat, lon, length_km, width_km, direction, x_offset_m=0):
    """
    Build a rotated rectangle polygon centred on (lat, lon),
    optionally offset in UTM X (east-west).
    """
    orbit_angle = sso_ground_track_angle(lat, direction=direction)

    utm_zone = int((lon + 180) / 6) + 1
    is_northern = lat >= 0
    epsg = 32600 + utm_zone if is_northern else 32700 + utm_zone

    to_utm = Transformer.from_crs(
        "EPSG:4326", f"EPSG:{epsg}", always_xy=True
    )
    to_ll = Transformer.from_crs(
        f"EPSG:{epsg}", "EPSG:4326", always_xy=True
    )

    x, y = to_utm.transform(lon, lat)

    dx = width_km * 1000 / 2
    dy = length_km * 1000 / 2

    rect = Polygon([
        (-dx, -dy),
        (-dx, dy),
        (dx, dy),
        (dx, -dy)
    ])

    rect_rot = rotate(rect, orbit_angle, origin=(0, 0), use_radians=False)
    rect_final = translate(
        rect_rot,
        xoff=x + x_offset_m,
        yoff=y
    )

    return [to_ll.transform(px, py) for px, py in rect_final.exterior.coords]


def generate_centroids():
    """
    Return list of (lat, lon, x_offset_m)
    """
    centroids = []

    if mode == MODE_MANUAL:
        for line in centroids_input.splitlines():
            if not line.strip():
                continue
            lat_str, lon_str = line.split(",")
            centroids.append((float(lat_str), float(lon_str), 0.0))
    else:
        base_lat, base_lon = COUNTRY_CENTROIDS[country]
        spacing_m = 10_000  # ~10 km

        for i in range(n_polygons):
            # Alternate left/right offsets
            offset_index = (i // 2) + 1
            sign = -1 if i % 2 == 0 else 1
            x_offset = sign * offset_index * spacing_m
            centroids.append((base_lat, base_lon, x_offset))

    return centroids


# ----------------------------
# Generate KMZ
# ----------------------------
if st.button("Generate KMZ"):
    try:
        centroids = generate_centroids()
        kml = simplekml.Kml()

        for i, (lat, lon, x_offset) in enumerate(centroids, start=1):
            coords = build_polygon(
                lat=lat,
                lon=lon,
                length_km=length_km,
                width_km=width_km,
                direction=direction,
                x_offset_m=x_offset
            )

            poly = kml.newpolygon(
                name=f"Polygon {i} @ {lat:.3f},{lon:.3f}",
                outerboundaryis=coords
            )
            poly.style.polystyle.color = simplekml.Color.changealphaint(
                100,
                simplekml.Color.green
            )

        kmz_path = "polygons.kmz"
        kml.savekmz(kmz_path)

        with open(kmz_path, "rb") as f:
            st.download_button(
                "Download KMZ",
                f,
                file_name=kmz_path
            )

        st.success("KMZ generated successfully!")

    except Exception as e:
        st.error(f"Error: {e}")
