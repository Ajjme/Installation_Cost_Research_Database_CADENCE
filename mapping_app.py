import os
import pandas as pd
import geopandas as gpd
import streamlit as st
import plotly.express as px

# Set Streamlit page configurations
st.set_page_config(page_title="CIRCAD Labor Cost Mapper", layout="wide")

# Define target occupations and wage metrics as requested
TARGET_OCCUPATIONS = [
    "Purchasing Managers",
    "Construction Managers",
    "Claims Adjusters, Examiners, and Investigators",
    "Cost Estimators",
    "Insurance Sales Agents",
    "Construction Laborers",
    "Roofers",
    "Construction and Building Inspectors",
    "Installation, Maintenance, and Repair Occupations",
    "First-Line Supervisors of Construction Trades"
]

WAGE_METRICS = ['H_PCT10', 'H_PCT25', 'H_MEDIAN', 'H_PCT75', 'H_PCT90', 'H_MEAN']

@st.cache_data
def load_and_compile_bls_data():
    """
    Loads MSA, BOS, State, and National Excel files, normalizes columns,
    and returns individual structured data dictionaries for strict fallback querying.
    """
    def clean_dataset(filepath):
        if not os.path.exists(filepath):
            st.error(f"File not found: {filepath}. Please ensure it is placed in the 'input_data' folder.")
            return None
        
        df = pd.read_excel(filepath)
        df.columns = df.columns.str.upper().str.strip()
        
        # Filter for only target occupations
        df = df[df['OCC_TITLE'].isin(TARGET_OCCUPATIONS)].copy()
        
        # Coerce wage metrics to numeric, transforming suppression codes (*, #) into NaN
        for col in WAGE_METRICS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                
        # Clean area and state code strings to force stable string keys
        for col in ['AREA', 'PRIM_STATE', 'STATE']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip().str.zfill(7 if col == 'AREA' else 1)
                
        return df

    # Load all four baseline layers
    df_msa = clean_dataset('input_data/MSA_M2025_dl.xlsx')
    df_bos = clean_dataset('input_data/BOS_M2025_dl.xlsx')
    df_state = clean_dataset('input_data/state_M2025_dl.xlsx')
    df_national = clean_dataset('input_data/national_M2025_dl.xlsx')
    
    if df_msa is None or df_bos is None or df_state is None or df_national is None:
        st.stop()

    # Combine Level 1 granular files (Metropolitan + Nonmetropolitan Balance of State)
    df_level1 = pd.concat([df_msa, df_bos], ignore_index=True)
    
    return {
        "level1": df_level1,
        "state": df_state,
        "national": df_national
    }

# --- Streamlit UI Layout ---
st.title("🌐 CIRCAD Project: National Labor Cost Mapping Tool")
st.markdown("This dashboard maps fully populated wage distributions using BLS OEWS granular regional data, filling data suppression gaps hierarchically.")

# Sidebar Selection Controls
st.sidebar.header("🗺️ Map Controls")
selected_occ = st.sidebar.selectbox("Select Occupation Group:", TARGET_OCCUPATIONS)
selected_metric = st.sidebar.selectbox("Select Wage Metric:", WAGE_METRICS, index=5) # Default to H_MEAN

st.sidebar.subheader("Geospatial Settings")
shapefile_folder_path = st.sidebar.text_input("Path to Shapefile Folder:", "geo_shapefiles")
shapefile_id_prop = st.sidebar.text_input("Shapefile Property Name for Area Code:", "msa7") 

# Load compiled wage dictionaries
bls_data = load_and_compile_bls_data()

