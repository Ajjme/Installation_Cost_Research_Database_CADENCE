Executive direction

The core analysis should be:

Material-only retail price model by geography, using big-box retailer web data as the main source, with RoofVista/SquareDash-style contractor quote platforms used only as validation / calibration sources, not primary material pricing.

We should explicitly exclude for now:

Proprietary construction databases: RSMeans, Gordian, Procore, etc.
Manufacturer / wholesale pricing.
Distributor pricing.
Insurance adjuster pricing such as Xactimate / Verisk.

Those can be listed as future “truth-set” or board-requested validation sources, but they should not be part of the first build.

1. Scope of the model
Primary goal

Estimate roofing material cost for single-family roof replacement by:

Material type.
Product tier.
Geography.
Retail vs. contractor/bulk purchase price where publicly observable.
Time.
Initial materials

We should model only the primary roof-covering material, not full system components yet.

Asphalt shingles

Need explicit tiering:

3-tab shingles
Architectural / dimensional shingles
Premium / designer architectural shingles
Potential future split: impact-resistant / Class 4 shingles, because hail-prone markets may shift demand toward these.

Home Depot already exposes filters for architectural shingles, 3-tab shingles, starter shingles, hip/ridge shingles, asphalt material, brands, warranty, fire rating, impact resistance, and price ranges, which means we can map products into our taxonomy from the public category page.

Metal roofs

Initial split:

Corrugated / exposed-fastener metal panels
Standing seam metal
Optional later: stone-coated steel, metal shingles.

Retailers are likely useful for corrugated panels, but less reliable for true standing seam because standing seam is often job-specific or quote-based.

Clay / concrete tile

Initial split:

Clay tile
Concrete tile
Optional later: profile/style, e.g. barrel/S-tile/flat.

Tile is likely harder than asphalt because many products are special order, regionally distributed, freight-sensitive, or not consistently priced online.

2. Source hierarchy
A. Main source: big-box retail pricing

Primary sources:

Home Depot.
Lowe’s.
Possibly Menards, depending on geographic coverage.
Possibly regional big-box/building-material retailers if needed.

Home Depot is the cleanest starting point because its roof-shingle page exposes product category, brand, material, product type, model number, retail price, unit price, and bulk discount examples. For example, Home Depot lists GAF Timberline HDZ at $41.47 per 33.33 sq. ft. bundle, or $1.24/sq. ft., with a bulk threshold of “Buy 39 or more” at $37.32. Home Depot also states that local store prices may vary and stocked inventory is not guaranteed, which means ZIP/store must be part of the scrape key.

Data fields to scrape

For each product/store/date:

Field	Example
retailer	Home Depot
scrape_date	2026-06-27
zip_code	90001
store_id	if available
product_id / SKU / model	Model# 0489180
product_name	GAF Timberline HDZ Charcoal...
brand	GAF
material_family	asphalt / metal / tile
product_tier	3-tab / architectural / premium
coverage_sqft_per_unit	33.33
retail_price_per_unit	$41.47
retail_price_per_sqft	$1.24
bulk_threshold	39 bundles
bulk_price_per_unit	$37.32
bulk_price_per_sqft	derived
availability	in stock / ship / unavailable
warranty	lifetime limited / 25-year / etc.
fire_rating	Class A
impact_rating	if available
color	Charcoal
URL	source URL
Retail vs. contractor pricing

We should not call bulk pricing “contractor price” unless the site explicitly does. Instead:

Retail price = single-unit web price.
Observed bulk price = public volume-discount price.
Contractor proxy price = public bulk price, if no pro-login price is available.

Home Depot’s observed bulk discount can be computed directly: $37.32 / $41.47 = ~10.0% discount for that example.

B. Public inflation series: BLS / FRED

Use BLS/FRED for time adjustment, not local price levels.

The BLS Producer Price Index measures average changes in prices received by domestic producers and is published monthly. BLS also provides public data tools and an API/data retrieval infrastructure.

Candidate PPI families:

Prepared asphalt and tar roofing.
Sheet metal roofing.
Asphalt paving/roofing materials proxies.
Steel mill products.
Aluminum products.
Clay products / concrete products where available.
Truck transportation / freight proxies if later needed.
Use in model

Retail scrape gives current observed price:

