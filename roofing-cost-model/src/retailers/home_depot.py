"""Home Depot roofing-material scraper.

Collects Home Depot roofing product data by ZIP code and category, stores raw
responses for audit, and emits structured product rows (JSONL) that the
downstream ``src.normalize_products`` module consumes.

Design notes:
  * Network access is isolated from parsing. ``extract_products_from_html`` and
    the small helpers are pure functions and are unit-tested without a network.
  * Two source methods are supported behind one interface:
        - "requests": fast HTTP + embedded-JSON / HTML parsing (tried first).
        - "playwright": optional browser fallback (only if installed and the
          requests path yields nothing).
    Downstream code only reads ``source_method`` for provenance; the row schema
    is identical either way.
  * robots.txt is honored, requests are rate-limited, and transient failures
    are retried with exponential backoff.

Usage:
    python -m src.retailers.home_depot \
        --zip-codes 90001,33101 --categories asphalt_shingles --max-pages 2
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlparse

import requests

try:  # YAML is required for category config but degrade gracefully.
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

try:  # Optional, only needed for the HTML card fallback.
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

logger = logging.getLogger(__name__)

RETAILER = "Home Depot"
BASE_URL = "https://www.homedepot.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "(roofing-cost-model research scraper; contact: research@example.org)"
)

# Canonical product row schema. Every emitted row contains exactly these keys.
PRODUCT_FIELDS = [
    "retailer",
    "scrape_date",
    "scrape_timestamp",
    "zip_code",
    "store_id",
    "store_name",
    "category_key",
    "category_url",
    "product_id",
    "sku",
    "model_number",
    "product_name",
    "brand",
    "product_url",
    "breadcrumb",
    "rating",
    "review_count",
    "retail_price_per_unit",
    "bulk_price_per_unit",
    "bulk_threshold",
    "unit_text",
    "coverage_sqft_per_unit",
    "price_per_sqft_raw",
    "availability_status",
    "shipping_status",
    "pickup_status",
    "color",
    "material",
    "product_type",
    "warranty",
    "fire_rating",
    "impact_rating",
    "source_url",
    "raw_json",
    "raw_html_path",
    "source_method",
]


# --------------------------------------------------------------------------- #
# Config / input loading
# --------------------------------------------------------------------------- #
def load_categories(path: str) -> dict[str, dict[str, Any]]:
    """Load category config YAML. Returns {category_key: {url, ...}}."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Category config not found: {path}")
    if yaml is None:  # pragma: no cover
        raise RuntimeError("PyYAML is required to read the category config.")
    with open(p, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("categories", {}) or {}


def load_zip_codes(path: str) -> list[str]:
    """Load ZIP codes from the geo seed CSV (zip_code column)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Geo seed file not found: {path}")
    zips: list[str] = []
    with open(p, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            z = (row.get("zip_code") or "").strip()
            if z:
                zips.append(z)
    return zips


# --------------------------------------------------------------------------- #
# robots.txt
# --------------------------------------------------------------------------- #
def load_robots(base_url: str, user_agent: str) -> urllib.robotparser.RobotFileParser:
    """Fetch and parse robots.txt for the host. Failures => permissive parser."""
    rp = urllib.robotparser.RobotFileParser()
    robots_url = urljoin(base_url, "/robots.txt")
    rp.set_url(robots_url)
    try:
        rp.read()
        logger.info("Loaded robots.txt from %s", robots_url)
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Could not read robots.txt (%s); proceeding cautiously.", exc)
    return rp


def can_fetch(rp: urllib.robotparser.RobotFileParser, user_agent: str, url: str) -> bool:
    try:
        return rp.can_fetch(user_agent, url)
    except Exception:  # pragma: no cover
        return True


# --------------------------------------------------------------------------- #
# HTTP session with retry/backoff
# --------------------------------------------------------------------------- #
def build_session(user_agent: str = DEFAULT_USER_AGENT) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def fetch_url(
    session: requests.Session,
    url: str,
    *,
    max_retries: int = 4,
    base_backoff: float = 1.5,
    timeout: int = 30,
    params: Optional[dict[str, Any]] = None,
) -> requests.Response:
    """GET a URL with exponential backoff on transient failures.

    Raises the last exception if all retries are exhausted.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"Transient status {resp.status_code}")
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001 - we retry broadly on transient errors
            last_exc = exc
            sleep_for = base_backoff ** attempt + random.uniform(0, 0.5)
            logger.warning(
                "Request failed (attempt %d/%d) for %s: %s; backing off %.1fs",
                attempt, max_retries, url, exc, sleep_for,
            )
            time.sleep(sleep_for)
    assert last_exc is not None
    raise last_exc


