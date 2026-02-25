import simplekml
from shapely.geometry import Polygon
from shapely.affinity import rotate, translate
from pyproj import Transformer
import math

# --- Inputs ---
centroids = [
    (51.5010, -0.1420)
]
length_km = 70.0  # along satellite ground track (north-south)
width_km = 20.0   # perpendicular (east-west)
direction = "NE->SW"  # "NE->SW" descending, "SE->NW" ascending

# --- Correct SSO angles (slightly off-polar) ---
if direction == "NE->SW":
    orbit_angle = -7  # descending pass, slightly west of north
elif direction == "SE->NW":
    orbit_angle = 7   # ascending pass, slightly east of north
else:
    raise ValueError("Direction must be NE->SW or SE->NW")

# --- Create KMZ ---
kml = simplekml.Kml()

for lat, lon in centroids:
    # Determine UTM zone
    utm_zone = int((lon + 180) / 6) + 1
    is_northern = lat >= 0

    # Define transformers
    transformer_to_utm = Transformer.from_crs(
        f"EPSG:4326", f"EPSG:{32600 + utm_zone if is_northern else 32700 + utm_zone}", always_xy=True
    )
    transformer_to_latlon = Transformer.from_crs(
        f"EPSG:{32600 + utm_zone if is_northern else 32700 + utm_zone}", "EPSG:4326", always_xy=True
    )

    # Convert centroid to meters
    x, y = transformer_to_utm.transform(lon, lat)

    # Create rectangle in meters
    # Long axis along y (north-south), short axis along x (east-west)
    dx = width_km * 1000 / 2   # half width (meters)
    dy = length_km * 1000 / 2  # half length (meters)
    rect = Polygon([(-dx, -dy), (-dx, dy), (dx, dy), (dx, -dy)])

    # Rotate rectangle slightly from north
    rect_rot = rotate(rect, orbit_angle, origin=(0, 0), use_radians=False)

    # Translate rectangle to UTM centroid
    rect_rot_trans = translate(rect_rot, xoff=x, yoff=y)

    # Convert polygon back to lat/lon for KML
    coords_latlon = [transformer_to_latlon.transform(px, py) for px, py in rect_rot_trans.exterior.coords]

    # Add to KML
    poly = kml.newpolygon(
        name=f"{direction} @ {lat:.4f},{lon:.4f}",
        outerboundaryis=coords_latlon
    )
    poly.style.polystyle.color = simplekml.Color.changealphaint(100, simplekml.Color.green)

# Save KMZ
kml.savekmz("sso_polygons_vertical.kmz")
print("Saved as sso_polygons_vertical.kmz")

