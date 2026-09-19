import pandas as pd
import pytest

from labor_cost_data import (
    TARGET_OCCUPATION_CODES,
    TARGET_OCCUPATIONS,
    WAGE_METRICS,
    build_state_wage_output,
    build_wage_outputs,
    read_consolidated_oews_file,
)


def make_row(area, title, state, occupation, occupation_code, **metrics):
    row = {
        "AREA": area,
        "AREA_TITLE": title,
        "PRIM_STATE": state,
        "OCC_CODE": occupation_code,
        "OCC_TITLE": occupation,
    }
    row.update({metric: metrics.get(metric) for metric in WAGE_METRICS})
    return row


@pytest.fixture
def oews_frames():
    national_rows = []
    for index, occupation in enumerate(TARGET_OCCUPATIONS):
        national_rows.append(
            make_row(
                "0000000",
                "United States",
                "US",
                occupation,
                TARGET_OCCUPATION_CODES[occupation],
                **{metric: 30 + index for metric in WAGE_METRICS},
            )
        )

    target = TARGET_OCCUPATIONS[0]
    code = TARGET_OCCUPATION_CODES[target]
    return {
        "msa": pd.DataFrame(
            [
                make_row(
                    "12345",
                    "Example MSA",
                    "AL",
                    target,
                    code,
                    H_MEAN=40,
                    H_MEDIAN=35,
                )
            ]
        ),
        "bos": pd.DataFrame(
            [make_row("0100001", "Example BOS", "AL", target, code, H_MEAN=38)]
        ),
        "state": pd.DataFrame(
            [
                make_row(
                    "01",
                    "Alabama",
                    "AL",
                    target,
                    code,
                    H_MEAN=37,
                    H_MEDIAN=33,
                    H_PCT10=20,
                )
            ]
        ),
        "national": pd.DataFrame(national_rows),
    }


def test_builds_complete_area_occupation_metric_grid(oews_frames):
    wide, long = build_wage_outputs(oews_frames)

    assert len(wide) == 2 * len(TARGET_OCCUPATIONS)
    assert len(long) == len(wide) * len(WAGE_METRICS)
    assert set(long["WAGE_METRIC"]) == set(WAGE_METRICS)
    assert not wide.duplicated(["AREA", "OCC_TITLE"]).any()
    assert not long.duplicated(["AREA", "OCC_TITLE", "WAGE_METRIC"]).any()


def test_resolves_each_metric_independently(oews_frames):
    wide, long = build_wage_outputs(oews_frames)
    target = TARGET_OCCUPATIONS[0]
    msa = wide[(wide["AREA"] == "0012345") & (wide["OCC_TITLE"] == target)].iloc[0]

    assert msa["H_MEAN"] == 40
    assert msa["H_MEAN_SOURCE_LEVEL"] == "local"
    assert msa["H_PCT10"] == 20
    assert msa["H_PCT10_SOURCE_LEVEL"] == "state"
    assert msa["H_PCT25"] == 30
    assert msa["H_PCT25_SOURCE_LEVEL"] == "national"

    metric_rows = long[(long["AREA"] == "0012345") & (long["OCC_TITLE"] == target)]
    assert metric_rows.set_index("WAGE_METRIC").loc["H_PCT10", "IS_IMPUTED"]
    assert not metric_rows.set_index("WAGE_METRIC").loc["H_MEAN", "IS_IMPUTED"]


def test_resolves_state_metrics_to_national_and_preserves_year(oews_frames):
    states = build_state_wage_output(oews_frames, data_year=2024)
    target = TARGET_OCCUPATIONS[0]
    state = states[states["OCC_TITLE"] == target].iloc[0]

    assert len(states) == len(TARGET_OCCUPATIONS)
    assert state["DATA_YEAR"] == 2024
    assert state["H_MEAN"] == 37
    assert state["H_MEAN_SOURCE_LEVEL"] == "state"
    assert state["H_PCT25"] == 30
    assert state["H_PCT25_SOURCE_LEVEL"] == "national"
    assert state["H_PCT25_SOURCE_AREA"] == "0000000"
    missing_state_occupation = states[
        states["OCC_TITLE"] == TARGET_OCCUPATIONS[1]
    ].iloc[0]
    assert missing_state_occupation["H_MEAN"] == 31
    assert missing_state_occupation["H_MEAN_SOURCE_LEVEL"] == "national"


def test_build_wage_outputs_preserves_selected_year(oews_frames):
    wide, long = build_wage_outputs(oews_frames, data_year=2022)

    assert set(wide["DATA_YEAR"]) == {2022}
    assert set(long["DATA_YEAR"]) == {2022}


def test_rejects_duplicate_local_keys(oews_frames):
    oews_frames["msa"] = pd.concat(
        [oews_frames["msa"], oews_frames["msa"]], ignore_index=True
    )

    with pytest.raises(ValueError, match="Duplicate local wage keys"):
        build_wage_outputs(oews_frames)


def test_consolidated_loader_keeps_only_cross_industry_rows(tmp_path):
    target = TARGET_OCCUPATIONS[0]
    code = TARGET_OCCUPATION_CODES[target]
    rows = [
        {
            **make_row("12345", "Example MSA", "AL", target, code, H_MEAN=40),
            "AREA_TYPE": 4,
            "NAICS": "000000",
        },
        {
            **make_row("12345", "Example MSA", "AL", target, code, H_MEAN=45),
            "AREA_TYPE": 4,
            "NAICS": "238160",
        },
        {
            **make_row("01", "Alabama", "AL", target, code, H_MEAN=37),
            "AREA_TYPE": 2,
            "NAICS": "000000",
        },
        {
            **make_row("99", "U.S.", "US", target, code, H_MEAN=35),
            "AREA_TYPE": 1,
            "NAICS": "000000",
        },
    ]
    path = tmp_path / "annual.xlsx"
    pd.DataFrame(rows).to_excel(path, index=False)

    frames = read_consolidated_oews_file(path)

    assert len(frames["msa"]) == 1
    assert frames["msa"]["H_MEAN"].iloc[0] == 40
    assert len(frames["state"]) == 1
    assert len(frames["national"]) == 1
    assert frames["bos"].empty