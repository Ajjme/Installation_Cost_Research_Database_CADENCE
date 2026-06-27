import os
import pandas as pd
import geopandas as gpd
import streamlit as st
import plotly.express as px

# Set Streamlit page configurations
st.set_page_config(page_title="CIRCAD Labor Cost Mapper", layout="wide")

THEME_COLORS = {
    "primary": "#1e3a8a",
    "background": "#fefefe",
    "secondary_background": "#f8fafc",
    "text": "#1f2937",
    "link": "#1e40af",
    "border": "#d1d5db",
}

FINANCIAL_SCALE = ["#dbeafe", "#93c5fd", "#60a5fa", "#2563eb", "#1e3a8a"]


def apply_financial_theme_css():
    st.markdown(
        f"""
        <style>
        @font-face {{
            font-family: "Inter";
            src: url("/app/static/Inter_18pt-Regular.ttf") format("truetype");
            font-weight: 400;
            font-style: normal;
        }}
        @font-face {{
            font-family: "Inter";
            src: url("/app/static/Inter_18pt-SemiBold.ttf") format("truetype");
            font-weight: 600;
            font-style: normal;
        }}
        @font-face {{
            font-family: "SourceCodePro";
            src: url("/app/static/SourceCodePro-Regular.ttf") format("truetype");
            font-weight: 400;
            font-style: normal;
        }}

        html, body, [class*="css"]  {{
            font-family: "Inter", sans-serif;
            color: {THEME_COLORS["text"]};
        }}

        h1, h2, h3 {{
            letter-spacing: -0.02em;
        }}

        [data-testid="stMetricValue"] {{
            font-family: "SourceCodePro", monospace;
            color: {THEME_COLORS["primary"]};
            font-weight: 600;
        }}

        [data-testid="stSidebar"] {{
            border-right: 1px solid {THEME_COLORS["border"]};
        }}

        [data-testid="stDataFrame"] {{
            border: 1px solid {THEME_COLORS["border"]};
            border-radius: 8px;
            overflow: hidden;
        }}

        .block-container {{
            padding-top: 1.5rem;
            padding-bottom: 2rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

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


apply_financial_theme_css()

# --- Streamlit UI Layout ---
st.title("CIRCAD Project CADENCE: National Labor Cost Mapping Tool")
st.markdown(
    "This dashboard maps fully populated wage distributions using BLS OEWS regional data and "
    "fills suppression gaps with a three-tier fallback hierarchy."
)

# Sidebar Selection Controls
st.sidebar.header("Map Controls")
selected_occ = st.sidebar.selectbox("Select Occupation Group:", TARGET_OCCUPATIONS)
selected_metric = st.sidebar.selectbox("Select Wage Metric:", WAGE_METRICS, index=5) # Default to H_MEAN

st.sidebar.subheader("Geospatial Settings")
shapefile_folder_path = st.sidebar.text_input("Path to Shapefile Folder:", "geo_shapefiles")
shapefile_id_prop = st.sidebar.text_input("Shapefile Property Name for Area Code:", "msa7") 
st.sidebar.markdown("---")
st.sidebar.caption("Financial theme active: Inter for UI, Source Code Pro for numeric emphasis.")

# Load compiled wage dictionaries
bls_data = load_and_compile_bls_data()

# Process map if shapefile exists
if os.path.exists(shapefile_folder_path):
    with st.spinner("Loading and healing geographic shapefiles..."):
        # Load GIS borders directly
        gdf = gpd.read_file(shapefile_folder_path)

        if shapefile_id_prop not in gdf.columns:
            st.error(
                f"The shapefile attribute '{shapefile_id_prop}' was not found. "
                f"Available attributes: {', '.join(sorted(gdf.columns))}"
            )
            st.stop()
        
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
                return val, "Level 1: MSA/BOS"
            
            # Level 2 Fallback (State level check)
            state_key = row['PRIM_STATE']
            if pd.notna(state_key) and state_key in state_code_fallback_map:
                return state_code_fallback_map[state_key], "Level 2: State"
            
            # Alternative string-based state lookup if codes are different
            if 'area_title' in row and pd.notna(row['area_title']):
                for st_name, st_val in state_fallback_map.items():
                    if str(st_name) in str(row['area_title']).upper():
                        return st_val, "Level 2: State (Name Match)"
                        
            # Level 3 Fallback (National baseline calculation)
            return national_value, "Level 3: National"

        # Apply fallback resolution script across target rows
        healed_values = gdf_merged.apply(heal_gaps, axis=1, result_type="expand")
        gdf_merged[selected_metric] = healed_values[0]
        gdf_merged['DATA_SOURCE'] = healed_values[1]
        
        # Fill in visual placeholder metadata for newly healed rows
        gdf_merged['AREA_TITLE'] = gdf_merged['AREA_TITLE'].fillna(gdf_merged['name'] if 'name' in gdf_merged.columns else "Healed Region")

        data_source_counts = (
            gdf_merged['DATA_SOURCE']
            .value_counts(dropna=False)
            .rename_axis("Data Source")
            .reset_index(name="Region Count")
        )
        
    # --- Layout Rendering Split ---
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.subheader(f"National Breakdown: {selected_occ} ({selected_metric})")
        
        # Generate Interactive Plotly Choropleth Map using shapefile geometry interface
        fig = px.choropleth(
            gdf_merged,
            geojson=gdf_merged.geometry.__geo_interface__,
            locations=gdf_merged.index,
            color=selected_metric,
            custom_data=["AREA_TITLE", "AREA", "DATA_SOURCE"],
            color_continuous_scale=FINANCIAL_SCALE,
            labels={selected_metric: "Hourly Rate ($)"},
            projection="albers usa"
        )
        fig.update_traces(
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Hourly Rate: $%{z:,.2f}<br>"
                "Resolved By: %{customdata[2]}<extra></extra>"
            ),
            hoverlabel={
                "bgcolor": "#ffffff",
                "bordercolor": THEME_COLORS["border"],
                "font": {"family": "Inter", "color": THEME_COLORS["text"]},
                "align": "left",
            },
        )
        fig.update_geos(
            fitbounds="locations",
            visible=False,
            bgcolor=THEME_COLORS["secondary_background"],
            subunitcolor="#94a3b8",
            showcountries=False,
            showframe=False,
        )
        fig.update_layout(
            margin={"r": 0, "t": 40, "l": 0, "b": 0},
            height=650,
            font={"family": "Inter", "color": THEME_COLORS["text"]},
            paper_bgcolor=THEME_COLORS["background"],
            plot_bgcolor=THEME_COLORS["background"],
            coloraxis_colorbar={
                "title": "Hourly Rate ($)",
                "ticksuffix": "",
                "outlinecolor": THEME_COLORS["border"],
            },
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
    with col2:
        st.subheader("Regional Summary Statistics")
        st.metric(label="Highest Hourly Rate Found", value=f"${gdf_merged[selected_metric].max():.2f}")
        st.metric(label="Median Hourly Rate Found", value=f"${gdf_merged[selected_metric].median():.2f}")
        st.metric(label="Lowest Hourly Rate Found", value=f"${gdf_merged[selected_metric].min():.2f}")
        
        st.markdown("---")
        st.subheader("Fallback Source Coverage")
        st.dataframe(data_source_counts, use_container_width=True, hide_index=True, height=170)

        st.markdown("---")
        st.subheader("Regional Wage Ranking")
        st.dataframe(
            gdf_merged[['AREA_TITLE', selected_metric, 'DATA_SOURCE']]
            .dropna()
            .sort_values(by=selected_metric, ascending=False)
            .rename(columns={selected_metric: "Wage ($/hr)", 'DATA_SOURCE': 'Resolved By'}),
            height=400
        )
else:
    st.info(f"Please place your unzipped shapefile files inside the '{shapefile_folder_path}' folder to render the spatial map visualizer.")