P
retail,product,store,t
	​


BLS/FRED gives escalation factor:

Escalation
m
0
	​

→m
1
	​

	​

=
PPI
m
0
	​

	​

PPI
m
1
	​

	​

	​


Use this to backcast or forward-adjust product prices when the scrape date and model date differ.

Important limitation

BLS/FRED is national index data, not ZIP/MSA SKU pricing. It should not be used as the main geography source.

C. Validation source: RoofVista

RoofVista is useful as a validation / calibration dataset, not as the main material-cost dataset.

RoofVista publishes a Roofing Cost Index with per-square installed prices by material, including 3-tab shingles, architectural shingles, premium shingles, tile, and standing seam metal. It states that prices include materials + labor and are per roofing square, where one square equals 100 sq. ft.

RoofVista also publishes labor/material splits: 3-tab shingles at roughly 48% labor / 53% materials, architectural shingles at 45% labor / 55% materials, premium shingles at 45% labor / 55% materials, tile at 50% / 50%, and standing seam metal at 50% / 50%. The percentages do not sum perfectly in the 3-tab case, so we should treat them as indicative rather than accounting-grade.

RoofVista says its current index covers six active states: Massachusetts, Connecticut, Rhode Island, New Hampshire, Vermont, and Maine, with New York, New Jersey, Pennsylvania, Texas, Florida, and California coming later. It also says municipal-level adjustments are available through its API and instant-quote tool, while the displayed table is state-level.

Most importantly, RoofVista documents a public API:

curl https://roofvista.com/api/v1/public/pricing?state=MA
curl https://roofvista.com/api/v1/public/pricing?state=CT&material=architectural

It says responses are cached for one hour and data is CC BY 4.0 with attribution.

How to use RoofVista

Use it for:

Installed-cost benchmarks by state/material.
Labor/material split priors.
Backing out implied material cost:
MaterialCost
RV
	​

=InstalledCost
RV
	​

×MaterialShare
RV
	​

Checking whether our retail material scrape is directionally plausible.

Example:

RoofVista architectural shingles national average: $683 per square installed.
If material share is 55%, implied material cost is:

683×0.55=375.65

So RoofVista-implied material cost is roughly $376 per square, or $3.76/sq. ft., for architectural shingles.

That is much higher than a single bundle retail shingle price because an installed quote’s “materials” likely includes more than field shingles: accessories, waste, underlayment, flashing, fasteners, delivery, markup, and possibly scope conventions. That reinforces why our first model should clearly say field material only.

D. Validation source: SquareDash

SquareDash appears to be an instant roof-pricing platform. Its site says it uses satellite measurement and shows an “exact, all-inclusive price” after a user enters an address. I did not find a public API in the initial search. For now, treat SquareDash as a potential manual benchmark / later partnership source, not a programmable dataset.

E. Labor source: BLS OEWS + productivity research gap

You already have BLS labor wage data. The right occupation is likely:

47-2181 Roofers
Possibly construction laborers as secondary support labor.
Possibly first-line supervisors if modeling full installed cost.

BLS OEWS publishes wage data at national, state, metropolitan, and nonmetropolitan levels, including downloadable tables.

The missing variable is not wage. It is:

LaborHours=f(RoofArea,Material,Pitch,Complexity,TearOff,CrewSize,Access,Stories,Region,Code)

For now, we need a separate research task to find sources for roofing labor productivity, ideally in units of:

labor-hours per square,
squares installed per crew-day,
crew size by roof type,
adjustment factors for hip/gable/flat,
adjustment factors for pitch and stories.

The first search did not uncover a strong public paper. RoofVista can provide a calibration point, but it is not enough to estimate labor hours directly because it gives installed cost and labor/material share, not worker-hours.

3. Geography strategy
Preferred geography

Target geography should be:

ZIP/store level for scraped retailer prices.
Crosswalk ZIP/store to:
county,
CBSA/MSA,
state,
Census region.
Aggregate to MSA, because that matches the BLS labor data structure.

BLS OEWS publishes metropolitan and nonmetropolitan area data, which makes MSA a natural modeling unit.

Geographic model

For every product:

Price
product,zip,date
	​

→Price
product,store,date
	​

→Price
material_tier,MSA,date
	​