def set_zip_context(session: requests.Session, zip_code: str) -> Optional[str]:
    """Best-effort localization of the session to a ZIP code.

    Home Depot localizes via cookies / an internal API that is not part of a
    stable public contract. We set the commonly-observed delivery-ZIP cookie so
    listing prices reflect the target ZIP where possible. Returns a store id if
    one can be discovered, else None. Never raises.
    """
    try:
        session.cookies.set("DELIVERY_ZIP", zip_code, domain=".homedepot.com")
        session.cookies.set("THD_LOCALIZED", zip_code, domain=".homedepot.com")
    except Exception as exc:  # pragma: no cover
        logger.debug("Could not set ZIP cookies for %s: %s", zip_code, exc)
    return None


# --------------------------------------------------------------------------- #
# Parsing helpers (pure functions, unit-tested)
# --------------------------------------------------------------------------- #
_APOLLO_RE = re.compile(
    r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.DOTALL
)
_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _coerce_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def _iter_ldjson_blocks(html: str) -> Iterable[Any]:
    for match in _LDJSON_RE.finditer(html or ""):
        block = match.group(1).strip()
        if not block:
            continue
        try:
            yield json.loads(block)
        except json.JSONDecodeError:
            continue


def _flatten_ldjson_products(obj: Any) -> list[dict[str, Any]]:
    """Pull Product nodes out of arbitrary JSON-LD (ItemList, @graph, etc.)."""
    products: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if "Product" in types:
                products.append(node)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(obj)
    return products


def _product_from_ldjson(node: dict[str, Any]) -> dict[str, Any]:
    """Map a JSON-LD Product node to a partial product row."""
    offers = node.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}

    brand = node.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")

    rating_obj = node.get("aggregateRating") or {}

    return {
        "product_id": node.get("productID") or node.get("sku"),
        "sku": node.get("sku"),
        "model_number": node.get("mpn"),
        "product_name": node.get("name"),
        "brand": brand,
        "product_url": node.get("url"),
        "retail_price_per_unit": _coerce_number(offers.get("price")),
        "availability_status": (offers.get("availability") or "").split("/")[-1] or None,
        "rating": _coerce_number(rating_obj.get("ratingValue")),
        "review_count": _coerce_number(rating_obj.get("reviewCount")),
        "color": node.get("color"),
        "material": node.get("material"),
        "raw_json": json.dumps(node, ensure_ascii=False),
    }


def extract_products_from_html(html: str) -> list[dict[str, Any]]:
    """Extract partial product rows from a listing page's HTML.

    Strategy order:
      1. JSON-LD Product nodes (most stable, schema.org).
      2. window.__APOLLO_STATE__ Product entries (Home Depot client cache).
    Returns a list of partial rows (missing keys are filled by the caller).
    """
    products: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # 1) JSON-LD
    for block in _iter_ldjson_blocks(html):
        for node in _flatten_ldjson_products(block):
            row = _product_from_ldjson(node)
            key = str(row.get("product_id") or row.get("product_url") or row.get("product_name"))
            if key and key not in seen_ids:
                seen_ids.add(key)
                products.append(row)

    if products:
        return products

    # 2) Apollo state fallback
    apollo_match = _APOLLO_RE.search(html or "")
    if apollo_match:
        try:
            state = json.loads(apollo_match.group(1))
        except json.JSONDecodeError:
            state = None
        if isinstance(state, dict):
            for key, value in state.items():
                if not isinstance(value, dict):
                    continue
                if not key.lower().startswith("product"):
                    continue
                identifiers = value.get("identifiers") or {}
                pricing = value.get("pricing") or {}
                row = {
                    "product_id": value.get("itemId") or identifiers.get("itemId"),
                    "sku": identifiers.get("skuId"),
                    "model_number": identifiers.get("modelNumber"),
                    "product_name": identifiers.get("productLabel") or value.get("productLabel"),
                    "brand": identifiers.get("brandName"),
                    "product_type": identifiers.get("productType"),
                    "retail_price_per_unit": _coerce_number(pricing.get("value")),
                    "raw_json": json.dumps(value, ensure_ascii=False),
                }
                pid = str(row.get("product_id") or row.get("product_name") or "")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    products.append(row)

    return products


