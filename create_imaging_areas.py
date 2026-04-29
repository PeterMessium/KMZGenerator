import streamlit as st
import geopandas as gpd
import simplekml
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.affinity import rotate, translate
from pyproj import Transformer
import math
import zipfile
import os
import pandas as pd
import tempfile
import xml.sax.saxutils as saxutils
import folium
from streamlit_folium import st_folium
from shapely.geometry import Polygon, MultiPolygon, Point, LineString
from shapely.ops import unary_union, transform
from shapely.affinity import rotate, translate
import re
import pandas as pd
from datetime import datetime
import sys
import shapely.wkt # Ensure this is at the top of your file

# ----------------------------
# APP CONFIG
# ----------------------------

if 'selected_tool' not in st.session_state:
    st.session_state.selected_tool = "Imaging Polygon Generator"

# ---------------------------------------------------------
# SIDEBAR NAVIGATION
# ---------------------------------------------------------

st.sidebar.header("Operations Team Tooling")

# Helper function now takes 'container' as an argument
def nav_button(label, tool_id, container):
    # Determine button type based on selection
    button_type = "primary" if st.session_state.selected_tool == tool_id else "secondary"
    
    # CRITICAL: We call .button() ON the container (the expander)
    if container.button(label, use_container_width=True, type=button_type):
        st.session_state.selected_tool = tool_id
        st.rerun()

# Section 1: Polygon Tools
poly_expander = st.sidebar.expander("Basic KMZ Tools", expanded=True)
nav_button("Imaging Polygon Generator", "Imaging Polygon Generator", poly_expander)
nav_button("Duplicate KMZs", "Duplicate KMZs", poly_expander)
nav_button("Shapefile → KMZ Converter", "Shapefile → KMZ Converter", poly_expander)

# Section 2: Trial Subsections Tools
poly_expander = st.sidebar.expander("Trial Subsections Tools", expanded=True)
nav_button("Subsection Generator", "Subsection Generator", poly_expander)
nav_button("Repeated Strip Generator", "Repeated Strip Generator", poly_expander)

# Section 2: Satellite Tasking
task_expander = st.sidebar.expander("Satellite Tasking Tools", expanded=True)
nav_button("Satellite Insights Map", "Satellite Insights Map", task_expander)
nav_button("OC Tasking AOI Generator", "OC Tasking AOI Generator", task_expander)
nav_button("WS Tasking Helper", "WS Tasking Helper", task_expander)

st.sidebar.divider()
st.sidebar.caption(f"Active Tool: **{st.session_state.selected_tool}**")

selected_tool = st.session_state.selected_tool



# ----------------------------
# SHARED HELPER FILES
# ----------------------------

def load_vector_file(uploaded_files):
    kmz_file = next((f for f in uploaded_files if f.name.lower().endswith(".kmz")), None)
    shp_file = next((f for f in uploaded_files if f.name.lower().endswith(".shp")), None)
    gdf = None
    if kmz_file:
        with tempfile.TemporaryDirectory() as tmp:
            kmz_path = os.path.join(tmp, kmz_file.name)
            with open(kmz_path, "wb") as f:
                f.write(kmz_file.getbuffer())
            with zipfile.ZipFile(kmz_path, "r") as kmz:
                kml_files = [n for n in kmz.namelist() if n.lower().endswith(".kml")]
                if not kml_files: raise ValueError("No KML found inside KMZ")
                kmz.extract(kml_files[0], tmp)
                kml_path = os.path.join(tmp, kml_files[0])
                gdf = gpd.read_file(kml_path, driver="KML")
    elif shp_file:
        with tempfile.TemporaryDirectory() as tmp:
            for f in uploaded_files:
                with open(os.path.join(tmp, f.name), "wb") as out:
                    out.write(f.getbuffer())
            shp_path = os.path.join(tmp, shp_file.name)
            gdf = gpd.read_file(shp_path)
    if gdf is not None and gdf.crs != "EPSG:4326":
        gdf = gdf.to_crs(epsg=4326)
    return gdf

def infer_hierarchy(gdf, name_col="Name", buffer_tolerance=0.0005, min_overlap_ratio=0.02):
    gdf = gdf.copy()
    if name_col not in gdf.columns:
        gdf[name_col] = ["Field_" + str(i) for i in range(len(gdf))]
    gdf["parent_field_name"] = None
    gdf["is_top_level"] = True
    gdf["_area"] = gdf.geometry.area
    gdf = gdf.sort_values("_area", ascending=False).reset_index(drop=True)
    buffered_geoms = gdf.geometry.buffer(buffer_tolerance)
    for i, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty: continue
        for j in range(i):
            parent_geom = buffered_geoms.iloc[j]
            if parent_geom is None or parent_geom.is_empty: continue
            intersection_area = parent_geom.intersection(geom).area
            overlap_ratio = intersection_area / geom.area
            if overlap_ratio >= min_overlap_ratio or parent_geom.intersects(geom):
                gdf.at[i, "parent_field_name"] = str(gdf.at[j, name_col])
                gdf.at[i, "is_top_level"] = False
                break
    gdf.drop(columns="_area", inplace=True)
    return gdf


# Tool 1: Imaging Polygon Generator

