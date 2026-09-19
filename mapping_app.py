import os
from pathlib import Path

import geopandas as gpd
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from labor_cost_data import (
    TARGET_OCCUPATIONS,
    WAGE_METRICS,
    build_state_wage_output,
    build_wage_outputs,
    normalize_code,
    read_consolidated_oews_file,
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
            letter-spacing: 0;
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
def load_resolved_wages(year: int):
    try:
        path = Path("input_data") / str(year) / f"all_data_M_{year}.xlsx"
        frames = read_consolidated_oews_file(path)
        local_wages, _ = build_wage_outputs(frames, data_year=year)
        state_wages = build_state_wage_output(frames, data_year=year)
        return local_wages, state_wages
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
selected_year = st.sidebar.select_slider(
    "Select OEWS Year:",
    options=[2021, 2022, 2023, 2024, 2025],
    value=2025,
)
selected_occ = st.sidebar.selectbox("Select Occupation Group:", TARGET_OCCUPATIONS)
selected_metric = st.sidebar.selectbox("Select Wage Metric:", WAGE_METRICS, index=5) # Default to H_MEAN

st.sidebar.subheader("Geospatial Settings")
shapefile_folder_path = st.sidebar.text_input("Path to Shapefile Folder:", "geo_shapefiles")
shapefile_id_prop = st.sidebar.text_input("Shapefile Property Name for Area Code:", "msa7") 
st.sidebar.markdown("---")
st.sidebar.caption("Financial theme active: Inter for UI, Source Code Pro for numeric emphasis.")

# Load every requested wage metric with local, state, and national fallback resolved.
wage_data, state_wage_data = load_resolved_wages(selected_year)

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
        
    with st.spinner(f"Joining {selected_year} wages to the map boundaries..."):
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
            st.error(
                f"No {selected_year} MSA wage areas matched the selected shapefile."
            )
            st.stop()

        gdf_merged["DATA_SOURCE"] = gdf_merged["DATA_SOURCE"].map(
            {
                "local": "Level 1: MSA",
                "state": "Level 2: State",
                "national": "Level 3: National",
                "unresolved": "Unresolved",
            }
        )
        selected_state_wages = state_wage_data[
            state_wage_data["OCC_TITLE"] == selected_occ
        ][
            [
                "PRIM_STATE",
                "AREA",
                "AREA_TITLE",
                selected_metric,
                f"{selected_metric}_SOURCE_LEVEL",
            ]
        ].rename(columns={f"{selected_metric}_SOURCE_LEVEL": "DATA_SOURCE"})
        selected_state_wages["DATA_SOURCE"] = selected_state_wages["DATA_SOURCE"].map(
            {
                "state": "State estimate",
                "national": "National fallback",
                "unresolved": "Unresolved",
            }
        )

        visible_values = pd.concat(
            [gdf_merged[selected_metric], selected_state_wages[selected_metric]],
            ignore_index=True,
        ).dropna()
        color_min = visible_values.min()
        color_max = visible_values.max()

        data_source_counts = (
            gdf_merged['DATA_SOURCE']
            .value_counts(dropna=False)
            .rename_axis("Data Source")
            .reset_index(name="Region Count")
        )
        
    # --- Layout Rendering Split ---
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.subheader(
            f"{selected_year} National Breakdown: {selected_occ} ({selected_metric})"
        )
        
        fig = go.Figure()
        fig.add_trace(
            go.Choropleth(
                locations=selected_state_wages["PRIM_STATE"],
                z=selected_state_wages[selected_metric],
                locationmode="USA-states",
                customdata=selected_state_wages[
                    ["AREA_TITLE", "AREA", "DATA_SOURCE"]
                ],
                coloraxis="coloraxis",
                marker_line_color="#ffffff",
                marker_line_width=0.7,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Geography: State<br>"
                    f"OEWS Year: {selected_year}<br>"
                    "Hourly Rate: $%{z:,.2f}<br>"
                    "Resolved By: %{customdata[2]}<extra></extra>"
                ),
                name="State underlay",
            )
        )
        fig.add_trace(
            go.Choropleth(
                geojson=gdf_merged.geometry.__geo_interface__,
                locations=gdf_merged.index,
                z=gdf_merged[selected_metric],
                customdata=gdf_merged[["AREA_TITLE", "AREA", "DATA_SOURCE"]],
                coloraxis="coloraxis",
                marker_line_color="#334155",
                marker_line_width=0.8,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Geography: MSA<br>"
                    f"OEWS Year: {selected_year}<br>"
                    "Hourly Rate: $%{z:,.2f}<br>"
                    "Resolved By: %{customdata[2]}<extra></extra>"
                ),
                name="MSA",
            )
        )
        fig.update_geos(
            scope="usa",
            projection_type="albers usa",
            visible=True,
            bgcolor=THEME_COLORS["secondary_background"],
            showland=True,
            landcolor="#f1f5f9",
            showlakes=True,
            lakecolor=THEME_COLORS["secondary_background"],
            showsubunits=True,
            subunitcolor="#94a3b8",
            showcountries=True,
            countrycolor="#64748b",
            showframe=False,
        )
        fig.update_layout(
            margin={"r": 0, "t": 40, "l": 0, "b": 0},
            height=650,
            font={"family": "Inter", "color": THEME_COLORS["text"]},
            paper_bgcolor=THEME_COLORS["background"],
            plot_bgcolor=THEME_COLORS["background"],
            coloraxis={
                "colorscale": FINANCIAL_SCALE,
                "cmin": color_min,
                "cmax": color_max,
                "colorbar": {
                    "title": "Hourly Rate ($)",
                    "ticksuffix": "",
                    "outlinecolor": THEME_COLORS["border"],
                },
            },
            hoverlabel={
                "bgcolor": "#ffffff",
                "bordercolor": THEME_COLORS["border"],
                "font": {"family": "Inter", "color": THEME_COLORS["text"]},
                "align": "left",
            },
            showlegend=False,
        )
        
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "State estimates form the background layer; MSA estimates take visual priority. "
            "The 2019 MSA boundaries are reused for every selected OEWS year."
        )
        
    with col2:
        st.subheader("MSA Summary Statistics")
        st.metric(label="Highest Hourly Rate Found", value=f"${gdf_merged[selected_metric].max():.2f}")
        st.metric(label="Median Hourly Rate Found", value=f"${gdf_merged[selected_metric].median():.2f}")
        st.metric(label="Lowest Hourly Rate Found", value=f"${gdf_merged[selected_metric].min():.2f}")
        
        st.markdown("---")
        st.subheader("MSA Fallback Coverage")
        st.dataframe(data_source_counts, use_container_width=True, hide_index=True, height=170)

        st.markdown("---")
        st.subheader("MSA Wage Ranking")
        st.dataframe(
            gdf_merged[['AREA_TITLE', selected_metric, 'DATA_SOURCE']]
            .dropna()
            .sort_values(by=selected_metric, ascending=False)
            .rename(columns={selected_metric: "Wage ($/hr)", 'DATA_SOURCE': 'Resolved By'}),
            height=400
        )
else:
    st.info(f"Please place your unzipped shapefile files inside the '{shapefile_folder_path}' folder to render the spatial map visualizer.")