def paginate_url(category_url: str, page_index: int, page_size: int = 24) -> str:
    """Return the URL for a given page. Page 0 is the bare category URL.

    Home Depot uses an N-offset segment (``/Nao-<offset>``) for pagination.
    """
    if page_index <= 0:
        return category_url
    offset = page_index * page_size
    sep = "" if category_url.endswith("/") else "/"
    return f"{category_url}{sep}Nao-{offset}"


# --------------------------------------------------------------------------- #
# Scraper orchestration
# --------------------------------------------------------------------------- #
@dataclass
class ScrapeConfig:
    out_dir: str = "data_raw/home_depot"
    sleep_seconds: float = 2.0
    max_pages: Optional[int] = None
    headless: bool = True
    user_agent: str = DEFAULT_USER_AGENT
    use_playwright_fallback: bool = True
    # When set, skip the network entirely and parse pre-saved HTML files from
    # this directory instead. Lets you feed pages captured from a normal browser
    # session (or a sanctioned unblocker/proxy) through the same pipeline.
    local_html_dir: Optional[str] = None


@dataclass
class RunPaths:
    day_dir: Path
    raw_dir: Path
    products_path: Path
    errors_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.errors_path = self.day_dir / "home_depot_errors.jsonl"


def _make_run_paths(out_dir: str, day: str) -> RunPaths:
    day_dir = Path(out_dir) / day
    raw_dir = day_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    products_path = day_dir / "home_depot_products_raw.jsonl"
    return RunPaths(day_dir=day_dir, raw_dir=raw_dir, products_path=products_path)


def _empty_row(**overrides: Any) -> dict[str, Any]:
    row = {field_name: None for field_name in PRODUCT_FIELDS}
    row.update(overrides)
    return row


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _log_error(path: Path, **payload: Any) -> None:
    payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _save_raw_html(raw_dir: Path, zip_code: str, category_key: str, page_index: int, html: str) -> str:
    safe = f"{category_key}_{zip_code}_p{page_index}.html"
    path = raw_dir / safe
    path.write_text(html, encoding="utf-8")
    return str(path)


# A realistic browser UA (no "scraper" tag) for the Playwright path -- Akamai
# fingerprints headless/automation, so the browser context must look ordinary.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Markers that indicate Akamai Bot Manager served a challenge/block page rather
# than real catalog content.
_BLOCK_MARKERS = (
    "Something went wrong",
    "Error Page",
    "Access Denied",
    "Pardon Our Interruption",
    "akamai",
)


def _looks_blocked(html: Optional[str]) -> bool:
    """Heuristically detect an anti-bot challenge / error page."""
    if not html:
        return True
    if len(html) < 6000:  # real listing pages are large
        lowered = html.lower()
        return any(m.lower() in lowered for m in _BLOCK_MARKERS)
    return False