if selected_tool == "Imaging Polygon Generator":

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
        "Sweden": (62.0, 15.0),
        "Denmark": (56.0, 10.0),
        "Netherlands": (52.2, 5.3),
        "Australia": (-25.0, 133.0),
        "New Zealand": (-41.0, 174.0),
        "Canada": (56.0, -106.0),
        "United States": (39.8, -98.6),
    }

    if mode == MODE_MANUAL:
        st.markdown("Enter centroids (latitude, longitude), one per line.")
        centroids_input = st.text_area("Centroids (lat, lon)", "52.0,0.1")
    else:
        country = st.selectbox("Country", list(COUNTRY_CENTROIDS.keys()))
        n_polygons = st.slider("Number of centroids", 1, 50, 10)

    # -------------------------------------------------------
    # Polygon configuration table
    # -------------------------------------------------------

    st.markdown("### Polygon configurations")

    # Define colour options
    COLOUR_OPTIONS = {
        "Blue": "#1f77b4",
        "Red": "#d62728",
        "Green": "#2ca02c",
        "Orange": "#ff7f0e",
        "Purple": "#9467bd",
        "Brown": "#8c564b",
        "Pink": "#e377c2",
        "Grey": "#7f7f7f",
        "Cyan": "#17becf",
        "Yellow": "#bcbd22",
    }

    # Default table with two example polygons
    default_configs = pd.DataFrame([
        {"width_km": 20, "length_km": 70, "direction": "NE->SW", "colour": "Blue"},
        {"width_km": 20, "length_km": 70, "direction": "SE->NW", "colour": "Red"},
    ])

    polygon_configs = st.data_editor(
        default_configs,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "width_km": st.column_config.NumberColumn("Width (km)", min_value=1),
            "length_km": st.column_config.NumberColumn("Length (km)", min_value=1),
            "direction": st.column_config.SelectboxColumn(
                "Direction",
                options=["NE->SW", "SE->NW"]
            ),
            "colour": st.column_config.SelectboxColumn(
                "Polygon Colour",
                options=list(COLOUR_OPTIONS.keys())
            ),
        }
    )

    # -------------------------------------------------------
    # Helper functions
    # -------------------------------------------------------

    def colour_name_to_kml(colour_name, alpha=120):
        hex_colour = COLOUR_OPTIONS.get(colour_name, "#000000").lstrip("#")
        r = hex_colour[0:2]
        g = hex_colour[2:4]
        b = hex_colour[4:6]
        return f"{alpha:02x}{b}{g}{r}"

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
            spacing_m = 10000
            for i in range(n_polygons):
                offset_index = (i // 2) + 1
                sign = -1 if i % 2 == 0 else 1
                x_offset = sign * offset_index * spacing_m
                centroids.append((base_lat, base_lon, x_offset))
        return centroids

    # -------------------------------------------------------
    # Generate KMZ
    # -------------------------------------------------------
    if st.button("Generate KMZ", key="tab1_generate"):
        try:
            centroids = generate_centroids()
            if polygon_configs.empty:
                st.error("Add at least one polygon configuration.")
                st.stop()

            kml = simplekml.Kml()
            poly_count = 0

            for lat, lon, x_offset in centroids:
                for _, cfg in polygon_configs.iterrows():
                    coords = build_polygon(
                        lat,
                        lon,
                        cfg["length_km"],
                        cfg["width_km"],
                        cfg["direction"],
                        x_offset
                    )
                    poly = kml.newpolygon(
                        name=f"{cfg['width_km']}x{cfg['length_km']}km {cfg['direction']} @ {lat:.3f},{lon:.3f}",
                        outerboundaryis=coords
                    )
                    poly.style.polystyle.color = colour_name_to_kml(cfg["colour"])
                    poly.style.linestyle.color = colour_name_to_kml(cfg["colour"], 255)
                    poly_count += 1

            kmz_path = "polygons.kmz"
            kml.savekmz(kmz_path)

            with open(kmz_path, "rb") as f:
                st.download_button("Download KMZ", f, file_name=kmz_path)

            st.success(f"KMZ generated successfully! ({poly_count} polygons)")

        except Exception as e:
            st.error(f"Error: {e}")


elif selected_tool == "Shapefile → KMZ Converter":

    st.subheader("Upload Shapefile components (.shp, .shx, .dbf, .prj, .cpg)")

    uploaded_files = st.file_uploader(
        "Select all shapefile components",
        type=["shp", "shx", "dbf", "prj", "cpg"],
        accept_multiple_files=True,
        key="tab2_upload"
    )

    # ----------------------------
    # CRS Detection Helper
    # ----------------------------

    def guess_crs(gdf):

        candidates = [
            "EPSG:4326",
            "EPSG:3857",
            "EPSG:27700",
            "EPSG:2154",
            "EPSG:32629",
            "EPSG:32630",
            "EPSG:32631",
            "EPSG:32632",
            "EPSG:32633",
        ]

        for crs in candidates:
            try:
                test = gdf.set_crs(crs, allow_override=True).to_crs("EPSG:4326")
                minx, miny, maxx, maxy = test.total_bounds

                if (
                    -180 <= minx <= 180 and
                    -90 <= miny <= 90 and
                    -180 <= maxx <= 180 and
                    -90 <= maxy <= 90
                ):
                    return crs
            except:
                continue

        return None


    # ----------------------------
    # KMZ Conversion Helper
    # ----------------------------

    def gdf_to_kmz(gdf, output_path):

        kml = simplekml.Kml()

        for idx, row in gdf.iterrows():

            geom = row.geometry

            if geom is None or geom.is_empty:
                continue

            attrs = row.drop(labels="geometry").to_dict()
            description = "<br>".join([f"<b>{k}</b>: {v}" for k, v in attrs.items()])

            if geom.geom_type == "MultiPolygon":
                polygons = [p for p in geom.geoms if not p.is_empty]

            elif geom.geom_type == "Polygon":
                polygons = [geom]

            else:
                continue

            for poly in polygons:

                kml.newpolygon(
                    name=str(idx),
                    description=description,
                    outerboundaryis=list(poly.exterior.coords),
                    innerboundaryis=[list(i.coords) for i in poly.interiors]
                )

        temp_kml = output_path.replace(".kmz", ".kml")
        kml.save(temp_kml)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as kmz:
            kmz.write(temp_kml, arcname="doc.kml")

        os.remove(temp_kml)


    if uploaded_files:

        with tempfile.TemporaryDirectory() as tmp:

            for f in uploaded_files:
                with open(os.path.join(tmp, f.name), "wb") as out:
                    out.write(f.getbuffer())

            shp_files = [f for f in uploaded_files if f.name.endswith(".shp")]

            if len(shp_files) != 1:
                st.error("Please upload exactly one .shp file.")
                st.stop()

            shp_path = os.path.join(tmp, shp_files[0].name)

            try:
                gdf = gpd.read_file(shp_path)
            except Exception as e:
                st.error(f"Error reading shapefile: {e}")
                st.stop()

            # ----------------------------
            # Handle CRS
            # ----------------------------

            if gdf.crs is None:

                st.warning("No CRS detected. Attempting automatic detection...")

                guessed = guess_crs(gdf)

                if guessed:
                    st.success(f"Detected CRS: {guessed}")
                    gdf = gdf.set_crs(guessed, allow_override=True)

                else:
                    st.error("Could not automatically determine CRS.")

                    manual_crs = st.text_input(
                        "Enter CRS manually (example: EPSG:27700)"
                    )

                    if manual_crs:
                        try:
                            gdf = gdf.set_crs(manual_crs, allow_override=True)
                        except:
                            st.error("Invalid CRS.")
                            st.stop()
                    else:
                        st.stop()

            # ----------------------------
            # Convert to WGS84
            # ----------------------------

            try:
                gdf = gdf.to_crs("EPSG:4326")
            except Exception as e:
                st.error(f"CRS conversion failed: {e}")
                st.stop()

            # ----------------------------
            # Preview Map
            # ----------------------------

            st.subheader("Geometry Preview")

            centroid = gdf.geometry.centroid.iloc[0]

            m = folium.Map(
                location=[centroid.y, centroid.x],
                zoom_start=15
            )

            folium.GeoJson(gdf).add_to(m)

            st_folium(m, width="100%", height=500)

            # ----------------------------
            # Export KMZ
            # ----------------------------

            if st.button("Convert to KMZ", key="tab2_convert"):

                kmz_name = os.path.splitext(shp_files[0].name)[0] + ".kmz"
                kmz_path = os.path.join(tmp, kmz_name)

                try:

                    gdf_to_kmz(gdf, kmz_path)

                    with open(kmz_path, "rb") as f:
                        st.download_button(
                            "Download KMZ",
                            f,
                            file_name=kmz_name,
                            mime="application/vnd.google-earth.kmz"
                        )

                    st.success("Shapefile converted successfully!")

                except Exception as e:
                    st.error(f"KMZ conversion failed: {e}")

  
elif selected_tool == "OC Tasking AOI Generator":


    st.subheader("OC Tasking AOI Generator")
    st.markdown("""
    **Instructions:**  
    Upload a CSV file with headers: `Name`, `Lat`, `Long`  
    Latitude (`Lat`) and Longitude (`Long`) in decimal degrees  
    """)

    uploaded_csv = st.file_uploader("Upload CSV", type=["csv"], key="tab3_csv")

    if uploaded_csv:
        try:
            df = pd.read_csv(uploaded_csv)
            required_cols = {"Name", "Lat", "Long"}
            if not required_cols.issubset(df.columns):
                st.error(f"CSV must contain headers: {', '.join(required_cols)}")
            else:
                st.success(f"CSV loaded successfully with {len(df)} rows.")

                if st.button("Generate KMZ", key="tab3_generate"):
                    try:
                        gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.Long, df.Lat), crs="EPSG:4326")
                        mean_lon = gdf.geometry.x.mean()
                        utm_zone = int((mean_lon + 180) / 6) + 1
                        utm_crs = f"+proj=utm +zone={utm_zone} +datum=WGS84 +units=m +no_defs"
                        gdf_utm = gdf.to_crs(utm_crs)

                        half_size = 4500
                        polygons = [Polygon([
                            (pt.x - half_size, pt.y - half_size),
                            (pt.x - half_size, pt.y + half_size),
                            (pt.x + half_size, pt.y + half_size),
                            (pt.x + half_size, pt.y - half_size)
                        ]) for pt in gdf_utm.geometry]

                        gdf_polys = gpd.GeoDataFrame(geometry=polygons, crs=utm_crs)
                        merged = unary_union(gdf_polys.geometry)
                        if merged.geom_type == "Polygon":
                            merged_polys = gpd.GeoDataFrame(geometry=[merged], crs=utm_crs)
                        else:
                            merged_polys = gpd.GeoDataFrame(geometry=list(merged.geoms), crs=utm_crs)

                        merged_polys = merged_polys.to_crs("EPSG:4326")

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



