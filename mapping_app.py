import os
from pathlib import Path

import geopandas as gpd
import streamlit as st
import plotly.express as px

from labor_cost_data import (
    TARGET_OCCUPATIONS,
    WAGE_METRICS,
    build_wage_outputs,
    normalize_code,
    read_oews_files,
)

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

@st.cache_data
def load_resolved_wages():
    try:
        frames = read_oews_files(Path("input_data"))
        wide, _ = build_wage_outputs(frames)
        return wide
    except (FileNotFoundError, ValueError) as error:
        st.error(str(error))
        st.stop()


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

# Load every requested wage metric with local, state, and national fallback resolved.
wage_data = load_resolved_wages()

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
        
        gdf[shapefile_id_prop] = normalize_code(gdf[shapefile_id_prop], width=7)
        
        # Simplify geometry path vertices to speed up web rendering frame rates
        gdf['geometry'] = gdf['geometry'].simplify(tolerance=0.01, preserve_topology=True)
        
    with st.spinner("Joining resolved wages to current MSA boundaries..."):
        selected_wages = wage_data[
            (wage_data["GEOGRAPHY_TYPE"] == "msa")
            & (wage_data["OCC_TITLE"] == selected_occ)
        ][["AREA", "AREA_TITLE", selected_metric, f"{selected_metric}_SOURCE_LEVEL"]].rename(
            columns={f"{selected_metric}_SOURCE_LEVEL": "DATA_SOURCE"}
        )
        gdf_merged = gdf.merge(
            selected_wages,
            left_on=shapefile_id_prop,
            right_on="AREA",
            how="inner",
        )
        if gdf_merged.empty:
            st.error("No 2025 MSA wage areas matched the selected shapefile.")
            st.stop()

        gdf_merged["DATA_SOURCE"] = gdf_merged["DATA_SOURCE"].map(
            {
                "local": "Level 1: MSA",
                "state": "Level 2: State",
                "national": "Level 3: National",
                "unresolved": "Unresolved",
            }
        )

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