Recommended aggregation:

Product-level scrape.
Normalize to price per square.
Winsorize extreme product prices.
Compute median by retailer/material tier/MSA/date.
Also keep min/max/p25/p75.

Output:

MSA	material_tier	retailer	median_price_per_square	p25	p75	product_count	store_count	scrape_date
4. Product taxonomy

We need a controlled taxonomy before scraping.

material_taxonomy
material_group	material_tier	inclusion rules
asphalt	3_tab	product type says 3-tab, strip shingle
asphalt	architectural	architectural, dimensional, laminated
asphalt	premium_architectural	designer, luxury, premium, impact-resistant if priced above threshold
metal	corrugated_panel	corrugated, ribbed, exposed fastener
metal	standing_seam	standing seam, concealed fastener
tile	clay_tile	clay, terra cotta
tile	concrete_tile	concrete roof tile
tile	clay_concrete_unspecified	tile where material is ambiguous
Mapping method

Use a hybrid rule-based classifier first:

IF product_type contains "3-Tab" -> asphalt_3_tab
IF product_type contains "Architectural" OR name contains "Timberline" / "Oakridge" / "Duration" -> asphalt_architectural
IF name contains "Designer" / "Grand" / "Premium" / "Camelot" / "Presidential" / "Berkshire" -> asphalt_premium_architectural
IF name contains "corrugated" / "ribbed" / "panel" AND material contains steel/metal -> metal_corrugated_panel
IF name contains "standing seam" -> metal_standing_seam
IF material contains clay -> tile_clay
IF material contains concrete -> tile_concrete

Then manually audit the top SKUs.

5. Core model outputs
Output 1: field material retail cost

This is the cleanest output.

Cost
field_material
	​

=RoofArea
sqft
	​

×PricePerSqft
field_material
	​


or:

Cost
field_material
	​

=RoofSquares×PricePerSquare
field_material
	​


where:

RoofSquares=
100
RoofArea
sqft
	​

	​

Output 2: retail vs. bulk discount

For each SKU:

BulkDiscount=1−
RetailPrice
BulkPrice
	​


Then aggregate by product tier and geography.

Output 3: MSA material index

For each MSA:

MSAIndex
material
	​

=
MedianPrice
National,material
	​

MedianPrice
MSA,material
	​

	​


This gives a local retail material factor.

Output 4: validation against installed-cost platforms

Use RoofVista:

ImpliedMaterialCost
RV
	​

=InstalledCost
RV
	​

×MaterialShare
RV
	​


Compare:

Gap=ImpliedMaterialCost
RV
	​

−RetailFieldMaterialCost

Interpretation of gap:

accessories,
underlayment,
waste,
delivery,
contractor markup,
non-field materials,
scope differences,
labor/material split noise.
6. Coding plan
Repository structure
roofing-cost-model/
  config/
    materials_taxonomy.yml
    retailer_urls.yml
    geo_seed_zips.csv
  data_raw/
    retail_scrapes/
    roofvista/
    bls/
  data_intermediate/
    normalized_products.parquet
    product_classifications.parquet
    geo_crosswalk.parquet
  data_output/
    material_price_by_msa.parquet
    material_price_by_state.parquet
    roofvista_validation.parquet
  src/
    scrape_home_depot.py
    scrape_lowes.py
    fetch_roofvista.py
    fetch_bls_ppi.py
    normalize_products.py
    classify_materials.py
    build_geo_crosswalk.py
    aggregate_prices.py
    validate_against_roofvista.py
  notebooks/
    01_retail_price_exploration.ipynb
    02_roofvista_validation.ipynb
    03_msa_material_index.ipynb
Script 1: RoofVista API pull
# src/fetch_roofvista.py

import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = "https://roofvista.com/api/v1/public/pricing"

STATES = ["MA", "CT", "RI", "NH", "VT", "ME"]
MATERIALS = [
    "3-tab",
    "architectural",
    "premium",
    "tile",
    "standing_seam_metal",
]

def fetch_state(state: str) -> dict:
    response = requests.get(BASE_URL, params={"state": state}, timeout=30)
    response.raise_for_status()
    return response.json()