elif selected_tool == "Subsection Generator":

    # --- 1. STYLING ---
    st.markdown("""
        <style>
            [data-testid="stMain"] div.stButton button, 
            [data-testid="stMain"] div.stDownloadButton button {
                width: 100% !important;
                border-radius: 10px !important;
                height: 3.5rem !important;
            }
            .block-container { 
                max-width: 98% !important; 
                padding-top: 5rem !important; 
                padding-bottom: 1rem !important; 
            }
            [data-testid="stMain"] div.stDownloadButton button {
                background-color: #1E3A8A !important; 
                color: white !important;
            }
            div[data-testid="stBaseButton-secondary"] { margin-top: 28px !important; }
            .column-header {
                font-weight: bold;
                font-size: 0.85rem;
                color: #A1A1AA;
                margin-bottom: 0px;
                padding-bottom: 0px;
            }
        </style>
    """, unsafe_allow_html=True)

    st.subheader("Subsection Generator")
    
    # --- 2. STATE MANAGEMENT ---
    if "group_names" not in st.session_state:
        st.session_state.group_names = ["Messium", "Farm Standard", "Low", "High"]
    
    if "group_colors" not in st.session_state:
        st.session_state.group_colors = {
            "Messium": "#1E3A8A", 
            "Farm Standard": "#10B981", 
            "Low": "#F59E0B", 
            "High": "#EF4444"
        }

    def hex_to_kml_color(hex_str, opacity="b3"):
        """Converts #RRGGBB to KML's AABBGGRR format."""
        hex_str = hex_str.lstrip('#')
        r, g, b = hex_str[0:2], hex_str[2:4], hex_str[4:6]
        return f"{opacity}{b}{g}{r}"

    uploaded_files = st.file_uploader(
        "Upload KMZ",
        type=["kmz"],
        accept_multiple_files=True,
        key="tab4_upload"
    )

    processed_datasets = []
    if uploaded_files:
        # --- 3. PROCESSING DATA ---
        datasets = [[f] for f in uploaded_files]
        for i, dataset_files in enumerate(datasets):
            try:
                gdf = load_vector_file(dataset_files)
                if gdf is not None and not gdf.empty:
                    
                    # STRING SANITIZATION
                    for col in gdf.select_dtypes(include=['object']).columns:
                        gdf[col] = gdf[col].astype(str).str.strip()

                    # GEOMETRY FLATTENING
                    if gdf.geometry.has_z.any():
                        from shapely.ops import transform
                        def to_2d(geom):
                            if geom is None: return None
                            return transform(lambda x, y, z=None: (x, y), geom)
                        gdf["geometry"] = gdf["geometry"].apply(to_2d)

                    # HIERARCHY LOGIC
                    potential_cols = ["Name", "name", "Field", "field", "Label", "label", "ID"]
                    target_col = next((c for c in potential_cols if c in gdf.columns), None)
                    
                    if target_col:
                        gdf["Name"] = gdf[target_col].replace(["nan", "None", ""], None)
                        gdf["Name"] = [n if (n and n != "None") else f"Field_{j}" for j, n in enumerate(gdf["Name"])]
                    else:
                        gdf["Name"] = [f"Field_{j}" for j in range(len(gdf))]
                    
                    if gdf.crs != "EPSG:4326":
                        gdf = gdf.to_crs(epsg=4326)

                    gdf = infer_hierarchy(gdf, name_col="Name")
                    processed_datasets.append(gdf)

            except Exception as e:
                st.error(f"Error loading KMZ {i+1}: {e}")

    # --- 4. MAIN INTERFACE ---
    if processed_datasets:
        dropdown_options = ["None"] + [g for g in st.session_state.group_names if g]
        col_input, col_map = st.columns([1.5, 1.5]) 
        all_mappings = [] 

        with col_input:
            st.markdown("#### Polygon Settings")
            container = st.container(height=600) 
            with container:
                for d_idx, gdf in enumerate(processed_datasets):
                    ds_map = {"top": {}, "sub": {}}
                    top_levels = gdf[gdf["is_top_level"]]
                    
                    for _, row in top_levels.iterrows():
                        orig_name = row["Name"]
                        new_name = st.text_input(f"Field: {orig_name}", value=orig_name, key=f"n_{d_idx}_t_{orig_name}")
                        ds_map["top"][orig_name] = {"name": new_name, "included": True}
                        
                        h_col1, h_col2, h_col3, h_col4 = st.columns([0.1, 1.7, 0.7, 1.5])
                        with h_col2: st.markdown('<p class="column-header">Subsection Name</p>', unsafe_allow_html=True)
                        with h_col3: st.markdown('<p class="column-header">Included</p>', unsafe_allow_html=True)
                        with h_col4: st.markdown('<p class="column-header">Assign Group</p>', unsafe_allow_html=True)
                        
                        subs = gdf[gdf["parent_field_name"] == orig_name]
                        for s_row_idx, s_row in subs.iterrows():
                            sc1, sc2, sc3, sc4 = st.columns([0.1, 1.7, 0.7, 1.5])
                            with sc1: st.markdown(f"↳")
                            with sc2: s_name = st.text_input("Sub Name", value=s_row["Name"], key=f"name_{d_idx}_s_{s_row_idx}", label_visibility="collapsed")
                            with sc3: s_inc = st.checkbox("Included", value=True, key=f"inc_{d_idx}_s_{s_row_idx}", label_visibility="collapsed")
                            with sc4: s_group = st.selectbox("Group", options=dropdown_options, key=f"grp_{d_idx}_s_{s_row_idx}", label_visibility="collapsed")
                            ds_map["sub"][s_row_idx] = {"name": s_name, "included": s_inc, "group": s_group}
                        st.divider()
                    all_mappings.append(ds_map)

        with col_map:
            st.markdown("#### Map Preview")
            main_gdf = processed_datasets[0]
            avg_lat, avg_lon = main_gdf.geometry.centroid.y.mean(), main_gdf.geometry.centroid.x.mean()
            m = folium.Map(location=[avg_lat, avg_lon], zoom_start=15)
            
            for d_idx, gdf in enumerate(processed_datasets):
                for idx, row in gdf.iterrows():
                    is_top = row["is_top_level"]
                    m_key = "top" if is_top else "sub"
                    m_lookup = row["Name"] if is_top else idx
                    m_data = all_mappings[d_idx][m_key].get(m_lookup)
                    
                    if m_data and m_data["included"]:
                        group_name = m_data.get("group", "None")
                        color = "#1E3A8A" if is_top else st.session_state.group_colors.get(group_name, "#ff7f0e")

                        label = m_data["name"] + (f" ({group_name})" if group_name != "None" else "")
                        geom = row.geometry
                        polys = [geom] if geom.geom_type == 'Polygon' else list(geom.geoms)
                        for p in polys:
                            folium.Polygon(
                                locations=[[pt[1], pt[0]] for pt in p.exterior.coords], 
                                color=color, fill=True, fill_opacity=0.4, 
                                weight=3 if is_top else 2, tooltip=label
                            ).add_to(m)
            st_folium(m, width="100%", height=550)

        # --- 5. GROUP MANAGER ---
        st.divider()
        st.markdown("#### 🛠️ Group Manager")
        group_df_data = [{"Group Name": g} for g in st.session_state.group_names]
        current_df = pd.DataFrame(group_df_data)

        col_tbl, col_clr = st.columns([2, 2])
        with col_tbl:
            st.markdown('<p class="column-header">Edit Group Names</p>', unsafe_allow_html=True)
            edited_df = st.data_editor(current_df, num_rows="dynamic", use_container_width=True, key="group_editor")
        
        with col_clr:
            st.markdown('<p class="column-header">Assign Colors</p>', unsafe_allow_html=True)
            new_color_map = {}
            picker_cols = st.columns(2) 
            for idx, g_name in enumerate(st.session_state.group_names):
                with picker_cols[idx % 2]:
                    current_color = st.session_state.group_colors.get(g_name, "#7F7F7F")
                    picked_color = st.color_picker(f"Color: {g_name}", value=current_color, key=f"cp_{g_name}")
                    new_color_map[g_name] = picked_color

        if st.button("Update Groups & Colors", use_container_width=True):
            valid_names = [g for g in edited_df["Group Name"].dropna().unique().tolist() if str(g).strip() != ""]
            st.session_state.group_names = valid_names
            st.session_state.group_colors = {name: new_color_map.get(name, "#7F7F7F") for name in valid_names}
            st.rerun()

        # --- 6. EXPORT LOGIC ---
        st.divider()
        
        # FIX: Button only shows if datasets exist
        if st.button("Generate Subsection-Enabled KMZ", use_container_width=True, type="primary"):
            output_generated = False
            
            # Anchor for scrolling
            result_anchor = st.container()

            for d_idx, gdf in enumerate(processed_datasets):
                kml = simplekml.Kml()
                export_rows = []
                
                for idx, row in gdf.iterrows():
                    is_top = row["is_top_level"]
                    m_key = "top" if is_top else "sub"
                    m_lookup = row["Name"] if is_top else idx
                    m_data = all_mappings[d_idx][m_key].get(m_lookup)

                    if m_data and m_data["included"]:
                        row_copy = row.copy()
                        row_copy["Name"] = m_data["name"]
                        row_copy["is_field_section"] = "True" if not is_top else "null"
                        row_copy["parent_field_name"] = all_mappings[d_idx]["top"].get(row["parent_field_name"], {}).get("name", row["parent_field_name"]) if not is_top else "null"
                        row_copy["ExportGroup"] = m_data.get("group", "None")
                        export_rows.append(row_copy)
                
                if export_rows:
                    working_gdf = gpd.GeoDataFrame(export_rows, crs=gdf.crs)
                    ungrouped = working_gdf[working_gdf["ExportGroup"] == "None"]
                    grouped = working_gdf[working_gdf["ExportGroup"] != "None"]
                    
                    final_gdf_list = []
                    if not ungrouped.empty: final_gdf_list.append(ungrouped)
                    if not grouped.empty:
                        dissolved = grouped.dissolve(by="ExportGroup", aggfunc={'Name': 'first', 'is_field_section': 'first', 'parent_field_name': 'first'}).reset_index()
                        dissolved["Name"] = dissolved["ExportGroup"] 
                        final_gdf_list.append(dissolved)
                    
                    if final_gdf_list:
                        export_gdf = pd.concat(final_gdf_list, ignore_index=True)
                        for _, row in export_gdf.iterrows():
                            geom = row.geometry
                            if geom.geom_type == 'Polygon':
                                pol = kml.newpolygon(name=row["Name"], outerboundaryis=list(geom.exterior.coords))
                            else:
                                pol = kml.newmultigeometry(name=row["Name"])
                                for part in geom.geoms:
                                    pol.newpolygon(outerboundaryis=list(part.exterior.coords))
                            
                            group_key = row.get("ExportGroup", "None")
                            kml_color = hex_to_kml_color(st.session_state.group_colors.get(group_key, "#1E3A8A"))
                            pol.style.polystyle.color, pol.style.polystyle.fill = kml_color, 1
                            pol.style.linestyle.color, pol.style.linestyle.width = kml_color, 2
                            pol.extendeddata.newdata("is_field_section", str(row["is_field_section"]))
                            pol.extendeddata.newdata("parent_field_name", str(row["parent_field_name"]))

                        kmz_path = f"export_{d_idx+1}.kmz"
                        kml.savekmz(kmz_path)
                        
                        # Display result in the anchor area
                        with result_anchor:
                            st.success(f"✅ Success! {kmz_path} generated.")
                            with open(kmz_path, "rb") as f:
                                st.download_button(label=f"📥 Download {kmz_path}", data=f, file_name=kmz_path, key=f"dl_{d_idx}", use_container_width=True)
                        output_generated = True

            if output_generated:
                # JS to scroll to the download button
                st.components.v1.html("""
                    <script>
                        var buttons = window.parent.document.querySelectorAll('button');
                        for (var i = 0; i < buttons.length; i++) {
                            if (buttons[i].textContent.includes('Download export')) {
                                buttons[i].scrollIntoView({behavior: 'smooth'});
                                break;
                            }
                        }
                    </script>
                """, height=0)
            else:
                st.warning("No polygons selected.")
    else:
        st.info("Upload a KMZ file to begin.")

