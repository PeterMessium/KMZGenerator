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
nav_button("Image Frequency Map", "Polygon Frequency Map", task_expander)
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
            /* This ensures only buttons in the MAIN area are affected, leaving the sidebar alone */
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

            /* Keep your blue download button style but scope it to main area */
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
    
    # --- 2. GROUP STATE ---
    if "group_names" not in st.session_state:
        st.session_state.group_names = ["Messium", "Farm Standard", "Low", "High"]

    uploaded_files = st.file_uploader(
        "Upload KMZ",
        type=["kmz"],
        accept_multiple_files=True,
        key="tab4_upload"
    )

    if uploaded_files:
        # --- 3. PROCESSING DATA ---
        datasets = [[f] for f in uploaded_files]
        processed_datasets = []

        for i, dataset_files in enumerate(datasets):
            try:
                gdf = load_vector_file(dataset_files)
                if gdf is not None and not gdf.empty:
                    potential_cols = ["Name", "name", "Field", "field", "Label", "label", "ID"]
                    target_col = next((c for c in potential_cols if c in gdf.columns), None)
                    
                    if target_col:
                        gdf["Name"] = gdf[target_col].astype(str).replace(["nan", "None", ""], None)
                        gdf["Name"] = [n if (n and n.strip() != "None") else f"Field_{j}" for j, n in enumerate(gdf["Name"])]
                    else:
                        gdf["Name"] = [f"Field_{j}" for j in range(len(gdf))]
                    
                    if gdf.crs != "EPSG:4326":
                        gdf = gdf.to_crs(epsg=4326)

                    gdf = infer_hierarchy(gdf, name_col="Name")
                    processed_datasets.append(gdf)
            except Exception as e:
                st.error(f"Error loading KMZ {i+1}: {e}")

        dropdown_options = ["None"] + [g for g in st.session_state.group_names if g]

        # --- 4. LAYOUT ---
        col_input, col_map = st.columns([1.5, 1.5]) 
        all_mappings = [] 

        with col_input:
            st.markdown("#### Polygon Settings")
            container = st.container(height=600) 
            with container:
                for d_idx, gdf in enumerate(processed_datasets):
                    # "Dataset X" label removed as requested
                    ds_map = {"top": {}, "sub": {}}
                    top_levels = gdf[gdf["is_top_level"]]
                    
                    for _, row in top_levels.iterrows():
                        orig_name = row["Name"]
                        # Main Field Input
                        new_name = st.text_input(f"Field: {orig_name}", value=orig_name, key=f"n_{d_idx}_t_{orig_name}")
                        ds_map["top"][orig_name] = {"name": new_name, "included": True}
                        
                        # --- HEADERS MOVED UNDER FIELD ---
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
            if processed_datasets:
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
                            color = "#1f77b4" if is_top else "#ff7f0e"
                            label = m_data["name"] + (f" ({m_data['group']})" if not is_top and m_data['group'] != "None" else "")
                            geom = row.geometry
                            polys = [geom] if geom.geom_type == 'Polygon' else list(geom.geoms)
                            for p in polys:
                                folium.Polygon(locations=[[pt[1], pt[0]] for pt in p.exterior.coords], color=color, fill=True, fill_opacity=0.3, weight=3 if is_top else 2, tooltip=label).add_to(m)
                st_folium(m, width="100%", height=550)

        # --- 5. GROUP MANAGER ---
        st.divider()
        st.markdown("#### 🛠️ Group Manager")
        current_df = pd.DataFrame([{"Group Name": g} for g in st.session_state.group_names])
        col_tbl, col_btn = st.columns([4, 1])
        with col_tbl:
            edited_df = st.data_editor(current_df, num_rows="dynamic", use_container_width=True, key="group_editor")
        with col_btn:
            if st.button("Update Groups", use_container_width=True):
                st.session_state.group_names = [g for g in edited_df["Group Name"].dropna().unique().tolist() if str(g).strip() != ""]
                st.rerun()

        # --- 6. EXPORT LOGIC ---
        st.divider()
        if st.button("Generate Subsection-Enabled KMZ", use_container_width=True, type="primary"):
            output_generated = False
            
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
                        row_copy["is_field_section"] = "True" if not is_top else ""
                        
                        if not is_top:
                            parent_orig_name = row["parent_field_name"]
                            row_copy["parent_field_name"] = all_mappings[d_idx]["top"].get(parent_orig_name, {}).get("name", parent_orig_name)
                        else:
                            row_copy["parent_field_name"] = ""

                        row_copy["ExportGroup"] = m_data.get("group", "None")
                        export_rows.append(row_copy)
                
                if export_rows:
                    working_gdf = gpd.GeoDataFrame(export_rows, crs=gdf.crs)
                    ungrouped = working_gdf[working_gdf["ExportGroup"] == "None"].reset_index(drop=True)
                    grouped = working_gdf[working_gdf["ExportGroup"] != "None"].reset_index(drop=True)
                    
                    final_gdf_list = []
                    if not ungrouped.empty: final_gdf_list.append(ungrouped)

                    if not grouped.empty:
                        dissolved = grouped.dissolve(by="ExportGroup", aggfunc={'Name': 'first', 'is_field_section': 'first', 'parent_field_name': 'first'})
                        dissolved = dissolved.reset_index()
                        dissolved["Name"] = dissolved["ExportGroup"] 
                        dissolved = dissolved.drop(columns=["ExportGroup"]).reset_index(drop=True)
                        final_gdf_list.append(dissolved)
                    
                    export_gdf = pd.concat(final_gdf_list, ignore_index=True).reset_index(drop=True)
                    
                    for _, row in export_gdf.iterrows():
                        geom = row.geometry
                        if geom.geom_type == 'Polygon':
                            pol = kml.newpolygon(name=row["Name"], outerboundaryis=list(geom.exterior.coords))
                        else:
                            pol = kml.newmultigeometry(name=row["Name"])
                            for part in geom.geoms:
                                pol.newpolygon(outerboundaryis=list(part.exterior.coords))
                        
                        pol.extendeddata.newdata("is_field_section", str(row["is_field_section"]))
                        pol.extendeddata.newdata("parent_field_name", str(row["parent_field_name"]))

                    kmz_path = f"export_{d_idx+1}.kmz"
                    kml.savekmz(kmz_path)
                    
                    with open(kmz_path, "rb") as f:
                        st.download_button(
                            label="📥 Download Subsection-Enabled KMZ", 
                            data=f, 
                            file_name=kmz_path,
                            key=f"dl_kmz_{d_idx}",
                            use_container_width=True
                        )
                    
                    output_generated = True

            if not output_generated:
                st.warning("No polygons selected.")


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