def fetch_state_material(state: str, material: str) -> dict:
    response = requests.get(
        BASE_URL,
        params={"state": state, "material": material},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()

def main():
    rows = []
    pulled_at = datetime.now(timezone.utc).isoformat()

    for state in STATES:
        try:
            payload = fetch_state(state)
            rows.append({
                "state": state,
                "material": None,
                "payload": payload,
                "pulled_at": pulled_at,
                "source": BASE_URL,
            })
        except Exception as exc:
            rows.append({
                "state": state,
                "material": None,
                "payload": None,
                "pulled_at": pulled_at,
                "source": BASE_URL,
                "error": str(exc),
            })

        for material in MATERIALS:
            try:
                payload = fetch_state_material(state, material)
                rows.append({
                    "state": state,
                    "material": material,
                    "payload": payload,
                    "pulled_at": pulled_at,
                    "source": BASE_URL,
                })
            except Exception as exc:
                rows.append({
                    "state": state,
                    "material": material,
                    "payload": None,
                    "pulled_at": pulled_at,
                    "source": BASE_URL,
                    "error": str(exc),
                })

    df = pd.DataFrame(rows)
    out = Path("data_raw/roofvista")
    out.mkdir(parents=True, exist_ok=True)
    df.to_json(out / f"roofvista_pricing_{pulled_at[:10]}.jsonl", orient="records", lines=True)

if __name__ == "__main__":
    main()

Note: the material parameter names may need to be adjusted after seeing the actual API response schema.

Script 2: retailer product normalization
# src/normalize_products.py

import pandas as pd
import re

def extract_coverage_sqft(text: str) -> float | None:
    if not isinstance(text, str):
        return None

    patterns = [
        r"(\d+\.?\d*)\s*sq\.?\s*ft\.?\s*per\s*bundle",
        r"(\d+\.?\d*)\s*sq\.?\s*ft",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))

    return None

def price_per_square(price_per_unit: float, coverage_sqft: float) -> float | None:
    if not price_per_unit or not coverage_sqft:
        return None
    return price_per_unit / coverage_sqft * 100

def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["coverage_sqft_per_unit"] = df.apply(
        lambda row: row.get("coverage_sqft_per_unit")
        or extract_coverage_sqft(row.get("product_name", "")),
        axis=1,
    )

    df["price_per_square"] = df.apply(
        lambda row: price_per_square(
            row.get("retail_price_per_unit"),
            row.get("coverage_sqft_per_unit"),
        ),
        axis=1,
    )

    df["bulk_price_per_square"] = df.apply(
        lambda row: price_per_square(
            row.get("bulk_price_per_unit"),
            row.get("coverage_sqft_per_unit"),
        ),
        axis=1,
    )

    df["bulk_discount_pct"] = (
        1 - df["bulk_price_per_unit"] / df["retail_price_per_unit"]
    )

    return df
Script 3: material classifier
# src/classify_materials.py

import pandas as pd

def classify_product(row: pd.Series) -> str:
    name = str(row.get("product_name", "")).lower()
    product_type = str(row.get("roofing_product_type", "")).lower()
    material = str(row.get("material", "")).lower()
    brand = str(row.get("brand", "")).lower()

    text = " ".join([name, product_type, material, brand])

    if "3-tab" in text or "three tab" in text:
        return "asphalt_3_tab"

    premium_terms = [
        "premium", "designer", "luxury", "grand", "camelot",
        "presidential", "berkshire", "grand manor", "impact resistant",
        "class 4",
    ]
    if any(term in text for term in premium_terms) and "shingle" in text:
        return "asphalt_premium_architectural"

    architectural_terms = [
        "architectural", "dimensional", "laminated",
        "timberline", "oakridge", "duration",
    ]
    if any(term in text for term in architectural_terms):
        return "asphalt_architectural"

    if "standing seam" in text:
        return "metal_standing_seam"

    if "corrugated" in text or "ribbed" in text or "exposed fastener" in text:
        if "metal" in text or "steel" in text or "galvalume" in text or "aluminum" in text:
            return "metal_corrugated_panel"

    if "clay" in text or "terra cotta" in text:
        return "tile_clay"

    if "concrete" in text and "tile" in text:
        return "tile_concrete"

    if "tile" in text:
        return "tile_unspecified"

    return "unclassified"