elif selected_tool == "Duplicate KMZs":


    st.subheader("Duplicate KMZ Polygons")

    uploaded_kmz = st.file_uploader(
        "Upload KMZ containing ONE polygon",
        type=["kmz"],
        key="tab5_upload"
    )

    duplicates = st.number_input(
        "Number of duplicates",
        min_value=1,
        max_value=200,
        value=10
    )

    offset_m = st.number_input(
        "Offset between polygons (metres)",
        min_value=1,
        value=50
    )

    if uploaded_kmz:

        try:

            # ----------------------------
            # Extract KMZ
            # ----------------------------
            with tempfile.TemporaryDirectory() as tmp:

                kmz_path = os.path.join(tmp, uploaded_kmz.name)

                with open(kmz_path, "wb") as f:
                    f.write(uploaded_kmz.getbuffer())

                with zipfile.ZipFile(kmz_path, "r") as kmz:
                    kml_files = [n for n in kmz.namelist() if n.endswith(".kml")]
                    kmz.extract(kml_files[0], tmp)
                    kml_path = os.path.join(tmp, kml_files[0])

                gdf = gpd.read_file(kml_path, driver="KML")

            if gdf.empty:
                st.error("No geometry found.")
                st.stop()

            geom = gdf.geometry.iloc[0]

            if geom.geom_type != "Polygon":
                st.error("KMZ must contain a single Polygon.")
                st.stop()

            # ----------------------------
            # Convert to UTM
            # ----------------------------
            centroid = geom.centroid
            lon, lat = centroid.x, centroid.y

            utm_zone = int((lon + 180) / 6) + 1
            epsg = 32600 + utm_zone if lat >= 0 else 32700 + utm_zone

            gdf_utm = gdf.to_crs(epsg)

            base_poly = gdf_utm.geometry.iloc[0]

            # ----------------------------
            # Generate duplicates
            # ----------------------------
            polys = []

            for i in range(duplicates):

                new_poly = translate(
                    base_poly,
                    xoff=i * offset_m,
                    yoff=0
                )

                polys.append(new_poly)

            dup_gdf = gpd.GeoDataFrame(geometry=polys, crs=f"EPSG:{epsg}")

            dup_gdf = dup_gdf.to_crs("EPSG:4326")

            # ----------------------------
            # Export KMZ
            # ----------------------------
            if st.button("Generate Duplicated KMZ"):

                kml = simplekml.Kml()

                for i, geom in enumerate(dup_gdf.geometry):

                    coords = list(geom.exterior.coords)

                    pol = kml.newpolygon(
                        name=f"Duplicate {i+1}",
                        outerboundaryis=coords
                    )

                    pol.style.polystyle.color = simplekml.Color.changealphaint(
                        120,
                        simplekml.Color.green
                    )

                kmz_out = "duplicated_polygons.kmz"

                kml.savekmz(kmz_out)

                with open(kmz_out, "rb") as f:
                    st.download_button(
                        "Download KMZ",
                        f,
                        file_name=kmz_out,
                        mime="application/vnd.google-earth.kmz"
                    )

                st.success(f"{duplicates} polygons generated successfully.")

        except Exception as e:
            st.error(f"Error: {e}")