elif selected_tool == "Polygon Frequency Map":

    st.subheader("Visualise Polygon Image Frequency with Layers and Scaled Colours")

    try:
        # Load CSV from local folder
        csv_path = "polygon_frequency.csv"
        df = pd.read_csv(csv_path)

        required_cols = {"Polygon", "Num of Images", "Geometry"}
        if not required_cols.issubset(df.columns):
            st.error(f"CSV must contain columns: {', '.join(required_cols)}")
        else:
            st.success(f"CSV loaded successfully with {len(df)} polygons.")

            import shapely.wkt
            import branca.colormap as cm

            # Convert WKT to shapely geometries
            df["geometry"] = df["Geometry"].apply(shapely.wkt.loads)
            gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")

            # Compute mean lat/lon for map center
            centroids = gdf.geometry.centroid
            mean_lat = centroids.y.mean()
            mean_lon = centroids.x.mean()

            # Determine max number of images for scaling
            max_images = gdf["Num of Images"].max()

            # Linear colour scale from red (0) → yellow → green (max)
            colormap = cm.LinearColormap(["red", "yellow", "green"], vmin=0, vmax=max_images)
            colormap.caption = "Number of Images"

            # Initialise folium map
            m = folium.Map(location=[mean_lat, mean_lon], zoom_start=6)

            # Define the four layer groups
            layers = {
                "Core NE->SW": folium.FeatureGroup(name="Core NE->SW", show=True),
                "Core SE->NW": folium.FeatureGroup(name="Core SE->NW", show=True),
                "Expanded NE->SW": folium.FeatureGroup(name="Expanded NE->SW", show=True),
                "Expanded SE->NW": folium.FeatureGroup(name="Expanded SE->NW", show=True)
            }

            # Add polygons to the correct layer
            for _, row in gdf.iterrows():
                geom = row.geometry
                polygons = [geom] if geom.geom_type == "Polygon" else geom.geoms

                # Robust layer classification: Expanded takes priority over Core
                if "Expanded" in row["Polygon"]:
                    if "NE->SW" in row["Polygon"]:
                        layer_name = "Expanded NE->SW"
                    elif "SE->NW" in row["Polygon"]:
                        layer_name = "Expanded SE->NW"
                    else:
                        layer_name = None
                elif "Core" in row["Polygon"]:
                    if "NE->SW" in row["Polygon"]:
                        layer_name = "Core NE->SW"
                    elif "SE->NW" in row["Polygon"]:
                        layer_name = "Core SE->NW"
                    else:
                        layer_name = None
                else:
                    layer_name = None  # skip polygons that don't match any category

                if layer_name is None:
                    continue

                for poly in polygons:
                    coords = [[y, x] for x, y, *rest in poly.exterior.coords]  # ignore Z
                    folium.Polygon(
                        locations=coords,
                        color=colormap(row["Num of Images"]),
                        weight=3,
                        fill=True,
                        fill_opacity=0.5,
                        popup=f"{row['Polygon']}<br>Images: {row['Num of Images']}",
                        tooltip=row["Polygon"]
                    ).add_to(layers[layer_name])

            # Add all layers to the map
            for lg in layers.values():
                lg.add_to(m)

            # Add layer control and colour legend
            folium.LayerControl().add_to(m)
            colormap.add_to(m)

            st_folium(m, width="100%", height=600)

    except FileNotFoundError:
        st.error(f"File 'polygon_frequency.csv' not found in the current folder.")
    except Exception as e:
        st.error(f"Error processing CSV: {e}")


