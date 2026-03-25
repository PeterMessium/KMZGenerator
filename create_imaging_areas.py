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

# ----------------------------
# App config
# ----------------------------
st.set_page_config(page_title="Operations Team Tooling", layout="wide")
st.title("Operations Team Tooling")

# ----------------------------
# Tabs
# ----------------------------
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "Imaging Polygon Generator",
    "Shapefile → KMZ Converter",
    "OpenCosmos Tasking AOI Generator",
    "Subsection Generator",
    "Duplicate KMZs",
    "Polygon Frequency Map",
    "Repeated Strip Generator"
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

# ----------------------------
# Tab 2: Shapefile → KMZ Converter (Improved)
# ----------------------------
with tab2:

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

  
# ----------------------------
# Tab 3: OC Tasking AOI Generator
# ----------------------------
with tab3:
    st.subheader("OpenCosmos Tasking AOI Generator")
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


# ----------------------------
# Shared Helper Functions
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

# [Note: Tab 1, 2, and 3 logic remains as per original script]

# ----------------------------
# Tab 4: Subsection Generator
# ----------------------------
with tab4:
    st.subheader("Subsection Generator")
    
    uploaded_files = st.file_uploader(
        "Upload KMZ or Shapefile sets",
        type=["kmz", "shp", "shx", "dbf", "prj"],
        accept_multiple_files=True,
        key="tab4_upload"
    )

    if uploaded_files:
        # 1. Processing Data
        kmz_files = [f for f in uploaded_files if f.name.lower().endswith(".kmz")]
        shp_files = [f for f in uploaded_files if not f.name.lower().endswith(".kmz")]
        datasets = [[kmz] for kmz in kmz_files]
        if shp_files: datasets.append(shp_files)

        processed_datasets = []
        for i, dataset_files in enumerate(datasets):
            try:
                gdf = load_vector_file(dataset_files)
                if gdf is not None and not gdf.empty:
                    if "Field" in gdf.columns:
                        gdf["Name"] = gdf["Field"].astype(str).replace("nan", "")
                        gdf["Name"] = [n if n.strip() else f"Field_{j}" for j, n in enumerate(gdf["Name"])]
                    else:
                        gdf["Name"] = [f"Field_{j}" for j in range(len(gdf))]
                    
                    gdf = infer_hierarchy(gdf, name_col="Name")
                    processed_datasets.append(gdf)
            except Exception as e:
                st.error(f"Error loading dataset {i+1}: {e}")

        # Side-by-side Layout
        col_input, col_map = st.columns([1.2, 1.5])

        all_mappings = [] 

        with col_input:
            st.markdown("#### Polygon Settings")
            container = st.container(height=650) 
            
            with container:
                for d_idx, gdf in enumerate(processed_datasets):
                    st.markdown(f"**Dataset {d_idx + 1}**")
                    ds_map = {"top": {}, "sub": {}}
                    
                    top_levels = gdf[gdf["is_top_level"]]
                    for _, row in top_levels.iterrows():
                        orig_name = row["Name"]
                        name_key = f"name_{d_idx}_top_{orig_name}"
                        new_name = st.text_input(f"Field: {orig_name}", value=orig_name, key=name_key)
                        
                        ds_map["top"][orig_name] = {"name": new_name, "included": True}
                        
                        subs = gdf[gdf["parent_field_name"] == orig_name]
                        for s_idx, (s_row_idx, s_row) in enumerate(subs.iterrows()):
                            sc1, sc2, sc3 = st.columns([0.2, 2.5, 1.3])
                            sub_inc_key = f"inc_{d_idx}_sub_{s_row_idx}"
                            sub_name_key = f"name_{d_idx}_sub_{s_row_idx}"
                            
                            with sc1:
                                st.markdown(f"↳")
                            with sc2:
                                s_name = st.text_input(f"Sub", value=f"{new_name} - S{s_idx+1}", key=sub_name_key, label_visibility="collapsed")
                            with sc3:
                                s_inc = st.checkbox("Included", value=True, key=sub_inc_key)
                                
                            ds_map["sub"][s_row_idx] = {"name": s_name, "included": s_inc}
                        st.divider()
                    
                    all_mappings.append(ds_map)

        with col_map:
            st.markdown("#### Map Preview")
            if processed_datasets:
                first_gdf = processed_datasets[0]
                m = folium.Map(
                    location=[first_gdf.geometry.centroid.y.mean(), first_gdf.geometry.centroid.x.mean()], 
                    zoom_start=16
                )
                
                for d_idx, gdf in enumerate(processed_datasets):
                    for idx, row in gdf.iterrows():
                        if row["is_top_level"]:
                            m_data = all_mappings[d_idx]["top"].get(row["Name"])
                            color = "#1f77b4" 
                        else:
                            m_data = all_mappings[d_idx]["sub"].get(idx)
                            color = "#ff7f0e" 
                        
                        if m_data and m_data["included"]:
                            coords = [[y, x] for x, y in row.geometry.exterior.coords]
                            folium.Polygon(
                                locations=coords,
                                color=color,
                                weight=3 if row["is_top_level"] else 2,
                                fill=True,
                                fill_opacity=0.3,
                                popup=m_data["name"],
                                tooltip=m_data["name"]
                            ).add_to(m)
                
                st_folium(m, width="100%", height=600)

        # ----------------------------
        # Export Logic (Fixed for Google My Maps Attributes)
        # ----------------------------
        st.divider()
        if st.button("Export Enriched KMZ", use_container_width=True, type="primary"):
            output_generated = False
            for d_idx, gdf in enumerate(processed_datasets):
                kml = simplekml.Kml()
                
                # Define Schema to ensure attributes appear as columns in My Maps
                schema = kml.newschema(name=f"Schema_DS_{d_idx+1}")
                schema.newsimplefield(name="is_top_level", type="string")
                schema.newsimplefield(name="parent_field_name", type="string")
                
                export_count = 0
                for idx, row in gdf.iterrows():
                    # Check mapping for inclusion
                    if row["is_top_level"]:
                        m_data = all_mappings[d_idx]["top"].get(row["Name"])
                    else:
                        m_data = all_mappings[d_idx]["sub"].get(idx)
                    
                    if m_data and m_data["included"]:
                        # Handle Polygon vs MultiPolygon
                        geom = row.geometry
                        if isinstance(geom, Polygon):
                            poly = kml.newpolygon(name=m_data["name"])
                            poly.outerboundaryis = list(geom.exterior.coords)
                        elif isinstance(geom, MultiPolygon):
                            # My Maps handles MultiPolygons better when grouped in a folder or as a MultiGeometry
                            poly = kml.newmultigeometry(name=m_data["name"])
                            for p in geom.geoms:
                                poly.newpolygon(outerboundaryis=list(p.exterior.coords))
                        else:
                            continue

                        # Apply Schema Data for the attributes
                        poly.extendeddata.schemadata.schemaurl = schema.id
                        poly.extendeddata.schemadata.newsimpledata("is_top_level", str(row["is_top_level"]))
                        poly.extendeddata.schemadata.newsimpledata("parent_field_name", str(row["parent_field_name"]) if row["parent_field_name"] else "N/A")

                        # Optionally include any other original columns
                        for col in gdf.columns:
                            if col not in ["geometry", "Name", "parent_field_name", "is_top_level"]:
                                val = row[col]
                                if pd.notna(val):
                                    poly.extendeddata.newdata(col, saxutils.escape(str(val)))
                                    
                        export_count += 1
                
                if export_count > 0:
                    out_path = f"enriched_export_{d_idx+1}.kmz"
                    kml.savekmz(out_path)
                    with open(out_path, "rb") as f:
                        st.download_button(f"📥 Download Dataset {d_idx+1} ({export_count} Polygons)", f, file_name=out_path)
                    output_generated = True
            
            if not output_generated:
                st.warning("No polygons were selected for export.")