# Process map if shapefile exists
if os.path.exists(shapefile_folder_path):
    with st.spinner("Loading and healing geographic shapefiles..."):
        # Load GIS borders directly
        gdf = gpd.read_file(shapefile_folder_path)
        
        # FIX: Force shapefile msa7 field to be string and pad leading zeroes to match Excel 'AREA' keys!
        gdf[shapefile_id_prop] = gdf[shapefile_id_prop].astype(str).str.strip().str.zfill(7)
        
        # Simplify geometry path vertices to speed up web rendering frame rates
        gdf['geometry'] = gdf['geometry'].simplify(tolerance=0.01, preserve_topology=True)
        
    # --- Execute 3-Level Hierarchy Cross-Walk & Gap Healing ---
    with st.spinner("Executing spatial fallback calculations for missing blocks..."):
        # Isolate target occupation segments across files
        l1_filtered = bls_data["level1"][bls_data["level1"]['OCC_TITLE'] == selected_occ]
        l2_filtered = bls_data["state"][bls_data["state"]['OCC_TITLE'] == selected_occ]
        l3_filtered = bls_data["national"][bls_data["national"]['OCC_TITLE'] == selected_occ]
        
        # Step A: Link Shapefile rows to Level 1 Data (MSA + Balance of State)
        gdf_merged = gdf.merge(l1_filtered, left_on=shapefile_id_prop, right_on='AREA', how='left')
        
        # Step B: Dynamically determine state for polygons that came up empty
        # Often the shapefile has a default state attribute, or we map it from the area title text string
        if 'PRIM_STATE' not in gdf_merged.columns or gdf_merged['PRIM_STATE'].isna().all():
            # Fallback if PRIM_STATE isn't built into the shapefile attributes natively
            if 'state' in gdf_merged.columns:
                gdf_merged['PRIM_STATE'] = gdf_merged['state'].astype(str).str.strip()
            else:
                gdf_merged['PRIM_STATE'] = None

        # Build cross-walk mapping dictionaries from state and national frames for optimized lookups
        state_fallback_map = dict(zip(l2_filtered['STATE_NAME'].str.upper() if 'STATE_NAME' in l2_filtered.columns else l2_filtered['PRIM_STATE'], l2_filtered[selected_metric]))
        state_code_fallback_map = dict(zip(l2_filtered['PRIM_STATE'], l2_filtered[selected_metric]))
        
        # National baseline number
        national_value = l3_filtered[selected_metric].values[0] if not l3_filtered.empty else None
        
        # Step C: Iterate and heal the target wage column if any specific zone is NaN
        def heal_gaps(row):
            val = row[selected_metric]
            if pd.notna(val):
                return val # Level 1 success
            
            # Level 2 Fallback (State level check)
            state_key = row['PRIM_STATE']
            if pd.notna(state_key) and state_key in state_code_fallback_map:
                return state_code_fallback_map[state_key]
            
            # Alternative string-based state lookup if codes are different
            if 'area_title' in row and pd.notna(row['area_title']):
                for st_name, st_val in state_fallback_map.items():
                    if str(st_name) in str(row['area_title']).upper():
                        return st_val
                        
            # Level 3 Fallback (National baseline calculation)
            return national_value

        # Apply fallback resolution script across target rows
        gdf_merged[selected_metric] = gdf_merged.apply(heal_gaps, axis=1)
        
        # Fill in visual placeholder metadata for newly healed rows
        gdf_merged['AREA_TITLE'] = gdf_merged['AREA_TITLE'].fillna(gdf_merged['name'] if 'name' in gdf_merged.columns else "Healed Region")
        
    # --- Layout Rendering Split ---
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.subheader(f"Healed National Breakdown: {selected_occ} ({selected_metric})")
        
        # Generate Interactive Plotly Choropleth Map using shapefile geometry interface
        fig = px.choropleth(
            gdf_merged,
            geojson=gdf_merged.geometry.__geo_interface__,
            locations=gdf_merged.index,
            color=selected_metric,
            hover_name="AREA_TITLE",
            hover_data={selected_metric: ":$.2f", "AREA": True},
            color_continuous_scale="Viridis",
            labels={selected_metric: "Hourly Rate ($)"},
            projection="albers usa"
        )
        fig.update_geos(fitbounds="locations", visible=False)
        fig.update_layout(margin={"r":0,"t":40,"l":0,"b":0}, height=650)
        
        st.plotly_chart(fig, use_container_width=True)
        
    with col2:
        st.subheader("Regional Summary Statistics")
        st.metric(label="Highest Hourly Rate Found", value=f"${gdf_merged[selected_metric].max():.2f}")
        st.metric(label="Median Hourly Rate Found", value=f"${gdf_merged[selected_metric].median():.2f}")
        st.metric(label="Lowest Hourly Rate Found", value=f"${gdf_merged[selected_metric].min():.2f}")
        
        st.markdown("---")
        st.dataframe(
            gdf_merged[['AREA_TITLE', selected_metric]]
            .dropna()
            .sort_values(by=selected_metric, ascending=False)
            .rename(columns={selected_metric: "Wage ($/hr)"}),
            height=400
        )
else:
    st.info(f"💡 Please place your unzipped shapefile files inside the `{shapefile_folder_path}` folder to render the spatial map visualizer.")