elif selected_tool == "Repeated Strip Generator":

    # --- 1. STYLING ---
    st.markdown("""
        <style>
            .block-container { max-width: 98% !important; padding-left: 1rem !important; padding-right: 1rem !important; padding-top: 2rem !important; }
            
            .center-wrapper {
                text-align: center;
                width: 100%;
            }
            
            .docs-link {
                display: inline-block;
                padding: 12px 24px;
                background-color: #374151;
                color: #ffffff !important;
                text-decoration: none !important;
                border-radius: 10px;
                font-weight: 600;
                border: 1px solid #4B5563;
                margin-top: 10px;
                margin-bottom: 20px;
                transition: background 0.3s;
            }
            .docs-link:hover { background-color: #4B5563; }

            div.stButton > button[kind="primary"] { 
                background-color: #ef4444 !important; 
                color: white !important; 
                border: none !important; 
                border-radius: 10px !important; 
                font-weight: 600 !important; 
                height: 3.5rem !important;
                width: 100% !important;
            }

            div.stButton > button:disabled {
                background-color: #374151 !important;
                color: #9CA3AF !important;
                opacity: 0.6 !important;
                cursor: not-allowed !important;
            }

            div.stDownloadButton button { 
                background-color: #1E3A8A !important; 
                color: white !important; 
                border-radius: 10px !important; 
                font-weight: 600 !important; 
                width: 100% !important; 
                height: 3.5rem !important;
                border: none !important;
            }
        </style>
    """, unsafe_allow_html=True)

    # --- 2. SESSION STATE ---
    if "rs_group_names" not in st.session_state:
        st.session_state.rs_group_names = ["Messium", "Farm Standard"]
    if "rs_rest_name" not in st.session_state:
        st.session_state.rs_rest_name = "Rest of Field"
    if "rs_group_colors" not in st.session_state:
        st.session_state.rs_group_colors = {"Messium": "#1E3A8A", "Farm Standard": "#10B981", "Rest of Field": "#808080"}
    
    keys = {
        'field_name': "", 'strip_w': "", 'headland_b': 0.0,
        'f_t_lon': "", 'f_t_lat': "", 'f_b_lon': "", 'f_b_lat': "",
        'l_t_lon': "", 'l_t_lat': "", 'l_b_lon': "", 'l_b_lat': "",
        'tab7_result_gdf': None, 'tab7_field_name': "", 'field_boundary_gdf': None,
        'uniform_length': False, 'create_rest_of_field': False
    }
    for key, val in keys.items():
        if key not in st.session_state: st.session_state[key] = val

    def hex_to_kml_color(hex_str, opacity="b3"):
        hex_str = hex_str.lstrip('#')
        r, g, b = hex_str[0:2], hex_str[2:4], hex_str[4:6]
        return f"{opacity}{b}{g}{r}"

    # --- 3. HEADER ---
    st.markdown("""
        <div class="center-wrapper">
            <h2 style="margin-bottom: 0px;">Repeated Strip Generator</h2>
            <a href="https://docs.google.com/document/d/1ZR-0C6Y_YgmpQ0Kt_l7vvT9xGFtO-3I9GKug7SkQVd8/edit?usp=sharing" target="_blank" class="docs-link">📖 View How To Guide</a>
        </div>
    """, unsafe_allow_html=True)

    st.divider()

    # --- 4. INPUT UI ---
    col_files, col_coords = st.columns([1, 1.5])
    with col_files:
        st.markdown("### 1. Parameters")
        uploaded_field = st.file_uploader("Upload KMZ/SHP", type=["kmz", "shp", "kml"], key="rs_upload")
        
        if uploaded_field:
            try:
                raw_gdf = load_vector_file([uploaded_field])
                if raw_gdf is not None:
                    poly_f = raw_gdf[raw_gdf.geometry.type == 'Polygon']
                    if not poly_f.empty:
                        st.session_state.field_boundary_gdf = poly_f
                        if st.session_state.field_name == "":
                            st.session_state.field_name = str(poly_f.iloc[0].get('Name', 'Field')).strip()
                    
                    point_f = raw_gdf[raw_gdf.geometry.type == 'Point']
                    name_map = {"First - Top": ("f_t_lat", "f_t_lon"), "First - Bottom": ("f_b_lat", "f_b_lon"), 
                                "Last - Top": ("l_t_lat", "l_t_lon"), "Last - Bottom": ("l_b_lat", "l_b_lon")}
                    for _, row in point_f.iterrows():
                        p_name = str(row.get('Name', '')).strip()
                        if p_name in name_map:
                            st.session_state[name_map[p_name][0]], st.session_state[name_map[p_name][1]] = f"{row.geometry.y:.6f}", f"{row.geometry.x:.6f}"
            except Exception as e: st.error(f"Upload error: {e}")
            
        st.text_input("Field Name", key="field_name")
        st.text_input("Tramline Width (m)", key="strip_w")
        st.number_input("Headland Buffer (m)", min_value=0.0, step=1.0, key="headland_b")
        st.toggle("Uniform Strip Length", key="uniform_length")
        st.toggle(f"Create '{st.session_state.rs_rest_name}' subsection", key="create_rest_of_field")

    with col_coords:
        st.markdown("### 2. Alignment")
        with st.container(border=True):
            st.markdown("**First Strip Center**")
            c1, c2 = st.columns(2); c1.text_input("Top Lat", key="f_t_lat"); c2.text_input("Top Lon", key="f_t_lon")
            c3, c4 = st.columns(2); c3.text_input("Bottom Lat", key="f_b_lat"); c4.text_input("Bottom Lon", key="f_b_lon")
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("**Last Strip Center**")
            c1, c2 = st.columns(2); c1.text_input("Top Lat", key="l_t_lat"); c2.text_input("Top Lon", key="l_t_lon")
            c3, c4 = st.columns(2); c3.text_input("Bottom Lat", key="l_b_lat"); c4.text_input("Bottom Lon", key="l_b_lon")

    # --- 5. GENERATION LOGIC ---
    required = ['field_name', 'strip_w', 'f_t_lat', 'f_t_lon', 'f_b_lat', 'f_b_lon', 'l_t_lat', 'l_t_lon', 'l_b_lat', 'l_b_lon']
    all_filled = all(str(st.session_state.get(k, "")).strip() != "" for k in required) and uploaded_field

    if st.button("Generate Strips", type="primary", use_container_width=True, disabled=not all_filled):
        try:
            strip_width = float(st.session_state.strip_w)
            h_buffer = float(st.session_state.headland_b)
            field_gdf = load_vector_file([uploaded_field])
            field_gdf = field_gdf[field_gdf.geometry.type == 'Polygon']
            
            if not field_gdf.empty:
                avg_lon, avg_lat = field_gdf.geometry.centroid.x.mean(), field_gdf.geometry.centroid.y.mean()
                utm_zone = int((avg_lon + 180) / 6) + 1
                utm_crs = f"EPSG:{32600 + utm_zone if avg_lat >= 0 else 32700 + utm_zone}"
                to_utm = Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True).transform
                
                field_utm_full = field_gdf.to_crs(utm_crs).geometry.unary_union
                field_safe_zone = field_utm_full.buffer(-h_buffer) if h_buffer > 0 else field_utm_full
                
                p1_t = Point(to_utm(float(st.session_state.f_t_lon), float(st.session_state.f_t_lat)))
                p1_b = Point(to_utm(float(st.session_state.f_b_lon), float(st.session_state.f_b_lat)))
                p2_t = Point(to_utm(float(st.session_state.l_t_lon), float(st.session_state.l_t_lat)))
                
                angle_v = math.atan2(p1_b.y - p1_t.y, p1_b.x - p1_t.x)
                angle_h = angle_v + (math.pi / 2)
                
                total_dist = ((p2_t.x - p1_t.x) * math.cos(angle_h)) + ((p2_t.y - p1_t.y) * math.sin(angle_h))
                num_strips = int(round(abs(total_dist) / strip_width)) + 1
                direction = 1 if total_dist > 0 else -1
                
                lane_candidates = []
                for i in range(num_strips):
                    offset = i * strip_width * direction
                    pivot_t = Point(p1_t.x + offset * math.cos(angle_h), p1_t.y + offset * math.sin(angle_h))
                    line = LineString([(pivot_t.x - 5000 * math.cos(angle_v), pivot_t.y - 5000 * math.sin(angle_v)), (pivot_t.x + 5000 * math.cos(angle_v), pivot_t.y + 5000 * math.sin(angle_v))])
                    
                    inter = line.intersection(field_safe_zone)
                    if not inter.is_empty:
                        if inter.geom_type == 'MultiLineString': inter = max(inter.geoms, key=lambda x: x.length)
                        coords = list(inter.coords)
                        dists = [(c[0]-pivot_t.x)*math.cos(angle_v) + (c[1]-pivot_t.y)*math.sin(angle_v) for c in coords]
                        lane_candidates.append({"pivot_t": pivot_t, "d_min": min(dists), "d_max": max(dists), "idx": i})

                strips_list = []
                all_strip_geoms_for_rest = []
                for l in lane_candidates:
                    s, e = l["d_min"], l["d_max"]
                    
                    p_s_long = (l["pivot_t"].x + (s - 50) * math.cos(angle_v), l["pivot_t"].y + (s - 50) * math.sin(angle_v))
                    p_e_long = (l["pivot_t"].x + (e + 50) * math.cos(angle_v), l["pivot_t"].y + (e + 50) * math.sin(angle_v))
                    
                    lane_rect = LineString([p_s_long, p_e_long]).buffer(strip_width/2, cap_style=2, join_style=2)
                    clipped_geom = lane_rect.intersection(field_safe_zone)
                    
                    if clipped_geom.geom_type == 'MultiPolygon':
                        clipped_geom = max(clipped_geom.geoms, key=lambda x: x.area)

                    all_strip_geoms_for_rest.append(clipped_geom)
                    
                    strips_list.append({
                        "Name": f"{st.session_state.field_name} - Strip {l['idx']+1}", 
                        "geometry": clipped_geom, 
                        "parent_field_name": st.session_state.field_name, 
                        "width_m": strip_width, 
                        "is_field_section": "True", 
                        "type": "strip"
                    })

                if st.session_state.create_rest_of_field:
                    combined_strips = unary_union(all_strip_geoms_for_rest).buffer(0.01, join_style=2)
                    rest_geom = field_utm_full.difference(combined_strips)
                    
                    if not rest_geom.is_empty:
                        if rest_geom.geom_type in ['MultiPolygon', 'GeometryCollection']:
                            clean_polys = [p for p in rest_geom.geoms if p.geom_type == 'Polygon' and p.area > 5.0]
                            rest_geom = unary_union(clean_polys) if clean_polys else None
                        
                        if rest_geom and not rest_geom.is_empty:
                            strips_list.append({
                                "Name": st.session_state.rs_rest_name, 
                                "geometry": rest_geom, 
                                "parent_field_name": st.session_state.field_name, 
                                "width_m": 0.0, 
                                "is_field_section": "True", 
                                "type": "rest"
                            })

                st.session_state.tab7_result_gdf = gpd.GeoDataFrame(strips_list, crs=utm_crs).to_crs("EPSG:4326")
                st.session_state.tab7_field_name = st.session_state.field_name
                st.rerun()
        except Exception as e: st.error(f"Error: {e}")

    # --- 6. GROUPING & PREVIEW ---
    if st.session_state.tab7_result_gdf is not None:
        st.divider()
        res_gdf = st.session_state.tab7_result_gdf
        field_bg_gdf = st.session_state.field_boundary_gdf
        col_group, col_preview = st.columns([1.2, 1.8])
        
        with col_group:
            st.markdown("### 3. Assign Groups")
            assign_container = st.container(height=400)
            group_options = ["None"] + st.session_state.rs_group_names
            strip_mappings = {}
            with assign_container:
                for idx, row in res_gdf.iterrows():
                    c1, c2 = st.columns([1, 1.5])
                    # If this is the "Rest" row, allow editing the label text directly
                    if row['type'] == 'rest':
                        c1.text_input("Rest Name", value=st.session_state.rs_rest_name, key="rs_rest_name", label_visibility="collapsed")
                        c2.markdown("*(No Grouping Available)*")
                        strip_mappings[idx] = "Rest of Field" # Fixed mapping for rest
                    else:
                        c1.markdown(f"**{row['Name']}**")
                        strip_mappings[idx] = c2.selectbox(f"Grp_{idx}", options=group_options, label_visibility="collapsed", key=f"rs_map_{idx}")

            st.markdown("#### Edit Group Names")
            g_df = pd.DataFrame([{"Group Name": g} for g in st.session_state.rs_group_names])
            edited_g = st.data_editor(g_df, num_rows="dynamic", use_container_width=True, key="rs_g_editor")
            
            cp1, cp2 = st.columns(2)
            for i, g_name in enumerate(st.session_state.rs_group_names):
                with cp1 if i % 2 == 0 else cp2:
                    st.session_state.rs_group_colors[g_name] = st.color_picker(g_name, st.session_state.rs_group_colors.get(g_name, "#7F7F7F"), key=f"cp_rs_{g_name}")
            
            if "rest" in res_gdf['type'].values:
                st.session_state.rs_group_colors["Rest of Field"] = st.color_picker(f"{st.session_state.rs_rest_name} Color", st.session_state.rs_group_colors.get("Rest of Field", "#808080"), key="cp_rs_rest")

            if st.button("Update Group Settings"):
                st.session_state.rs_group_names = [x for x in edited_g["Group Name"].tolist() if x]
                st.rerun()

        with col_preview:
            st.markdown("### 4. Preview")
            m = folium.Map(location=[res_gdf.geometry.centroid.y.mean(), res_gdf.geometry.centroid.x.mean()], zoom_start=15)
            if field_bg_gdf is not None:
                folium.GeoJson(field_bg_gdf, style_function=lambda x: {'color': 'red', 'fillOpacity': 0, 'weight': 3}).add_to(m)
            for idx, row in res_gdf.iterrows():
                group = strip_mappings[idx]
                color = st.session_state.rs_group_colors.get("Rest of Field" if row['type'] == 'rest' else group, "#808080")
                tooltip_name = st.session_state.rs_rest_name if row['type'] == 'rest' else row['Name']
                folium.GeoJson(row.geometry, style_function=lambda x, c=color: {'color': c, 'fillOpacity': 0.4, 'weight': 1}, tooltip=tooltip_name).add_to(m)
            st_folium(m, width="100%", height=550, key="rs_map_preview")

        # --- 7. EXPORT ---
        st.divider()
        if st.button("Generate KMZ", use_container_width=True, type="primary"):
            kml = simplekml.Kml()
            
            # 1. Field Boundary
            if field_bg_gdf is not None:
                for _, f_row in field_bg_gdf.iterrows():
                    f_polys = [f_row.geometry] if f_row.geometry.geom_type == 'Polygon' else list(f_row.geometry.geoms)
                    for p in f_polys:
                        pol = kml.newpolygon(name=f"{st.session_state.tab7_field_name}")
                        pol.outerboundaryis = list(p.exterior.coords)
                        pol.style.polystyle.color = simplekml.Color.changealphaint(20, simplekml.Color.red)
                        pol.style.linestyle.color = simplekml.Color.red
                        pol.style.linestyle.width = 4
                        pol.extendeddata.newdata("is_field_section", "False")

            export_gdf = res_gdf.copy()
            export_gdf["ExportGroup"] = [strip_mappings[i] for i in export_gdf.index]

            # 2. Grouped Strips
            for group in st.session_state.rs_group_names:
                group_gdf = export_gdf[(export_gdf["ExportGroup"] == group) & (export_gdf["type"] == "strip")]
                if not group_gdf.empty:
                    combined_geom = group_gdf.geometry.unary_union
                    pol_geom = kml.newmultigeometry(name=group)
                    geoms = [combined_geom] if combined_geom.geom_type == 'Polygon' else list(combined_geom.geoms)
                    k_clr = hex_to_kml_color(st.session_state.rs_group_colors.get(group, "#FFFFFF"))
                    for g in geoms:
                        p = pol_geom.newpolygon(outerboundaryis=list(g.exterior.coords))
                        if hasattr(g, 'interiors'): p.innerboundaryis = [list(i.coords) for i in g.interiors]
                        p.style.polystyle.color = k_clr
                        p.style.linestyle.color = k_clr
                    pol_geom.extendeddata.newdata("is_field_section", "True")
                    pol_geom.extendeddata.newdata("parent_field_name", st.session_state.tab7_field_name)

            # 3. Rest of Field
            rest_row = export_gdf[export_gdf["type"] == "rest"]
            if not rest_row.empty:
                row = rest_row.iloc[0]
                rest_kml_obj = kml.newmultigeometry(name=st.session_state.rs_rest_name)
                rest_clr = hex_to_kml_color(st.session_state.rs_group_colors.get("Rest of Field", "#808080"), opacity="60")
                geoms = [row.geometry] if row.geometry.geom_type == 'Polygon' else list(row.geometry.geoms)
                for g in geoms:
                    p = rest_kml_obj.newpolygon(outerboundaryis=list(g.exterior.coords))
                    if hasattr(g, 'interiors'): p.innerboundaryis = [list(i.coords) for i in g.interiors]
                    p.style.polystyle.color = rest_clr
                    p.style.linestyle.color = rest_clr
                rest_kml_obj.extendeddata.newdata("is_field_section", "True")
                rest_kml_obj.extendeddata.newdata("parent_field_name", st.session_state.tab7_field_name)

            # 4. Ungrouped Strips
            ungrouped = export_gdf[(export_gdf["ExportGroup"] == "None") & (export_gdf["type"] == "strip")]
            for _, row in ungrouped.iterrows():
                p = kml.newpolygon(name=row["Name"], outerboundaryis=list(row.geometry.exterior.coords))
                p.style.polystyle.color = hex_to_kml_color("#808080", opacity="40")
                p.extendeddata.newdata("is_field_section", "True")
                p.extendeddata.newdata("parent_field_name", st.session_state.tab7_field_name)

            kmz_out = f"{st.session_state.field_name}_Trial_Strips.kmz"
            with tempfile.NamedTemporaryFile(delete=False, suffix=".kmz") as tmp:
                kml.savekmz(tmp.name)
                with open(tmp.name, "rb") as f:
                    st.download_button("Download KMZ", data=f, file_name=kmz_out, use_container_width=True)
                    