# ----------------------------
# Tab 5: Duplicate KMZs
# ----------------------------
with tab5:

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



with tab6:
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



# ----------------------------
# Tab 7: Tramline Strip Generator (Unified Layer - Type Fixed)
# ----------------------------
with tab7:
    st.subheader("Parallel Tramline Strip Generator")
    st.markdown("Generate parallel strips aligned to a master heading, saved into a single unified KMZ layer.")

    # Initialize session state for persistence
    if 'tab7_result_gdf' not in st.session_state:
        st.session_state.tab7_result_gdf = None
    if 'tab7_field_name' not in st.session_state:
        st.session_state.tab7_field_name = ""

    col_files, col_coords = st.columns([1, 1])

    with col_files:
        st.markdown("### 1. Field Boundary")
        field_name_input = st.text_input("Field Name", value="")
        uploaded_field = st.file_uploader(
            "Upload Field KMZ/SHP", 
            type=["kmz", "shp", "shx", "dbf", "prj"], 
            key="tab7_upload_input"
        )
        # FIXED: value set to 0.0 to match step 0.5
        strip_width = st.number_input("Strip/Tramline Width (m)", value=0.0, step=0.5)

    with col_coords:
        st.markdown("### 2. Alignment Coordinates")
        c1, c2 = st.columns(2)
        with c1:
            st.write("**First Strip (A)**")
            f_top_lon = st.number_input("Top Long (A)", format="%.6f", value=0.0)
            f_top_lat = st.number_input("Top Lat (A)", format="%.6f", value=0.0)
            f_bot_lon = st.number_input("Bottom Long (A)", format="%.6f", value=0.0)
            f_bot_lat = st.number_input("Bottom Lat (A)", format="%.6f", value=0.0)
        with c2:
            st.write("**Final Strip (B)**")
            l_top_lon = st.number_input("Top Long (B)", format="%.6f", value=0.0)
            l_top_lat = st.number_input("Top Lat (B)", format="%.6f", value=0.0)

    # --- GENERATION LOGIC ---
    if st.button("Generate & Clip Strips", type="primary", key="tab7_gen_btn"):
        if not uploaded_field:
            st.error("Please upload a field boundary first.")
        elif any(v == 0.0 for v in [f_top_lon, f_top_lat, f_bot_lon, f_bot_lat, l_top_lon, l_top_lat]):
            st.warning("Please enter valid coordinates for both Strip A and Strip B.")
        elif strip_width <= 0:
            st.warning("Please enter a valid Strip Width.")
        else:
            try:
                # 1. Load and Project Field
                field_gdf = load_vector_file([uploaded_field] if not isinstance(uploaded_field, list) else uploaded_field)
                if field_gdf is not None:
                    avg_lon = field_gdf.geometry.centroid.x.mean()
                    avg_lat = field_gdf.geometry.centroid.y.mean()
                    utm_zone = int((avg_lon + 180) / 6) + 1
                    epsg_code = 32600 + utm_zone if avg_lat >= 0 else 32700 + utm_zone
                    utm_crs = f"EPSG:{epsg_code}"
                    
                    to_utm = Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True).transform
                    field_utm = field_gdf.to_crs(utm_crs).geometry.unary_union
                    
                    # 2. Geometry Setup
                    p1_t = Point(to_utm(f_top_lon, f_top_lat))
                    p1_b = Point(to_utm(f_bot_lon, f_bot_lat))
                    p2_t = Point(to_utm(l_top_lon, l_top_lat))
                    
                    line1 = LineString([p1_t, p1_b])
                    target_len = line1.length
                    angle_v = math.atan2(p1_b.y - p1_t.y, p1_b.x - p1_t.x)
                    angle_h = angle_v + (math.pi / 2)
                    
                    dist_vec_x, dist_vec_y = p2_t.x - p1_t.x, p2_t.y - p1_t.y
                    total_dist = (dist_vec_x * math.cos(angle_h)) + (dist_vec_y * math.sin(angle_h))
                    num_strips = int(round(abs(total_dist) / strip_width)) + 1
                    direction = 1 if total_dist > 0 else -1
                    
                    # 3. Create Strips
                    strips_list = []
                    c1_utm = line1.centroid
                    for i in range(num_strips):
                        offset = i * strip_width * direction
                        curr_cx = c1_utm.x + offset * math.cos(angle_h)
                        curr_cy = c1_utm.y + offset * math.sin(angle_h)
                        
                        h_len = target_len / 2
                        p_u = (curr_cx - h_len * math.cos(angle_v), curr_cy - h_len * math.sin(angle_v))
                        p_d = (curr_cx + h_len * math.cos(angle_v), curr_cy + h_len * math.sin(angle_v))
                        
                        strip_geom = LineString([p_u, p_d]).buffer(strip_width/2, cap_style=2, join_style=2)
                        clipped = strip_geom.intersection(field_utm)
                        
                        if not clipped.is_empty:
                            strips_list.append({
                                "Name": f"{field_name_input} - Strip {i+1}",
                                "geometry": clipped,
                                "parent_field": field_name_input,
                                "width_m": strip_width,
                                "is_field_section": "True"
                            })
                    
                    if strips_list:
                        st.session_state.tab7_result_gdf = gpd.GeoDataFrame(strips_list, crs=utm_crs).to_crs("EPSG:4326")
                        st.session_state.tab7_field_name = field_name_input if field_name_input else "Unnamed_Field"
                    else:
                        st.warning("No strips generated within the field boundary.")
            except Exception as e:
                st.error(f"Error: {e}")

    # --- PERSISTENT DISPLAY AREA ---
    if st.session_state.tab7_result_gdf is not None:
        res_gdf = st.session_state.tab7_result_gdf
        f_name = st.session_state.tab7_field_name
        field_bg_gdf = load_vector_file([uploaded_field] if not isinstance(uploaded_field, list) else uploaded_field)

        st.divider()

        # 1. Map Preview
        m = folium.Map(location=[res_gdf.geometry.centroid.y.mean(), res_gdf.geometry.centroid.x.mean()], zoom_start=15)
        folium.GeoJson(field_bg_gdf, name="Field Outline", style_function=lambda x: {'color': 'red', 'fillOpacity': 0, 'weight': 3}).add_to(m)
        folium.GeoJson(res_gdf, name="Strips", tooltip=folium.GeoJsonTooltip(fields=["Name"]), style_function=lambda x: {'color': 'green', 'fillOpacity': 0.3, 'weight': 1}).add_to(m)
        st_folium(m, width="100%", height=500, key="tab7_map_unified")

        # 2. Unified KMZ Construction
        kml = simplekml.Kml()
        
        for _, f_row in field_bg_gdf.iterrows():
            f_geom = f_row.geometry
            f_polys = [f_geom] if f_geom.geom_type == 'Polygon' else list(f_geom.geoms)
            for p in f_polys:
                pol = kml.newpolygon(name=f"BOUNDARY: {f_name}")
                pol.outerboundaryis = list(p.exterior.coords)
                pol.style.polystyle.color = simplekml.Color.changealphaint(20, simplekml.Color.red)
                pol.style.linestyle.color = simplekml.Color.red
                pol.style.linestyle.width = 4
                pol.extendeddata.newdata("parent_field", "None")
                pol.extendeddata.newdata("width_m", "0")
                pol.extendeddata.newdata("is_field_section", "False")

        for _, s_row in res_gdf.iterrows():
            s_geom = s_row.geometry
            s_polys = [s_geom] if s_geom.geom_type == 'Polygon' else list(s_geom.geoms)
            for p in s_polys:
                pol = kml.newpolygon(name=s_row["Name"])
                pol.outerboundaryis = list(p.exterior.coords)
                pol.style.polystyle.color = simplekml.Color.changealphaint(100, simplekml.Color.green)
                pol.style.linestyle.width = 1
                pol.extendeddata.newdata("parent_field", str(s_row["parent_field"]))
                pol.extendeddata.newdata("width_m", str(s_row["width_m"]))
                pol.extendeddata.newdata("is_field_section", "True")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".kmz") as tmp:
            kml.savekmz(tmp.name)
            with open(tmp.name, "rb") as f:
                st.download_button(
                    label=f"📥 Download {f_name} Unified KMZ",
                    data=f,
                    file_name=f"{f_name}_unified.kmz",
                    type="primary",
                    key="tab7_dl_unified"
                )