elif selected_tool == "Repeated Strip Generator":

    # --- 1. STYLING ---
    # We target 'button:disabled' specifically to ensure the "gray out" effect.
    st.markdown("""
        <style>
            .block-container { max-width: 98% !important; padding-left: 1rem !important; padding-right: 1rem !important; padding-top: 2rem !important; }
            
            /* Primary Button Style (Red) */
            div.stButton > button[kind="primary"] { 
                background-color: #ef4444 !important; 
                color: white !important; 
                border: none !important; 
                border-radius: 10px !important; 
                font-weight: 600 !important; 
            }

            /* Gray out the button when disabled */
            div.stButton > button:disabled {
                background-color: #374151 !important; /* Dark slate gray */
                color: #9CA3AF !important; /* Muted light gray text */
                border: 1px solid #4B5563 !important;
                cursor: not-allowed !important;
                opacity: 0.6 !important;
            }

            div.stButton > button[kind="secondary"] { border-radius: 10px !important; }
            div.stDownloadButton button { background-color: #1E3A8A !important; color: white !important; border: 1px solid #2563EB !important; border-radius: 10px !important; font-weight: 600 !important; width: 100% !important; }
        </style>
    """, unsafe_allow_html=True)

    # --- 2. SESSION STATE ---
    keys = {
        'field_name': "", 'strip_w': "", 'headland_b': 0.0,
        'f_t_lon': "", 'f_t_lat': "", 'f_b_lon': "", 'f_b_lat': "",
        'l_t_lon': "", 'l_t_lat': "", 'l_b_lon': "", 'l_b_lat': "",
        'tab7_result_gdf': None, 'tab7_field_name': ""
    }
    for key, val in keys.items():
        if key not in st.session_state: st.session_state[key] = val

    # --- 3. HEADER ---
    st.subheader("Repeated Strip Generator")
    st.markdown("For alternating strip trials.")

    # --- 4. TOGGLEABLE INFO BOX ---
    with st.expander("📖 Click here to learn how to use this tool"):
        info_col1, info_col2 = st.columns([1, 1]) 
        with info_col1:
            st.markdown("""
                ### How to Use
                **Method 1: Manual Entry**
                1. Upload your field boundary KMZ/SHP.
                2. Manually type the Top and Bottom coordinates for your **First** and **Last** strips.
                
                **Method 2: Auto-Fill via KMZ**
                1. Create a KMZ containing your field polygon and **4 Placemarks**.
                2. Name the placemarks exactly as follows:
                    * `First - Top`, `First - Bottom`, `Last - Top`, `Last - Bottom`
                3. Upon upload, coordinates and field name auto-populate.
                
                *Note: Tramline Width (m) and Headland Buffer (m) must always be entered manually.*
            """)
        with info_col2:
            try:
                st.image("help.png", caption="KMZ Placemark Naming Scheme", use_container_width=True)
            except:
                st.warning("help.png not found in script directory.")

    st.divider()

    # --- 5. UI LAYOUT ---
    col_files, col_coords = st.columns([1, 1.5])
    with col_files:
        st.markdown("### 1. Field Boundary")
        uploaded_field = st.file_uploader("Upload KMZ/SHP", type=["kmz", "shp", "kml"], key="tab7_upload_input")
        
        if uploaded_field:
            try:
                raw_gdf = load_vector_file([uploaded_field])
                if raw_gdf is not None:
                    poly_features = raw_gdf[raw_gdf.geometry.type == 'Polygon']
                    if not poly_features.empty and st.session_state.field_name == "":
                        st.session_state.field_name = str(poly_features.iloc[0].get('Name', 'Field')).strip()
                    
                    point_features = raw_gdf[raw_gdf.geometry.type == 'Point']
                    name_map = {
                        "First - Top": ("f_t_lat", "f_t_lon"), 
                        "First - Bottom": ("f_b_lat", "f_b_lon"), 
                        "Last - Top": ("l_t_lat", "l_t_lon"), 
                        "Last - Bottom": ("l_b_lat", "l_b_lon")
                    }
                    for _, row in point_features.iterrows():
                        p_name = str(row.get('Name', '')).strip()
                        if p_name in name_map:
                            lat_key, lon_key = name_map[p_name]
                            st.session_state[lat_key], st.session_state[lon_key] = f"{row.geometry.y:.6f}", f"{row.geometry.x:.6f}"
            except Exception as e: st.error(f"Upload error: {e}")
            
        st.text_input("Field Name", key="field_name")
        st.text_input("Tramline Width (m)", key="strip_w")
        st.number_input("Headland Buffer (m)", min_value=0.0, step=1.0, key="headland_b")

    with col_coords:
        st.markdown("### 2. Alignment Coordinates")
        with st.container(border=True):
            st.markdown("**First Strip**")
            r1c1, r1c2 = st.columns(2)
            r1c1.text_input("Top Latitude", key="f_t_lat")
            r1c2.text_input("Top Longitude", key="f_t_lon")
            r2c1, r2c2 = st.columns(2)
            r2c1.text_input("Bottom Latitude", key="f_b_lat")
            r2c2.text_input("Bottom Longitude", key="f_b_lon")
        
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("**Last Strip**")
            r3c1, r3c2 = st.columns(2)
            r3c1.text_input("Top Latitude", key="l_t_lat")
            r3c2.text_input("Top Longitude", key="l_t_lon")
            r4c1, r4c2 = st.columns(2)
            r4c1.text_input("Bottom Latitude", key="l_b_lat")
            r4c2.text_input("Bottom Longitude", key="l_b_lon")


    # --- 6. ACTIONS ---
    # Comprehensive check for all required inputs
    required_keys = [
        'field_name', 'strip_w', 
        'f_t_lat', 'f_t_lon', 'f_b_lat', 'f_b_lon', 
        'l_t_lat', 'l_t_lon', 'l_b_lat', 'l_b_lon'
    ]
    
    # Button is only clickable if all strings are non-empty AND a file is uploaded
    all_filled = all(str(st.session_state.get(k, "")).strip() != "" for k in required_keys) and uploaded_field is not None

    if st.button("Generate", type="primary", use_container_width=True, disabled=not all_filled):
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
                
                field_utm_clipped = field_utm_full.buffer(-h_buffer) if h_buffer > 0 else field_utm_full
                
                p1_t = Point(to_utm(float(st.session_state.f_t_lon), float(st.session_state.f_t_lat)))
                p1_b = Point(to_utm(float(st.session_state.f_b_lon), float(st.session_state.f_b_lat)))
                p2_t = Point(to_utm(float(st.session_state.l_t_lon), float(st.session_state.l_t_lat)))
                
                angle_v = math.atan2(p1_b.y - p1_t.y, p1_b.x - p1_t.x)
                angle_h = angle_v + (math.pi / 2)
                
                total_dist = ((p2_t.x - p1_t.x) * math.cos(angle_h)) + ((p2_t.y - p1_t.y) * math.sin(angle_h))
                num_strips = int(round(abs(total_dist) / strip_width)) + 1
                direction = 1 if total_dist > 0 else -1
                
                strips_list = []
                c1_utm = LineString([p1_t, p1_b]).centroid
                for i in range(num_strips):
                    offset = i * strip_width * direction
                    curr_cx, curr_cy = c1_utm.x + offset * math.cos(angle_h), c1_utm.y + offset * math.sin(angle_h)
                    
                    ext_len = 10000 
                    p_u = (curr_cx - ext_len * math.cos(angle_v), curr_cy - ext_len * math.sin(angle_v))
                    p_d = (curr_cx + ext_len * math.cos(angle_v), curr_cy + ext_len * math.sin(angle_v))
                    
                    strip_geom = LineString([p_u, p_d]).buffer(strip_width/2, cap_style=2, join_style=2)
                    clipped = strip_geom.intersection(field_utm_clipped)
                    
                    if not clipped.is_empty and clipped.area > (strip_width * 80):
                        strips_list.append({
                            "Name": f"{st.session_state.field_name} - Strip {i+1}",
                            "geometry": clipped,
                            "parent_field": st.session_state.field_name,
                            "width_m": strip_width,
                            "is_field_section": "True"
                        })
                
                if strips_list:
                    st.session_state.tab7_result_gdf = gpd.GeoDataFrame(strips_list, crs=utm_crs).to_crs("EPSG:4326")
                    st.session_state.tab7_field_name = st.session_state.field_name
                    st.success(f"Generated {len(strips_list)} strips.")
                else:
                    st.error("No strips met the length requirements.")
        except Exception as e: st.error(f"Error: {e}")

    # --- 7. MAP & DOWNLOAD ---
    if st.session_state.tab7_result_gdf is not None:
        res_gdf = st.session_state.tab7_result_gdf
        field_bg_gdf = load_vector_file([uploaded_field])
        field_bg_gdf = field_bg_gdf[field_bg_gdf.geometry.type == 'Polygon']
        st.divider()
        
        m = folium.Map(location=[res_gdf.geometry.centroid.y.mean(), res_gdf.geometry.centroid.x.mean()], zoom_start=15)
        folium.GeoJson(field_bg_gdf, style_function=lambda x: {'color': 'red', 'fillOpacity': 0, 'weight': 3}).add_to(m)
        folium.GeoJson(res_gdf, tooltip=folium.GeoJsonTooltip(fields=["Name"]), style_function=lambda x: {'color': 'green', 'fillOpacity': 0.3, 'weight': 1}).add_to(m)
        st_folium(m, width="100%", height=600, key="tab7_map_final")

        kml = simplekml.Kml()
        # Add Boundary
        for _, f_row in field_bg_gdf.iterrows():
            f_polys = [f_row.geometry] if f_row.geometry.geom_type == 'Polygon' else list(f_row.geometry.geoms)
            for p in f_polys:
                pol = kml.newpolygon(name=f"{st.session_state.tab7_field_name}")
                pol.outerboundaryis = list(p.exterior.coords)
                pol.style.polystyle.color = simplekml.Color.changealphaint(20, simplekml.Color.red)
                pol.style.linestyle.color = simplekml.Color.red
                pol.style.linestyle.width = 4
                pol.extendeddata.newdata("is_field_section", "False")

        # Add Strips
        for _, s_row in res_gdf.iterrows():
            s_polys = [s_row.geometry] if s_row.geometry.geom_type == 'Polygon' else list(s_row.geometry.geoms)
            for p in s_polys:
                pol = kml.newpolygon(name=s_row["Name"])
                pol.outerboundaryis = list(p.exterior.coords)
                pol.style.polystyle.color = simplekml.Color.changealphaint(100, simplekml.Color.green)
                pol.extendeddata.newdata("is_field_section", "True")
                pol.extendeddata.newdata("parent_field", str(s_row["parent_field"]))
                pol.extendeddata.newdata("width_m", str(s_row["width_m"]))

        with tempfile.NamedTemporaryFile(delete=False, suffix=".kmz") as tmp:
            kml.savekmz(tmp.name)
            with open(tmp.name, "rb") as f:
                st.download_button(label=f"Download", data=f, file_name=f"{st.session_state.tab7_field_name}_strips.kmz", type="primary", use_container_width=True)


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
                # Target Icon for Passes
                st.subheader(f"🎯 {p_label}")
                
                display_df = p_group.copy()
                display_df["Cloud Risk"] = display_df["Cloud Coverage"].apply(
                    lambda v: f"{'🟢' if v < 20 else '🟡' if v <= 80 else '🔴'} {'█' * int(v/10)}{'░' * (10-int(v/10))} {v}%"
                )
                
                # Dynamic height to force full table expansion
                row_height, header_height = 35, 40
                calc_h = (len(display_df) * row_height) + header_height + 2

                edited_df = st.data_editor(
                    display_df[["Select", "Site Name", "Orbital Direction", "Area Coverage", "Cloud Risk", "ONA"]],
                    key=f"editor_{drag_id}_{p_label}",
                    hide_index=True, 
                    use_container_width=True, 
                    height=calc_h,
                    disabled=["Site Name", "Orbital Direction", "Area Coverage", "Cloud Risk", "ONA"]
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
