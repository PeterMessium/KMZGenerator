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

# ----------------------------
# App config
# ----------------------------
st.set_page_config(page_title="Operations Team Tooling", layout="wide")
st.title("Operations Team Tooling")

# ----------------------------
# Tabs
# ----------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "Imaging Polygon Generator",
    "Shapefile → KMZ Converter",
    "OpenCosmos Tasking AOI Generator",
    "Subsection Generator"
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

        for f in uploaded_files:
            with open(os.path.join(shapefile_folder, f.name), "wb") as out_file:
                out_file.write(f.getbuffer())

        shp_files = [f for f in uploaded_files if f.name.endswith(".shp")]
        if len(shp_files) != 1:
            st.error("Please upload exactly one .shp file.")
        else:
            shp_path = os.path.join(shapefile_folder, shp_files[0].name)
            kmz_output = f"{os.path.splitext(shp_files[0].name)[0]}.kmz"

            try:
                gdf = gpd.read_file(shp_path)
                if gdf.crs != "EPSG:4326":
                    gdf = gdf.to_crs(epsg=4326)

                kml = simplekml.Kml()
                for idx, row in gdf.iterrows():
                    geom = row.geometry
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
