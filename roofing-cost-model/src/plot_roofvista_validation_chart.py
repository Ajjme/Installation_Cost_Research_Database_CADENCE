"""Plot RoofVista validation results from CSV.

Usage examples:
    py -3 src/plot_roofvista_validation_chart.py
    py -3 src/plot_roofvista_validation_chart.py --run-id 815d25060916
    py -3 src/plot_roofvista_validation_chart.py --output data_output/roofvista/roofvista_validation_chart.html
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs


DEFAULT_CSV = Path("data_output/roofvista/roofvista_validation_estimates.csv")
DEFAULT_OUT = Path("data_output/roofvista/roofvista_validation_chart.html")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build interactive RoofVista stacked bar chart from CSV.")
    parser.add_argument("--input", type=Path, default=DEFAULT_CSV, help="Input RoofVista CSV path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT, help="Output HTML path")
    parser.add_argument("--run-id", default=None, help="Specific run_id to plot (default: latest run)")
    parser.add_argument(
        "--status",
        default="ok",
        help="Filter parse_status value (default: ok). Use 'all' to disable status filtering.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input CSV not found: {args.input}")

    df = pd.read_csv(args.input)
    if df.empty:
        raise SystemExit("Input CSV is empty.")

    if args.status.lower() != "all":
        df = df[df["parse_status"] == args.status].copy()

    if df.empty:
        raise SystemExit("No rows after parse_status filtering.")

    run_id = args.run_id
    if run_id is None:
        run_id = str(df["run_id"].iloc[-1])

    df = df[df["run_id"] == run_id].copy()
    if df.empty:
        raise SystemExit(f"No rows found for run_id={run_id}")

    metric_cols = [
        "installed_cost_per_square",
        "material_cost_per_square",
        "labor_cost_per_square",
    ]
    for col in metric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=metric_cols)
    if df.empty:
        raise SystemExit("No numeric values found for required cost columns.")

    viz = df[
        [
            "requested_state",
            "requested_city",
            "material_tier_requested",
            "installed_cost_per_square",
            "material_cost_per_square",
            "labor_cost_per_square",
        ]
    ].copy()
    viz["requested_state"] = viz["requested_state"].fillna("").astype(str)
    viz["requested_city"] = viz["requested_city"].fillna("").astype(str)

    grouped = (
        viz.groupby("material_tier_requested", as_index=False)[metric_cols]
        .mean(numeric_only=True)
        .sort_values("installed_cost_per_square")
    )

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            name="Installed Cost / Square",
            x=grouped["material_tier_requested"],
            y=grouped["installed_cost_per_square"],
            marker_color="#4C78A8",
            hovertemplate="%{x}<br>Installed: $%{y:,.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            name="Material Cost / Square",
            x=grouped["material_tier_requested"],
            y=grouped["material_cost_per_square"],
            marker_color="#F58518",
            hovertemplate="%{x}<br>Material: $%{y:,.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            name="Labor Cost / Square",
            x=grouped["material_tier_requested"],
            y=grouped["labor_cost_per_square"],
            marker_color="#54A24B",
            hovertemplate="%{x}<br>Labor: $%{y:,.2f}<extra></extra>",
        )
    )

    fig.update_layout(
        barmode="stack",
        title=f"RoofVista Stacked Cost Components by Material (run {run_id})",
        xaxis_title="Material Tier",
        yaxis_title="Cost per Square (USD)",
        template="plotly_white",
        legend_title="Cost Components",
        margin=dict(t=80, l=60, r=30, b=80),
    )

    fig_json = fig.to_json()
    records_json = viz.to_dict(orient="records")
    plotly_js = get_plotlyjs()

    html = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
    <title>RoofVista Interactive Stacked Cost Chart</title>
    <style>
        body {{ font-family: Segoe UI, Tahoma, sans-serif; margin: 0; padding: 0; background: #f8fafc; color: #1f2937; }}
        .wrap {{ max-width: 1200px; margin: 0 auto; padding: 18px 22px 10px; }}
        .controls {{ display: flex; gap: 16px; flex-wrap: wrap; align-items: end; margin-bottom: 12px; }}
        .control {{ display: flex; flex-direction: column; gap: 6px; }}
        label {{ font-size: 13px; font-weight: 600; }}
        select {{ min-width: 220px; padding: 8px 10px; border: 1px solid #cbd5e1; border-radius: 8px; background: #fff; }}
        #chart {{ width: 100%; height: 720px; background: #fff; border-radius: 12px; box-shadow: 0 1px 2px rgba(0,0,0,0.06); }}
    </style>
    <script type=\"text/javascript\">{plotly_js}</script>
</head>
<body>
    <div class=\"wrap\">
        <div class=\"controls\">
            <div class=\"control\">
                <label for=\"stateFilter\">State</label>
                <select id=\"stateFilter\"></select>
            </div>
            <div class=\"control\">
                <label for=\"cityFilter\">City</label>
                <select id=\"cityFilter\"></select>
            </div>
        </div>
        <div id=\"chart\"></div>
    </div>

    <script>
        const dataRecords = {json.dumps(records_json)};
        const figure = {fig_json};
        const chartEl = document.getElementById('chart');
        const stateEl = document.getElementById('stateFilter');
        const cityEl = document.getElementById('cityFilter');

        Plotly.newPlot(chartEl, figure.data, figure.layout, {{responsive: true}});

        function uniqueStates() {{
            return [...new Set(dataRecords.map(r => r.requested_state).filter(Boolean))].sort();
        }}

        function uniqueCitiesForState(state) {{
            const rows = dataRecords.filter(r => !state || state === 'ALL' || r.requested_state === state);
            return [...new Set(rows.map(r => r.requested_city).filter(Boolean))].sort();
        }}

        function setOptions(selectEl, values, includeAll = true) {{
            selectEl.innerHTML = '';
            if (includeAll) {{
                const allOpt = document.createElement('option');
                allOpt.value = 'ALL';
                allOpt.text = 'All';
                selectEl.appendChild(allOpt);
            }}
            values.forEach(v => {{
                const opt = document.createElement('option');
                opt.value = v;
                opt.text = v;
                selectEl.appendChild(opt);
            }});
        }}

        function aggregateRows(rows) {{
            const map = new Map();
            rows.forEach(r => {{
                const key = r.material_tier_requested;
                if (!map.has(key)) {{
                    map.set(key, {{
                        material_tier_requested: key,
                        installed_sum: 0,
                        material_sum: 0,
                        labor_sum: 0,
                        count: 0,
                    }});
                }}
                const cur = map.get(key);
                cur.installed_sum += Number(r.installed_cost_per_square || 0);
                cur.material_sum += Number(r.material_cost_per_square || 0);
                cur.labor_sum += Number(r.labor_cost_per_square || 0);
                cur.count += 1;
            }});

            const out = [...map.values()].map(v => {{
                const n = Math.max(v.count, 1);
                return {{
                    material_tier_requested: v.material_tier_requested,
                    installed_cost_per_square: v.installed_sum / n,
                    material_cost_per_square: v.material_sum / n,
                    labor_cost_per_square: v.labor_sum / n,
                }};
            }});

            out.sort((a, b) => a.installed_cost_per_square - b.installed_cost_per_square);
            return out;
        }}

        function applyFilters() {{
            const state = stateEl.value;
            const city = cityEl.value;
            let rows = dataRecords;
            if (state && state !== 'ALL') {{
                rows = rows.filter(r => r.requested_state === state);
            }}
            if (city && city !== 'ALL') {{
                rows = rows.filter(r => r.requested_city === city);
            }}

            const agg = aggregateRows(rows);
            const x = agg.map(r => r.material_tier_requested);
            const installed = agg.map(r => r.installed_cost_per_square);
            const material = agg.map(r => r.material_cost_per_square);
            const labor = agg.map(r => r.labor_cost_per_square);

            Plotly.update(
                chartEl,
                {{ x: [x, x, x], y: [installed, material, labor] }},
                {{
                    title: `RoofVista Stacked Cost Components by Material (run {run_id}) - State: ${{state || 'ALL'}}, City: ${{city || 'ALL'}}`
                }}
            );
        }}

        setOptions(stateEl, uniqueStates(), true);
        setOptions(cityEl, uniqueCitiesForState('ALL'), true);

        stateEl.addEventListener('change', () => {{
            const state = stateEl.value;
            setOptions(cityEl, uniqueCitiesForState(state), true);
            cityEl.value = 'ALL';
            applyFilters();
        }});
        cityEl.addEventListener('change', applyFilters);

        applyFilters();
    </script>
</body>
</html>
"""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(f"Interactive chart saved: {args.output}")


if __name__ == "__main__":
    main()