elif selected_tool == "WS Tasking Helper":

    # --- 1. STYLING ---
    st.markdown("""
        <style>
            /* Main Page Padding */
            .block-container {
                max-width: 98% !important;
                padding-left: 1rem !important;
                padding-right: 1rem !important;
                padding-bottom: 50px !important; 
            }

            /* FORCE TABLES TO EXPAND (Removes internal scrollbars) */
            [data-testid="stDataEditor"] {
                min-height: auto !important;
            }
            [data-testid="stDataEditor"] > div:first-child {
                height: auto !important;
                max-height: none !important;
            }
            
            /* Professional Header Offset */
            [id] {
                scroll-margin-top: 110px;
            }

            /* Dark Blue Button Styling for the Export Action */
            div.stDownloadButton button {
                background-color: #1E3A8A !important; 
                color: white !important;
                border: 1px solid #2563EB !important;
                border-radius: 10px !important;
                height: 3.5rem !important; 
                font-weight: 600 !important;
                width: 100% !important;
                margin-top: 20px;
            }
            
            div.stDownloadButton button:hover {
                background-color: #2563EB !important;
                border-color: #3B82F6 !important;
            }

            /* Center align the satellite headers */
            .sat-header {
                text-align: center;
                margin-top: 2rem;
                margin-bottom: 1rem;
            }
        </style>
    """, unsafe_allow_html=True)

    st.title("🛰️ WS Tasking Helper")

    # --- INFO BOX ---
    st.info("""
    **How to use this tool:**
    1. Copy and paste the tasking options directly from Slack into the text box below.
    2. Click **Generate Table** to parse and clean the options.
    3. Review each pass and select the best options using the checkboxes.
    4. Export the final tasking plan using the **Export Tasking Plan** button.
    """)

    if 'processed_df' not in st.session_state:
        st.session_state.processed_df = None
    if 'should_scroll' not in st.session_state:
        st.session_state.should_scroll = False

    import textwrap
    from datetime import datetime
    import re
    import pandas as pd
    import streamlit.components.v1 as components

    placeholder_text = textwrap.dedent("""\
        e.g.
        Drag-001
        ERT HU SZE 1 (NE->SW) -- 2026-03-28 07:41:01 UTC: Area coverage: 87.97% ONA: -13.88 Cloud forecast: -1.0% - scheduled
        ERT HU SZE 1 (SE->NW) -- 2026-03-28 07:41:01 UTC: Area coverage: 100.00% ONA: -13.88 Cloud forecast: 100% - scheduled
        ERT HU BAL (NE->SW) -- 2026-03-28 07:41:06 UTC: Area coverage: 85.74% ONA: -6.90 Cloud forecast: 100% - scheduled
    """)

    raw_text = st.text_area(
        "Paste Tasking Options Below:",
        height=250,
        placeholder=placeholder_text
    )
    
    # --- 2. DATA PARSING & CLEANING ---
    if st.button("Generate Table", type="primary"):
        if raw_text:
            sections = re.split(r'(Drag-\d+)', raw_text)
            all_data = []
            current_drag_id = "Unknown Satellite"
            
            pattern = r"((.+?)\s\((.+?)\)\s--\s(\d{4}-\d{2}-\d{2})\s(\d{2}:\d{2}:\d{2})\sUTC:\sArea coverage:\s([\d.]+)%\sONA:\s([\d.-]+)\sCloud\sforecast:\s([\d.-]+)%.*)"

            for section in sections:
                if re.match(r'Drag-\d+', section):
                    current_drag_id = section
                else:
                    matches = re.findall(pattern, section)
                    for m in matches:
                        full_line, site_raw, dir_raw, d_date, d_time, cov, ona, cloud = m
                        
                        full_string = f"{site_raw} ({dir_raw})"
                        dir_regex = r"([N|S][E|W](?:->|-)[N|S][E|W])"
                        dir_match = re.search(dir_regex, full_string)
                        suffix_match = re.search(r'(D\dD\d)', full_string)
                        
                        direction = dir_match.group(0) if dir_match else (suffix_match.group(0) if suffix_match else dir_raw)
                        site_clean = site_raw.replace("CUSTOMER ", "").replace("Core Polygon ", "").replace("Polygon ", "").replace("Updated", "")
                        if dir_match: site_clean = site_clean.replace(dir_match.group(0), "")
                        
                        all_data.append({
                            "Satellite": current_drag_id, 
                            "Site Name": site_clean.strip(), 
                            "Orbital Direction": direction,
                            "Timestamp": datetime.strptime(f"{d_date} {d_time}", "%Y-%m-%d %H:%M:%S"),
                            "Area Coverage": float(cov), 
                            "ONA": float(ona), 
                            "Cloud Coverage": float(cloud),
                            "Original Log Line": full_line.strip()
                        })

            if all_data:
                df = pd.DataFrame(all_data).sort_values(["Satellite", "Timestamp"])
                df['Time_Diff'] = df.groupby("Satellite")['Timestamp'].diff().dt.total_seconds() / 60
                
                def assign_passes(group):
                    pass_list, curr_p = [], 1
                    for diff in group['Time_Diff']:
                        if diff > 60: curr_p += 1
                        pass_list.append(f"Pass {curr_p}")
                    return pd.Series(pass_list, index=group.index)

                df['Pass Group'] = df.groupby('Satellite', group_keys=False).apply(assign_passes)
                df = df[df["Area Coverage"] >= 60].sort_values("Area Coverage", ascending=False)
                df = df.drop_duplicates(subset=["Satellite", "Pass Group", "Site Name"])
                
                df["Select"] = False
                st.session_state.processed_df = df.sort_values(["Satellite", "Timestamp"])
                st.session_state.should_scroll = True
                st.rerun()

    # --- 3. DISPLAY & SELECTION ---
    if st.session_state.processed_df is not None:
        
        if st.session_state.get("should_scroll", False):
            first_id = st.session_state.processed_df["Satellite"].iloc[0]
            components.html(f"<script>window.parent.document.getElementById('{first_id}').scrollIntoView({{behavior: 'smooth'}});</script>", height=0)
            st.session_state.should_scroll = False

        export_structure = {}

        for drag_id, drag_group in st.session_state.processed_df.groupby("Satellite"):
            # Satellite ID Anchor and Centered Header
            st.markdown(f'<div id="{drag_id}"></div>', unsafe_allow_html=True)
            st.markdown(f'<h2 class="sat-header">{drag_id}</h2>', unsafe_allow_html=True)
            export_structure[drag_id] = []

            for p_label, p_group in drag_group.groupby("Pass Group", sort=False):
                # Header and Filter Toggles
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    st.subheader(f"🎯 {p_label}")
                with col2:
                    hide_ona = st.toggle("Hide High ONA", key=f"ona_{drag_id}_{p_label}", help="Hide ONA > 13 or < -13")
                with col3:
                    hide_cloud = st.toggle("Hide High Cloud", key=f"cloud_{drag_id}_{p_label}", help="Hide Cloud > 40%")
                
                # Apply Filters
                display_df = p_group.copy()
                if hide_ona:
                    display_df = display_df[(display_df["ONA"] <= 13) & (display_df["ONA"] >= -13)]
                if hide_cloud:
                    display_df = display_df[display_df["Cloud Coverage"] <= 40]
                
                # Dynamic height to force full table expansion
                row_height, header_height = 35, 40
                calc_h = (len(display_df) * row_height) + header_height + 2

                # Define columns for proper sorting and visualization
                column_configuration = {
                    "Select": st.column_config.CheckboxColumn(required=True),
                    "Cloud Coverage": st.column_config.ProgressColumn(
                        "Cloud Risk",
                        help="Satellite cloud forecast percentage",
                        format="%f%%",
                        min_value=0,
                        max_value=100,
                    ),
                    "Area Coverage": st.column_config.NumberColumn(format="%.2f%%"),
                    "ONA": st.column_config.NumberColumn(format="%.2f"),
                }

                edited_df = st.data_editor(
                    display_df[["Select", "Site Name", "Orbital Direction", "Area Coverage", "Cloud Coverage", "ONA"]],
                    key=f"editor_{drag_id}_{p_label}",
                    hide_index=True, 
                    use_container_width=True, 
                    height=calc_h,
                    column_config=column_configuration,
                    disabled=["Site Name", "Orbital Direction", "Area Coverage", "Cloud Coverage", "ONA"]
                )

                selected_rows = edited_df[edited_df["Select"] == True]
                if not selected_rows.empty:
                    match_site = selected_rows.iloc[0]["Site Name"]
                    original_line = p_group[p_group["Site Name"] == match_site]["Original Log Line"].values[0]
                    export_structure[drag_id].append(f"{p_label}: {original_line}")
                else:
                    export_structure[drag_id].append(f"{p_label}: SKIP")

        # --- 4. FINAL EXPORT SECTION ---
        st.divider()
        
        text_output = ""
        for sat_id, pass_lines in export_structure.items():
            text_output += f"{sat_id}:\n" + "\n".join(pass_lines) + "\n\n"

        st.download_button(
            label="🚀 Export Tasking Plan (.txt)",
            data=text_output,
            file_name=f"ws_plan_{datetime.now().strftime('%Y%m%d_%H%M')}.txt",
            mime="text/plain",
            type="primary",
            use_container_width=True
        )