def _fetch_listing_html_playwright(
    url: str,
    headless: bool,
    zip_code: Optional[str] = None,
    *,
    max_attempts: int = 3,
) -> Optional[str]:
    """Optional Playwright fallback rendering the page in a real browser.

    Hardened against basic bot fingerprinting: realistic UA/viewport/locale, the
    ``navigator.webdriver`` flag removed, ZIP localization cookies seeded, a
    homepage "warm-up" so Akamai's sensor JS can set a valid cookie, a
    network-idle wait, and a lazy-load scroll. Retries when a challenge/block
    page is detected. Returns HTML or None.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("Playwright not installed; skipping browser fallback.")
        return None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                user_agent=_BROWSER_USER_AGENT,
                viewport={"width": 1366, "height": 900},
                locale="en-US",
                timezone_id="America/New_York",
            )
            # Hide the most common automation tell.
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            if zip_code:
                context.add_cookies(
                    [
                        {"name": "DELIVERY_ZIP", "value": zip_code, "domain": ".homedepot.com", "path": "/"},
                        {"name": "THD_LOCALIZED", "value": zip_code, "domain": ".homedepot.com", "path": "/"},
                    ]
                )

            page = context.new_page()

            # Warm-up: visit the homepage so Akamai's sensor script can run and
            # set a valid _abck cookie before we request the catalog page.
            try:
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
            except Exception as exc:
                logger.debug("Homepage warm-up failed: %s", exc)

            html: Optional[str] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:
                        pass
                    # Trigger lazy-loaded product pods.
                    try:
                        for _ in range(4):
                            page.mouse.wheel(0, 4000)
                            page.wait_for_timeout(800)
                    except Exception:
                        pass
                    html = page.content()
                except Exception as exc:
                    logger.warning("Playwright navigation failed (attempt %d): %s", attempt, exc)
                    html = None

                if not _looks_blocked(html):
                    break

                logger.warning(
                    "Anti-bot challenge detected (attempt %d/%d) for %s; retrying.",
                    attempt, max_attempts, url,
                )
                page.wait_for_timeout(3000 * attempt)

            context.close()
            browser.close()
            if _looks_blocked(html):
                logger.warning("Still blocked after %d attempts for %s.", max_attempts, url)
            return html
    except Exception as exc:  # pragma: no cover - browser dependent
        logger.warning("Playwright fetch failed for %s: %s", url, exc)
        return None


_LOCAL_FILENAME_RE = re.compile(r"^(?P<category>.+?)_(?P<zip>\d{5})_p(?P<page>\d+)\.html$")


def _save_page_path(save_dir: str, category_key: str, zip_code: Optional[str], page_index: int) -> Path:
    """Build the standardized capture path '<category>_<zip>_pN.html'.

    Matches the convention ``ingest_local_html`` parses, so saved pages are
    auto-attributed on ingestion.
    """
    zip_part = zip_code if zip_code else "00000"
    return Path(save_dir) / f"{category_key}_{zip_part}_p{page_index}.html"


def _build_rows(
    products: list[dict[str, Any]],
    *,
    day: str,
    timestamp: str,
    zip_code: Optional[str],
    store_id: Optional[str],
    category_key: Optional[str],
    category_url: Optional[str],
    source_url: Optional[str],
    raw_html_path: Optional[str],
    source_method: str,
) -> list[dict[str, Any]]:
    """Turn partial extracted products into full schema rows."""
    rows = []
    for partial in products:
        product_url = partial.get("product_url")
        if product_url and product_url.startswith("/"):
            product_url = urljoin(BASE_URL, product_url)
        row = _empty_row(
            retailer=RETAILER,
            scrape_date=day,
            scrape_timestamp=timestamp,
            zip_code=zip_code,
            store_id=store_id,
            category_key=category_key,
            category_url=category_url,
            source_url=source_url,
            raw_html_path=raw_html_path,
            source_method=source_method,
        )
        for key, value in partial.items():
            if key in row:
                row[key] = value
        if product_url:
            row["product_url"] = product_url
        rows.append(row)
    return rows


def ingest_local_html(
    local_dir: str,
    categories: dict[str, dict[str, Any]],
    config: ScrapeConfig,
) -> Path:
    """Parse pre-saved HTML files instead of hitting the network.

    Files are matched by the convention ``<category>_<zip>_p<page>.html`` (the
    same naming the live scraper uses for raw captures). Files that do not match
    fall back to the first configured category and an unknown ZIP. This lets you
    feed pages saved from a normal browser session (fully within Home Depot's
    terms) through the identical extract pipeline.
    """
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    paths = _make_run_paths(config.out_dir, day)

    src_dir = Path(local_dir)
    if not src_dir.exists():
        raise FileNotFoundError(f"Local HTML directory not found: {local_dir}")

    default_category = next(iter(categories), None)
    total_products = 0
    files = sorted(src_dir.glob("*.html"))
    logger.info("Ingesting %d local HTML file(s) from %s", len(files), src_dir)

    for html_file in files:
        match = _LOCAL_FILENAME_RE.match(html_file.name)
        if match:
            category_key = match.group("category")
            zip_code: Optional[str] = match.group("zip")
        else:
            category_key = default_category
            zip_code = None
            logger.info(
                "Filename %s does not match '<category>_<zip>_pN.html'; "
                "using category=%s zip=None.",
                html_file.name, category_key,
            )

        category_url = (categories.get(category_key, {}) or {}).get("url") if category_key else None

        try:
            html = html_file.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.error("Could not read %s: %s", html_file, exc)
            _log_error(paths.errors_path, source_file=str(html_file), error=f"read_failed: {exc}")
            continue

        products = extract_products_from_html(html)
        if not products:
            logger.warning("No products extracted from %s", html_file.name)
            _log_error(
                paths.errors_path,
                source_file=str(html_file), category=category_key, zip_code=zip_code,
                error="no_products_extracted",
            )
            continue

        rows = _build_rows(
            products,
            day=day,
            timestamp=now.isoformat(),
            zip_code=zip_code,
            store_id=None,
            category_key=category_key,
            category_url=category_url,
            source_url=str(html_file),
            raw_html_path=str(html_file),
            source_method="local_html",
        )
        _write_jsonl(paths.products_path, rows)
        total_products += len(rows)
        logger.info("Extracted %d products from %s", len(rows), html_file.name)

    logger.info(
        "Local ingestion complete: %d total products -> %s",
        total_products, paths.products_path,
    )
    return paths.products_path


def save_pages_interactive(
    zip_codes: list[str],
    categories: dict[str, dict[str, Any]],
    config: ScrapeConfig,
    save_dir: str = "local_pages",
    max_pages: int = 1,
    prompt: Any = input,
) -> list[Path]:
    """Guided, human-in-the-loop page capture.

    Opens a real (headful, persistent-profile) browser at each category page so
    you can solve any anti-bot challenge and set your store/ZIP once, then press
    Enter to capture the fully-rendered HTML. Files are written with the
    ``<category>_<zip>_pN.html`` convention so ``--local-html-dir`` ingests them
    directly.

    The browser profile persists under ``<save_dir>/.browser_profile`` so the
    Akamai cookie survives between captures and runs.

    Returns the list of saved file paths.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(
            "Playwright is required for --save-page. Install it with:\n"
            "  pip install playwright\n  playwright install chromium"
        )
        return []

    out = Path(save_dir)
    out.mkdir(parents=True, exist_ok=True)
    profile_dir = out / ".browser_profile"
    saved: list[Path] = []

    print("\n=== Home Depot page capture ===")
    print(f"Pages will be saved to: {out.resolve()}")
    print("At each prompt: press Enter to capture, 's' to skip, 'q' to quit.\n")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=_BROWSER_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.pages[0] if context.pages else context.new_page()

        quit_all = False
        for zip_code in zip_codes:
            if quit_all:
                break
            if zip_code:
                try:
                    context.add_cookies(
                        [
                            {"name": "DELIVERY_ZIP", "value": zip_code, "domain": ".homedepot.com", "path": "/"},
                            {"name": "THD_LOCALIZED", "value": zip_code, "domain": ".homedepot.com", "path": "/"},
                        ]
                    )
                except Exception as exc:
                    logger.debug("Could not seed cookies for %s: %s", zip_code, exc)

            for category_key, cat in categories.items():
                if quit_all:
                    break
                category_url = (cat or {}).get("url") or ""
                if not category_url:
                    logger.info("Category %s has no URL; skipping.", category_key)
                    continue

                for page_index in range(max(1, max_pages)):
                    target = paginate_url(category_url, page_index)
                    try:
                        page.goto(target, wait_until="domcontentloaded", timeout=60000)
                    except Exception as exc:
                        logger.warning("Navigation to %s failed: %s", target, exc)

                    print(f"\n[zip={zip_code} category={category_key} page={page_index}]")
                    print(f"  URL: {target}")
                    answer = str(
                        prompt("  Set store/ZIP if needed, then press Enter to capture (s=skip, q=quit): ")
                    ).strip().lower()

                    if answer == "q":
                        quit_all = True
                        break
                    if answer == "s":
                        print("  Skipped.")
                        continue

                    try:
                        html = page.content()
                    except Exception as exc:
                        logger.error("Could not read page content: %s", exc)
                        continue

                    dest = _save_page_path(save_dir, category_key, zip_code, page_index)
                    dest.write_text(html, encoding="utf-8")
                    saved.append(dest)

                    if _looks_blocked(html):
                        print(
                            f"  WARNING: captured page looks like an anti-bot/challenge page "
                            f"({len(html)} bytes). Solve it in the browser and re-capture."
                        )
                    print(f"  Saved {dest}")

        context.close()

    print(f"\nCaptured {len(saved)} page(s) into {out.resolve()}")
    if saved:
        print("Next, ingest them with:")
        print(f"  python -m src.retailers.home_depot --local-html-dir {save_dir}")
    return saved