def main(input_path: str, output_path: str):
    df = pd.read_parquet(input_path)
    df["material_class"] = df.apply(classify_product, axis=1)
    df.to_parquet(output_path, index=False)
Script 4: aggregation to MSA
# src/aggregate_prices.py

import pandas as pd

def aggregate_to_msa(products: pd.DataFrame) -> pd.DataFrame:
    usable = products[
        products["material_class"].notna()
        & products["price_per_square"].notna()
        & products["cbsa_code"].notna()
    ].copy()

    grouped = (
        usable
        .groupby(["scrape_date", "retailer", "cbsa_code", "cbsa_name", "material_class"])
        .agg(
            median_price_per_square=("price_per_square", "median"),
            p25_price_per_square=("price_per_square", lambda x: x.quantile(0.25)),
            p75_price_per_square=("price_per_square", lambda x: x.quantile(0.75)),
            min_price_per_square=("price_per_square", "min"),
            max_price_per_square=("price_per_square", "max"),
            product_count=("product_id", "nunique"),
            store_count=("store_id", "nunique"),
        )
        .reset_index()
    )

    national = (
        usable
        .groupby(["scrape_date", "retailer", "material_class"])
        .agg(national_median_price_per_square=("price_per_square", "median"))
        .reset_index()
    )

    grouped = grouped.merge(
        national,
        on=["scrape_date", "retailer", "material_class"],
        how="left",
    )

    grouped["msa_material_index"] = (
        grouped["median_price_per_square"]
        / grouped["national_median_price_per_square"]
    )

    return grouped
7. Immediate research tasks still needed
Task 1: Confirm retailer scraping approach

We need to decide whether to use:

HTML parsing.
Retailer internal API endpoints.
Browser automation.
Third-party scraping provider.

Home Depot’s public page contains enough structured page text to prove the concept: it exposes product names, model numbers, prices, unit prices, coverage, bulk discount, and local price caveat.

Task 2: Lowe’s feasibility

Initial search did not yield a clean Lowe’s scrapeable page in the web tool. We should separately inspect Lowe’s category pages through a browser/devtools workflow or scraper prototype. Do not assume Lowe’s is as easy as Home Depot until tested.

Task 3: RoofVista API schema test

RoofVista publicly documents API endpoints, but the next coding step is to call the endpoint and inspect the JSON schema.

Task 4: Labor-hours source

We still need a defensible public or semi-public source for:

crew size,
squares installed per day,
labor-hours per square,
pitch/complexity adjustment,
hip vs. gable vs. flat adjustment.

This is currently the largest gap for converting BLS wages into installed labor cost.

Task 5: MSA / ZIP crosswalk

We need a ZIP-to-CBSA/MSA crosswalk, likely HUD USPS ZIP-CBSA or Census/HUD crosswalk. This will let us align retailer ZIP/store pricing with BLS OEWS MSA wage data.

8. Revised research questions

The next round should answer these precisely:

Can Home Depot prices be collected reliably by ZIP/store for roofing SKUs without account login?
Can Lowe’s prices be collected reliably by ZIP/store?
Which retailer categories contain the relevant material-only products for:
3-tab asphalt,
architectural asphalt,
premium asphalt,
corrugated metal,
standing seam metal,
clay tile,
concrete tile?
Does RoofVista’s API expose only state/material averages, or also municipal/ZIP-level pricing?
Does RoofVista’s API return labor/material split fields directly?
Can we find a credible public productivity source for roofer labor-hours per square?
Should our first model estimate only field covering cost, or should it add a “material system multiplier” to approximate accessories, waste, underlayment, fasteners, and delivery?
9. Recommended first build

I would build the first version in this order:

Home Depot scraper only.
Asphalt shingles only.
Three classes: 3-tab, architectural, premium architectural.
Collect prices for a seed ZIP list mapped to MSAs.
Normalize all products to $/square.
Compute retail and public bulk-price curves.
Join to BLS/FRED index series for time adjustment.
Pull RoofVista API for validation in its covered states.
Compare:
Home Depot field-shingle retail price,
Home Depot field-shingle bulk price,
RoofVista implied material cost,
RoofVista installed cost.
Expand to metal and tile after the asphalt pipeline is stable.

This gives us a defensible first deliverable without waiting on proprietary data.