if selected_tool == "Satellite Insights Map":
    # 1. UI Refresh via CSS
    st.markdown("""
        <style>
            .block-container {
                max-width: 98% !important;
                padding-left: 1rem !important;
                padding-right: 1rem !important;
            }
            .map-header {
                font-size: 32px;
                font-weight: 700;
                margin-bottom: 20px;
                display: flex;
                align-items: center;
                gap: 12px;
            }
            .stMultiSelect div[role="listbox"] {
                overflow-wrap: normal !important;
            }
            .stMultiSelect span {
                font-size: 13px !important;
            }
            [data-testid="stVerticalBlock"] > div:has(div.stMultiSelect) {
                margin-bottom: -10px;
            }
            /* Styling for the stats row below the map */
            .stats-row-container {
                padding-top: 20px;
                padding-bottom: 10px;
            }
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="map-header">🛰️ Satellite Insights Map</div>', unsafe_allow_html=True)

    # 2. Functional Data Loading & Preprocessing
    try:
        df = pd.read_csv("insights_num.csv")
        df.columns = df.columns.str.strip()
        df["Recent Date"] = pd.to_datetime(df["Recent Date"], errors='coerce')
        
        def categorize_recency(d):
            if pd.isna(d): return "No Images"
            days = (datetime.now() - d).days
            if days > 21: return "> 21 Days Old"
            if 10 <= days <= 21: return "10 - 21 Days Old"
            return "< 10 Days (Recent)"
        
        df["Recency Category"] = df["Recent Date"].apply(categorize_recency)
        
        poly_df = pd.read_csv("polygons.csv")
        poly_df.columns = poly_df.columns.str.strip()
    except Exception as e:
        st.error(f"Error loading CSV files: {e}")
        st.stop()

    # 3. Sidebar for Filters ONLY
    main_col, side_panel = st.columns([4, 1], gap="large")

    with side_panel:
        st.write("### Filters")
        
        # ISO Filter
        if "ISO" in df.columns:
            iso_options = sorted(df["ISO"].dropna().unique().tolist())
            selected_iso = st.multiselect(
                "Country (ISO):",
                options=iso_options,
                default=iso_options
            )
        else:
            selected_iso = []

        selected_insights = st.multiselect(
            "Insights Count:",
            options=sorted(df["Insights"].unique()),
            default=sorted(df["Insights"].unique())
        )
        
        recency_options = ["< 10 Days (Recent)", "10 - 21 Days Old", "> 21 Days Old", "No Images"]
        selected_recency = st.multiselect(
            "Recency Status:",
            options=recency_options,
            default=recency_options
        )
        
        # Apply Filter Logic
        mask = (
            (df["Insights"].isin(selected_insights)) & 
            (df["Recency Category"].isin(selected_recency))
        )
        if selected_iso:
            mask = mask & (df["ISO"].isin(selected_iso))
            
        filtered_df = df[mask]

        st.markdown("---")
        st.write("### Layers")
        show_core = st.toggle("UK Core Polygons", value=False)
        show_expanded = st.toggle("UK Expanded Polygons", value=False)
        
        st.caption(f"Current Date: {datetime.now().strftime('%Y-%m-%d')}")

    with main_col:
        # Map Mode Buttons
        c1, c2, _ = st.columns([1, 1, 2.5])
        with c1:
            is_freq = st.button("📊 Frequency Map", use_container_width=True, type="primary" if "map_mode" not in st.session_state or st.session_state.map_mode == "Frequency" else "secondary")
            if is_freq: st.session_state.map_mode = "Frequency"
        with c2:
            is_rec = st.button("🕒 Recency Map", use_container_width=True, type="primary" if "map_mode" in st.session_state and st.session_state.map_mode == "Recency" else "secondary")
            if is_rec: st.session_state.map_mode = "Recency"
        
        map_mode = st.session_state.get("map_mode", "Frequency")

        # 4. Create the Map
        if not filtered_df.empty:
            avg_lat = filtered_df["Latitude"].mean()
            avg_lon = filtered_df["Longitude"].mean()
            m = folium.Map(location=[avg_lat, avg_lon], zoom_start=6, control_scale=True)

            # --- A. Draw Tasking Polygons ---
            for _, p_row in poly_df.iterrows():
                p_name = str(p_row['Polygon'])
                is_core = p_name.startswith("Core")
                is_expanded = p_name.startswith("Expanded")
                
                if (is_core and show_core) or (is_expanded and show_expanded):
                    try:
                        geom = shapely.wkt.loads(p_row['Geometry'])
                        color = "#1f77b4" if is_core else "#9467bd"
                        dash = "1" if is_core else "5, 5"
                        coords = [[p[1], p[0]] for p in geom.exterior.coords]
                        folium.Polygon(
                            locations=coords, popup=p_name, tooltip=p_name,
                            color=color, weight=2, fill=True, fill_opacity=0.1, dash_array=dash
                        ).add_to(m)
                    except: continue

            # --- B. Draw Farm Points ---
            def get_color(row, mode):
                if mode == "Frequency":
                    val = row["Insights"]
                    if val == 0: return "#8B0000"
                    if val == 1: return "#FFD700"
                    if val == 2: return "#76D100"
                    if val == 3: return "#32CD32"
                    return "#008000"
                else:
                    cat = row["Recency Category"]
                    if cat == "No Images": return "#8B0000"
                    if cat == "> 21 Days Old": return "#FFD700"
                    if cat == "10 - 21 Days Old": return "#76D100"
                    return "#32CD32"

            for _, row in filtered_df.iterrows():
                point_color = get_color(row, map_mode)
                date_str = row['Recent Date'].strftime('%Y-%m-%d') if pd.notnull(row['Recent Date']) else "No Data"
                combined_tooltip = f"Farm {row['Farm ID']} | Insights: {row['Insights']} | Last: {date_str}"
                
                folium.CircleMarker(
                    location=[row["Latitude"], row["Longitude"]],
                    radius=5.5, 
                    color="white",
                    weight=0.7,
                    fill=True,
                    fill_color=point_color,
                    fill_opacity=0.9,
                    popup=folium.Popup(f"<b>Farm ID:</b> {row['Farm ID']}<br><b>Insights:</b> {row['Insights']}<br><b>Latest Image:</b> {date_str}", max_width=200),
                    tooltip=combined_tooltip
                ).add_to(m)

            # 6. Legend
            if map_mode == "Frequency":
                legend_title, labels = "Insights Progress", [
                    ("#8B0000", "0 (None)"), ("#FFD700", "1 Insight"), ("#76D100", "2 Insights"),
                    ("#32CD32", "3 Insights"), ("#008000", "4+ Insights")
                ]
            else:
                legend_title, labels = "Image Recency", [
                    ("#8B0000", "No Images"), ("#FFD700", "> 21 Days Old"),
                    ("#76D100", "10 - 21 Days Old"), ("#32CD32", "< 10 Days (Recent)")
                ]

            legend_items = "".join([f'<i style="background: {c}; width: 10px; height: 10px; float: left; margin-right: 10px; border-radius: 50%;"></i> {l}<br>' for c, l in labels])
            legend_html = f"""
                 <div style="position: fixed; bottom: 50px; left: 50px; width: 165px; height: auto; 
                 background-color: rgba(30, 30, 30, 0.9); border: 1px solid #555; z-index: 9999; font-size: 11px;
                 padding: 10px; border-radius: 8px; color: #FFFFFF; font-family: sans-serif; box-shadow: 2px 2px 10px rgba(0,0,0,0.5);">
                 <b style="display: block; margin-bottom: 8px;">{legend_title}</b>{legend_items}</div>
            """
            m.get_root().html.add_child(folium.Element(legend_html))

            st_folium(m, use_container_width=True, height=750)
        else:
            st.warning("No data matches the selected filters.")

        # --- 7. NEW: STATS SECTION BELOW MAP ---
        st.write("### 📊 Stats for Farms Currently Displayed")
        if not filtered_df.empty:
            # Data Calculations
            total_farms = len(filtered_df)
            avg_images = filtered_df["Insights"].mean()
            
            max_img_val = filtered_df["Insights"].max()
            max_img_ids = filtered_df[filtered_df["Insights"] == max_img_val]["Farm ID"].tolist()
            
            min_img_val = filtered_df["Insights"].min()
            min_img_ids = filtered_df[filtered_df["Insights"] == min_img_val]["Farm ID"].tolist()
            
            dated_df = filtered_df.dropna(subset=["Recent Date"])
            if not dated_df.empty:
                mrd = dated_df["Recent Date"].max()
                mrd_ids = dated_df[dated_df["Recent Date"] == mrd]["Farm ID"].tolist()
                lrd = dated_df["Recent Date"].min()
                lrd_ids = dated_df[dated_df["Recent Date"] == lrd]["Farm ID"].tolist()
                
                mrd_str, lrd_str = mrd.strftime('%d-%m-%Y'), lrd.strftime('%d-%m-%Y')
            else:
                mrd_str = lrd_str = "N/A"
                mrd_ids = lrd_ids = []

            # 5-Column Stats Ribbon
            s1, s2, s3, s4, s5 = st.columns(5)
            
            with s1:
                st.metric("Total Farms Displayed", total_farms)
            with s2:
                st.metric("Avg Images", f"{avg_images:.2f}")
            with s3:
                st.metric("Max Images", f"{max_img_val}", 
                          help=f"Farm IDs: {', '.join(map(str, max_img_ids[:100]))}")
            with s4:
                st.metric("Min Images", f"{min_img_val}", 
                          help=f"Farm IDs: {', '.join(map(str, min_img_ids[:100]))}")
            with s5:
                st.metric("Newest Image", mrd_str)

        else:
            st.info("Apply filters to view summary statistics.")

    # Data table footer
    st.divider()
    with st.expander("📂 View Full Data Table"):
        st.dataframe(filtered_df, use_container_width=True, hide_index=True)