def scrape(
    zip_codes: list[str],
    categories: dict[str, dict[str, Any]],
    config: ScrapeConfig,
) -> Path:
    """Run the full scrape. Returns the path to the products JSONL file."""
    if config.local_html_dir:
        logger.info("local_html_dir set; parsing saved HTML instead of scraping.")
        return ingest_local_html(config.local_html_dir, categories, config)

    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    paths = _make_run_paths(config.out_dir, day)

    session = build_session(config.user_agent)
    robots = load_robots(BASE_URL, config.user_agent)

    total_products = 0

    for zip_code in zip_codes:
        store_id = set_zip_context(session, zip_code)

        for category_key, cat in categories.items():
            category_url = (cat or {}).get("url") or ""
            if not category_url:
                logger.info("Category %s has no URL; skipping.", category_key)
                continue

            page_index = 0
            while True:
                if config.max_pages is not None and page_index >= config.max_pages:
                    break

                url = paginate_url(category_url, page_index)

                if not can_fetch(robots, config.user_agent, url):
                    logger.warning("robots.txt disallows %s; skipping.", url)
                    _log_error(
                        paths.errors_path,
                        zip_code=zip_code, category=category_key, url=url,
                        error="disallowed_by_robots",
                    )
                    break

                logger.info(
                    "Fetching zip=%s category=%s page=%d url=%s",
                    zip_code, category_key, page_index, url,
                )

                html: Optional[str] = None
                source_method = "requests"
                try:
                    resp = fetch_url(session, url)
                    html = resp.text
                except Exception as exc:  # noqa: BLE001
                    logger.error("requests fetch failed for %s: %s", url, exc)
                    _log_error(
                        paths.errors_path,
                        zip_code=zip_code, category=category_key, url=url,
                        error=f"requests_fetch_failed: {exc}",
                    )

                products: list[dict[str, Any]] = []
                if html:
                    products = extract_products_from_html(html)

                # Browser fallback if requests yielded nothing.
                if not products and config.use_playwright_fallback:
                    fallback_html = _fetch_listing_html_playwright(
                        url, config.headless, zip_code=zip_code
                    )
                    if fallback_html:
                        html = fallback_html
                        source_method = "playwright"
                        products = extract_products_from_html(html)

                if html is None:
                    break

                raw_html_path = _save_raw_html(
                    paths.raw_dir, zip_code, category_key, page_index, html
                )

                if not products:
                    logger.info(
                        "No products extracted for zip=%s category=%s page=%d "
                        "(raw saved to %s).",
                        zip_code, category_key, page_index, raw_html_path,
                    )
                    _log_error(
                        paths.errors_path,
                        zip_code=zip_code, category=category_key, url=url,
                        error="no_products_extracted", raw_html_path=raw_html_path,
                    )
                    break

                rows = _build_rows(
                    products,
                    day=day,
                    timestamp=now.isoformat(),
                    zip_code=zip_code,
                    store_id=store_id,
                    category_key=category_key,
                    category_url=category_url,
                    source_url=url,
                    raw_html_path=raw_html_path,
                    source_method=source_method,
                )

                _write_jsonl(paths.products_path, rows)
                total_products += len(rows)
                logger.info(
                    "Extracted %d products (zip=%s category=%s page=%d)",
                    len(rows), zip_code, category_key, page_index,
                )

                page_index += 1
                time.sleep(config.sleep_seconds + random.uniform(0, 0.5))

    logger.info(
        "Scrape complete: %d total products -> %s", total_products, paths.products_path
    )
    return paths.products_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_csv_arg(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Home Depot roofing-material scraper.")
    parser.add_argument("--zip-codes", help="Comma-separated ZIP override.")
    parser.add_argument("--categories", help="Comma-separated category-key override.")
    parser.add_argument("--out-dir", default="data_raw/home_depot")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--sleep-seconds", type=float, default=2.0)
    parser.add_argument("--headless", action="store_true", help="Run browser fallback headless.")
    parser.add_argument(
        "--no-playwright",
        action="store_true",
        help="Disable the Playwright browser fallback.",
    )
    parser.add_argument(
        "--local-html-dir",
        default=None,
        help=(
            "Parse pre-saved HTML files from this directory instead of "
            "hitting the network. Files named '<category>_<zip>_pN.html' are "
            "attributed automatically."
        ),
    )
    parser.add_argument(
        "--save-page",
        action="store_true",
        help=(
            "Guided capture: open a real browser at each category page so you "
            "can solve any challenge / set your store, then press Enter to save "
            "the rendered HTML for later --local-html-dir ingestion."
        ),
    )
    parser.add_argument(
        "--save-dir",
        default="local_pages",
        help="Destination directory for --save-page captures.",
    )
    parser.add_argument("--categories-config", default="config/home_depot_categories.yml")
    parser.add_argument("--geo-seed", default="config/geo_seed_zips.csv")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # ZIP codes: CLI override or seed file.
    zip_override = _parse_csv_arg(args.zip_codes)
    zip_codes = zip_override if zip_override else load_zip_codes(args.geo_seed)

    # Categories: load config, optionally filter by override keys.
    all_categories = load_categories(args.categories_config)
    cat_override = _parse_csv_arg(args.categories)
    if cat_override:
        categories = {k: all_categories[k] for k in cat_override if k in all_categories}
        missing = [k for k in cat_override if k not in all_categories]
        if missing:
            logger.warning("Unknown categories ignored: %s", ", ".join(missing))
    else:
        categories = all_categories

    if not zip_codes and not args.local_html_dir:
        logger.error("No ZIP codes to scrape.")
        return
    if not categories:
        logger.error("No categories to scrape.")
        return

    config = ScrapeConfig(
        out_dir=args.out_dir,
        sleep_seconds=args.sleep_seconds,
        max_pages=args.max_pages,
        headless=args.headless,
        use_playwright_fallback=not args.no_playwright,
        local_html_dir=args.local_html_dir,
    )

    if args.save_page:
        save_pages_interactive(
            zip_codes,
            categories,
            config,
            save_dir=args.save_dir,
            max_pages=args.max_pages or 1,
        )
        return

    scrape(zip_codes, categories, config)


if __name__ == "__main__":
    main()
