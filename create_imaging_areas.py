import streamlit as st
import geopandas as gpd
import simplekml
from shapely.geometry import Polygon, MultiPolygon, box
from shapely.affinity import rotate, translate
from shapely.ops import unary_union
from pyproj import Transformer
import math
import zipfile
import os
import pandas as pd

# ----------------------------
# App config
# ----------------------------
st.set_page_config(page_title="Operations Team Tooling", layout="wide")
st.title("Operations Team Tooling")

# ----------------------------
# Tabs
# ----------------------------
tab1, tab2, tab3 = st.tabs([
    "Imaging Polygon Generator",
    "Shapefile → KMZ Converter",
    "OpenCosmos Tasking AOI Generator"
])

# ----------------------------
# Tab 1: Imaging Polygon Generator
# ----------------------------
with tab1:
    MODE_COUNTRY = "Generate batch of polygons"
    MODE_MANUAL = "Coordinates-based input"

    mode = st.radio("Polygon generation mode", [MODE_COUNTRY, MODE_MANUAL])

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
        "Australia": (-25.0, 133.0),
        "New Zealand": (-41.0, 174.0),
        "Canada": (56.0, -106.0),
        "United States": (39.8, -98.6),
    }

    if mode == MODE_MANUAL:
        st.markdown("Enter centroids (latitude, longitude), one per line.")
        centroids_input = st.text_area("Centroids (lat, lon)", "0.00,0.00")
    else:
        country = st.selectbox("Country", list(COUNTRY_CENTROIDS.keys()))
        n_polygons = st.slider("Number of polygons", min_value=0, max_value=50, value=10)

    length_km = st.number_input("Length along satellite track (km)", min_value=1.0, value=70.0)
    width_km = st.number_input("Width perpendicular to track (km)", min_value=1.0, value=20.0)
    direction = st.selectbox("Orbit direction", ["NE->SW", "SE->NW"])

    # ----------------------------
    # Helper functions
    # ----------------------------
    def sso_ground_track_angle(lat_deg, inclination_deg=97.8, direction="NE->SW"):
        if abs(lat_deg) > inclination_deg:
            lat_deg = math.copysign(inclination_deg, lat_deg)
        lat_rad = math.radians(lat_deg)
        inc_rad = math.radians(inclination_deg)
        ratio = math.cos(inc_rad) / math.cos(lat_rad)
        ratio = max(-1.0, min(1.0, ratio))
        angle_rad = math.asin(ratio)
        angle_deg = math.degrees(angle_rad)
        return angle_deg if direction == "NE->SW" else -angle_deg

    def build_polygon(lat, lon, length_km, width_km, direction, x_offset_m=0):
        orbit_angle = sso_ground_track_angle(lat, direction=direction)
        utm_zone = int((lon + 180) / 6) + 1
        is_northern = lat >= 0
        epsg = 32600 + utm_zone if is_northern else 32700 + utm_zone
        to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        to_ll = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
        x, y = to_utm.transform(lon, lat)
        dx = width_km * 1000 / 2
        dy = length_km * 1000 / 2
        rect = Polygon([(-dx, -dy), (-dx, dy), (dx, dy), (dx, -dy)])
        rect_rot = rotate(rect, orbit_angle, origin=(0, 0), use_radians=False)
        rect_final = translate(rect_rot, xoff=x + x_offset_m, yoff=y)
        return [to_ll.transform(px, py) for px, py in rect_final.exterior.coords]

    def generate_centroids():
        centroids = []
        if mode == MODE_MANUAL:
            for line in centroids_input.splitlines():
                if not line.strip():
                    continue
                lat_str, lon_str = line.split(",")
                centroids.append((float(lat_str), float(lon_str), 0.0))
        else:
            base_lat, base_lon = COUNTRY_CENTROIDS[country]
            spacing_m = 10_000
            for i in range(n_polygons):
                offset_index = (i // 2) + 1
                sign = -1 if i % 2 == 0 else 1
                x_offset = sign * offset_index * spacing_m
                centroids.append((base_lat, base_lon, x_offset))
        return centroids

    # ----------------------------
    # Generate KMZ
    # ----------------------------
    if st.button("Generate KMZ", key="tab1_generate"):
        try:
            centroids = generate_centroids()
            kml = simplekml.Kml()
            for i, (lat, lon, x_offset) in enumerate(centroids, start=1):
                coords = build_polygon(lat, lon, length_km, width_km, direction, x_offset)
                poly = kml.newpolygon(
                    name=f"Polygon {i} @ {lat:.3f},{lon:.3f}", outerboundaryis=coords
                )
                poly.style.polystyle.color = simplekml.Color.changealphaint(100, simplekml.Color.green)
            kmz_path = "polygons.kmz"
            kml.savekmz(kmz_path)
            with open(kmz_path, "rb") as f:
                st.download_button("Download KMZ", f, file_name=kmz_path)
            st.success("KMZ generated successfully!")
        except Exception as e:
            st.error(f"Error: {e}")

# ----------------------------
# Tab 2: Shapefile → KMZ Converter
# ----------------------------
with tab2:
    st.subheader("Upload Shapefile components (.shp, .shx, .dbf, .prj)")

    uploaded_files = st.file_uploader(
        "Select all shapefile components",
        type=["shp", "shx", "dbf", "prj"],
        accept_multiple_files=True
    )

    if uploaded_files:
        shapefile_folder = "temp_shapefile"
        os.makedirs(shapefile_folder, exist_ok=True)

        # Save uploaded files
        for f in uploaded_files:
            with open(os.path.join(shapefile_folder, f.name), "wb") as out_file:
                out_file.write(f.getbuffer())

        shp_files = [f for f in uploaded_files if f.name.endswith(".shp")]
        if len(shp_files) != 1:
            st.error("Please upload exactly one .shp file.")
        else:
            shp_path = os.path.join(shapefile_folder, shp_files[0].name)
            
            base_name = os.path.splitext(shp_files[0].name)[0]  # Remove .shp extension
            kmz_output = f"{base_name}.kmz"

            try:
                # Load shapefile
                gdf = gpd.read_file(shp_path)
                if gdf.crs != "EPSG:4326":
                    gdf = gdf.to_crs(epsg=4326)

                kml = simplekml.Kml()
                for idx, row in gdf.iterrows():
                    geom = row.geometry
                    # Description with all attributes except geometry
                    attrs = row.drop(labels="geometry").to_dict()
                    description = "<br>".join([f"<b>{k}</b>: {v}" for k, v in attrs.items()])

                    if isinstance(geom, MultiPolygon):
                        for poly in geom.geoms:
                            kml.newpolygon(
                                name=str(idx),
                                description=description,
                                outerboundaryis=list(poly.exterior.coords),
                                innerboundaryis=[list(interior.coords) for interior in poly.interiors]
                            )
                    elif isinstance(geom, Polygon):
                        kml.newpolygon(
                            name=str(idx),
                            description=description,
                            outerboundaryis=list(geom.exterior.coords),
                            innerboundaryis=[list(interior.coords) for interior in geom.interiors]
                        )
                    else:
                        st.warning(f"Unsupported geometry type: {geom.geom_type}")

                # Save KML then compress to KMZ
                temp_kml = "temp_shapefile.kml"
                kml.save(temp_kml)
                with zipfile.ZipFile(kmz_output, "w", zipfile.ZIP_DEFLATED) as kmz:
                    kmz.write(temp_kml, arcname="doc.kml")
                os.remove(temp_kml)

                with open(kmz_output, "rb") as f:
                    st.download_button("Download KMZ", f, file_name=kmz_output)

                st.success("Shapefile converted to KMZ successfully!")

            except Exception as e:
                st.error(f"Error converting shapefile: {e}")

# ----------------------------
# Tab 3: OC Tasking AOI Generator
# ----------------------------
with tab3:
    st.subheader("OpenCosmos Tasking AOI Generator")
    st.markdown("""
    **Instructions:**  
    Upload a CSV file with the following requirements:
    - Must have headers: `Name`, `Lat`, `Long`  
    - Latitude (`Lat`) and Longitude (`Long`) in decimal degrees  
    - Example:  
      ```
      Name,Lat,Long
      Farm1,52.123,-1.234
      Farm2,51.987,-1.567
      ```
    """)

    uploaded_csv = st.file_uploader("Upload CSV", type=["csv"], key="tab3_csv")
    
    if uploaded_csv:
        try:
            df = pd.read_csv(uploaded_csv)
            
            # Validate headers
            required_cols = {"Name", "Lat", "Long"}
            if not required_cols.issubset(df.columns):
                st.error(f"CSV must contain headers: {', '.join(required_cols)}")
            else:
                st.success(f"CSV loaded successfully with {len(df)} rows.")
                
                if st.button("Generate KMZ", key="tab3_generate"):
                    try:
                        # Convert to GeoDataFrame
                        gdf = gpd.GeoDataFrame(
                            df,
                            geometry=gpd.points_from_xy(df.Long, df.Lat),
                            crs="EPSG:4326"
                        )

                        # Determine UTM zone
                        mean_lon = gdf.geometry.x.mean()
                        utm_zone = int((mean_lon + 180) / 6) + 1
                        utm_crs = f"+proj=utm +zone={utm_zone} +datum=WGS84 +units=m +no_defs"
                        gdf_utm = gdf.to_crs(utm_crs)

                        # Create 9x9 km squares (4.5 km half-size)
                        half_size = 4500  # meters
                        polygons = [
                            box(pt.x - half_size, pt.y - half_size,
                                pt.x + half_size, pt.y + half_size)
                            for pt in gdf_utm.geometry
                        ]
                        gdf_polys = gpd.GeoDataFrame(geometry=polygons, crs=utm_crs)

                        # Merge overlapping polygons
                        merged = unary_union(gdf_polys.geometry)
                        if merged.geom_type == "Polygon":
                            merged_polys = gpd.GeoDataFrame(geometry=[merged], crs=utm_crs)
                        else:
                            merged_polys = gpd.GeoDataFrame(geometry=list(merged.geoms), crs=utm_crs)

                        # Reproject back to WGS84
                        merged_polys = merged_polys.to_crs("EPSG:4326")

                        # Create KMZ
                        kml = simplekml.Kml()
                        for i, geom in enumerate(merged_polys.geometry):
                            coords = list(geom.exterior.coords)
                            pol = kml.newpolygon(name=f"Merged Area {i+1}", outerboundaryis=coords)
                            pol.style.polystyle.color = simplekml.Color.changealphaint(100, simplekml.Color.green)

                        kmz_path = "farm_polygons_merged.kmz"
                        kml.savekmz(kmz_path)
                        with open(kmz_path, "rb") as f:
                            st.download_button("Download KMZ", f, file_name=kmz_path)
                        st.success("KMZ file generated successfully!")
                    
                    except Exception as e:
                        st.error(f"Error generating KMZ: {e}")
        except Exception as e:
            st.error(f"Error reading CSV: {e}")
