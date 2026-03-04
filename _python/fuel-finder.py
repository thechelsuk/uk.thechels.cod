import helper
import json
import os
import pathlib
import math
import datetime
import time
import sys
import requests

# Load .env file for local development if present
_env_file = pathlib.Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# -- Configuration ------------------------------------------------------------

BASE_URL        = "https://www.fuel-finder.service.gov.uk"
AUTH_PATH       = "/api/v1/oauth/generate_access_token"
PFS_PATH        = "/api/v1/pfs"
PRICES_PATH     = "/api/v1/pfs/fuel-prices"

CHELTENHAM_LAT  = 51.899
CHELTENHAM_LON  = -2.078
RADIUS_MILES    = 20
EARTH_RADIUS_MI = 3958.8
BATCH_DELAY_SECS     = 1.5   # pause between paginated requests to avoid hammering the API
PRICE_LOOKBACK_DAYS  = 30   # how far back the daily price fetch looks

# Human-readable names for API fuel type codes
FUEL_LABELS = {
    "E5":           "Premium Unleaded",
    "E10":          "Unleaded",
    "B7_STANDARD":  "Diesel",
    "B7_PREMIUM":   "Premium Diesel",
    "SDV5":         "Super Diesel",
}


def fuel_label(code):
    return FUEL_LABELS.get(code, code)

# -- Helpers ------------------------------------------------------------------

def haversine_miles(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi       = math.radians(lat2 - lat1)
    dlambda    = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_access_token(client_id, client_secret):
    url  = BASE_URL + AUTH_PATH
    resp = requests.post(url, json={"client_id": client_id, "client_secret": client_secret}, timeout=20)
    if not resp.ok:
        print(f"  Auth failed {resp.status_code}: {resp.text}")
        resp.raise_for_status()
    data = resp.json()
    if isinstance(data.get("data"), dict):
        return data["data"]["access_token"]
    return data["access_token"]


def fetch_all_pages(path, token, extra_params=None, label=""):
    """Fetch all paginated results, sleeping between batches to stay polite."""
    results = []
    batch   = 1
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        params = {"batch-number": batch}
        if extra_params:
            params.update(extra_params)
        for attempt in range(4):
            try:
                resp = requests.get(
                    BASE_URL + path,
                    headers=headers,
                    params=params,
                    timeout=(10, 90),
                )
                if resp.status_code == 504:
                    wait = BATCH_DELAY_SECS * (attempt + 2)
                    print(f"  {label}batch {batch}: 504 timeout (attempt {attempt + 1}/4), waiting {wait}s...")
                    time.sleep(wait)
                    if attempt == 3:
                        resp.raise_for_status()
                    continue
                break
            except requests.exceptions.ReadTimeout:
                wait = BATCH_DELAY_SECS * (attempt + 2)
                print(f"  {label}batch {batch}: read timeout (attempt {attempt + 1}/4), waiting {wait}s...")
                time.sleep(wait)
                if attempt == 3:
                    raise
        # 404 means no more pages; anything else is a real error
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        results.extend(page)
        print(f"  {label}batch {batch}: {len(page)} records")
        if len(page) < 500:
            break
        batch += 1
        time.sleep(BATCH_DELAY_SECS)
    return results


def load_station_cache(cache_path):
    """Load local station info. Returns only non-None entries (real local stations)."""
    if cache_path.exists():
        raw = json.loads(cache_path.read_text())
        # Migration: strip legacy None entries (they move to ignore-stations.json)
        return {k: v for k, v in raw.items() if v is not None}
    return {}


def save_station_cache(cache_path, cache):
    cache_path.write_text(json.dumps(cache, indent=2))


def load_ignore_set(ignore_path):
    """Load the set of node_ids confirmed as outside our radius."""
    if ignore_path.exists():
        return set(json.loads(ignore_path.read_text()))
    return set()


def save_ignore_set(ignore_path, ignore_set):
    ignore_path.write_text(json.dumps(sorted(ignore_set), indent=2))


def fetch_local_prices(local_node_ids, token, since_date, label="prices "):
    """Page through the price feed and return records for local stations only.

    Stops early once every local station has reported a price in this batch,
    avoiding downloading the full national dataset unnecessarily.
    """
    results     = []
    remaining   = set(local_node_ids)   # IDs we still need prices for
    batch       = 1
    headers     = {"Authorization": f"Bearer {token}"}
    while remaining:
        params = {"batch-number": batch, "effective-start-timestamp": since_date}
        for attempt in range(4):
            try:
                resp = requests.get(
                    BASE_URL + PRICES_PATH,
                    headers=headers,
                    params=params,
                    timeout=(10, 90),
                )
                if resp.status_code == 504:
                    wait = BATCH_DELAY_SECS * (attempt + 2)
                    print(f"  {label}batch {batch}: 504 timeout (attempt {attempt + 1}/4), waiting {wait}s...")
                    time.sleep(wait)
                    if attempt == 3:
                        resp.raise_for_status()
                    continue
                break
            except requests.exceptions.ReadTimeout:
                wait = BATCH_DELAY_SECS * (attempt + 2)
                print(f"  {label}batch {batch}: read timeout (attempt {attempt + 1}/4), waiting {wait}s...")
                time.sleep(wait)
                if attempt == 3:
                    raise
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        found_this_page = 0
        for record in page:
            nid = record.get("node_id")
            if nid in remaining:
                results.append(record)
                remaining.discard(nid)
                found_this_page += 1
        print(f"  {label}batch {batch}: {len(page)} records, {found_this_page} local ({len(remaining)} still needed)")
        if len(page) < 500:
            break  # last page
        if not remaining:
            print(f"  All local stations covered — stopping early")
            break
        batch += 1
        time.sleep(BATCH_DELAY_SECS)
    return results


def station_from_pfs_record(record):
    loc  = record.get("location") or {}
    lat  = loc.get("latitude") or loc.get("lat")
    lon  = loc.get("longitude") or loc.get("lng") or loc.get("lon")
    if lat is None or lon is None:
        return None
    dist = haversine_miles(CHELTENHAM_LAT, CHELTENHAM_LON, float(lat), float(lon))
    if dist > RADIUS_MILES:
        return None
    loc_addr  = loc.get("address_line_1") or ""
    postcode  = loc.get("postcode") or ""
    # Avoid duplicating postcode if it's already at the end of address_line_1
    if postcode and loc_addr.upper().endswith(postcode.upper()):
        address = loc_addr
    elif postcode:
        address = f"{loc_addr}, {postcode}"
    else:
        address = loc_addr
    return {
        "trading_name":   record.get("trading_name") or "Unknown",
        "brand_name":     record.get("brand_name") or "",
        "distance_miles": round(dist, 2),
        "is_supermarket": bool(record.get("is_supermarket_service_station")),
        "address":        address,
    }


# -- Main ---------------------------------------------------------------------

if __name__ == "__main__":
    try:
        root         = pathlib.Path(__file__).parent.parent.resolve()
        cache_path   = root / "_data" / "fuel-stations.json"
        ignore_path  = root / "_data" / "ignore-stations.json"
        bootstrap    = "--bootstrap" in sys.argv

        FUEL_KEY   = os.getenv("FUEL_KEY") or ""
        FUEL_TOKEN = os.getenv("FUEL_TOKEN") or ""

        if not FUEL_KEY or not FUEL_TOKEN:
            print("Error: FUEL_KEY and FUEL_TOKEN environment variables are required")
            raise SystemExit(1)

        # 1. Authenticate
        print("Authenticating...")
        access_token = get_access_token(FUEL_KEY, FUEL_TOKEN)
        print("  OK")

        lookback_date  = (datetime.date.today() - datetime.timedelta(days=PRICE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

        # 2. Load station location cache and ignore list
        station_cache = load_station_cache(cache_path)
        ignore_set    = load_ignore_set(ignore_path)

        if bootstrap:
            # Bootstrap: clear ignore list so every station is re-evaluated against
            # the current radius, then do a full PFS fetch (no date filter).
            print(f"Bootstrap mode: clearing ignore list ({len(ignore_set)} entries) and fetching all PFS records...")
            ignore_set = set()
            save_ignore_set(ignore_path, ignore_set)
            pfs_added = 0
            pfs_ignored = 0
            try:
                all_pfs = fetch_all_pages(PFS_PATH, access_token, label="PFS ")
                for record in all_pfs:
                    nid   = record.get("node_id")
                    entry = station_from_pfs_record(record)
                    if entry:
                        # Preserve any stored prices when refreshing station metadata
                        existing = station_cache.get(nid) or {}
                        if existing.get("fuel_prices"):
                            entry["fuel_prices"]    = existing["fuel_prices"]
                            entry["prices_updated"] = existing["prices_updated"]
                        station_cache[nid] = entry
                        pfs_added += 1
                    else:
                        station_cache.pop(nid, None)  # remove if previously local
                        ignore_set.add(nid)
                        pfs_ignored += 1
                save_station_cache(cache_path, station_cache)
                save_ignore_set(ignore_path, ignore_set)
                print(f"  Bootstrap complete: {pfs_added} local stations, {pfs_ignored} ignored")
            except Exception as e:
                print(f"  Bootstrap PFS fetch stopped ({e}); partial progress saved")
                save_station_cache(cache_path, station_cache)
                save_ignore_set(ignore_path, ignore_set)

            # Seed price history using the same early-exit approach as the daily run
            print(f"Seeding price history (last {PRICE_LOOKBACK_DAYS} days)...")
            try:
                local_ids       = set(station_cache.keys())
                historic_prices = fetch_local_prices(local_ids, access_token, lookback_date, label="seed-prices ")
                seeded = 0
                for record in historic_prices:
                    nid    = record.get("node_id")
                    cached = station_cache.get(nid)
                    if cached:
                        new_prices = record.get("fuel_prices") or []
                        if new_prices:
                            existing_date = cached.get("prices_updated") or ""
                            record_date   = (record.get("effective_start_timestamp") or "")[:10]
                            if record_date >= existing_date:
                                cached["fuel_prices"]    = new_prices
                                cached["prices_updated"] = record_date or lookback_date
                                seeded += 1
                save_station_cache(cache_path, station_cache)
                print(f"  Price history seeded for {seeded} local stations")
            except Exception as e:
                print(f"  Price history seed stopped ({e}); partial progress saved")
                save_station_cache(cache_path, station_cache)
        else:
            print(f"Station cache: {len(station_cache)} local stations, {len(ignore_set)} ignored")

        # 3. Fetch prices for local stations — looks back PRICE_LOOKBACK_DAYS days,
        #    stops as soon as all local stations have reported in (early exit).
        local_ids  = set(station_cache.keys())
        print(f"Fetching prices for {len(local_ids)} local stations (lookback: {PRICE_LOOKBACK_DAYS} days, since {lookback_date})...")
        all_prices = fetch_local_prices(local_ids, access_token, lookback_date)
        print(f"  Price records returned for local stations: {len(all_prices)}")

        # 6. Merge fresh prices into the station cache so we accumulate latest
        #    known prices for every local station, not just today's reporters.
        today_str     = datetime.date.today().strftime("%Y-%m-%d")
        price_updates = 0
        for record in all_prices:
            nid    = record.get("node_id")
            cached = station_cache.get(nid)
            if cached:
                new_prices  = record.get("fuel_prices") or []
                record_date = (record.get("effective_start_timestamp") or "")[:10] or today_str
                if new_prices:
                    existing_date = cached.get("prices_updated") or ""
                    if record_date >= existing_date:
                        cached["fuel_prices"]    = new_prices
                        cached["prices_updated"] = record_date
                        price_updates += 1

        if price_updates:
            save_station_cache(cache_path, station_cache)
        print(f"Local stations with fresh prices: {price_updates}")

        # 7. Build price map from ALL cached local stations that have any prices
        price_map = {
            nid: station["fuel_prices"]
            for nid, station in station_cache.items()
            if station.get("fuel_prices")
        }
        print(f"Local stations with any known prices: {len(price_map)}")

        updated = datetime.datetime.now().strftime("%-d %B %Y at %H:%M")

        if not price_map:
            output = f"*No price data yet for stations near Cheltenham. Last checked: {updated}*"
            md = root / "_pages/fuel-prices.md"
            md_contents = md.open().read()
            md_contents = helper.replace_chunk(md_contents, "fuel_marker", output)
            md.open("w").write(md_contents)
            print("No price data available — page updated with status message.")
            raise SystemExit(0)

        # 8. Collect all distinct fuel-type codes across all priced stations
        all_fuel_types = set()
        for prices in price_map.values():
            for p in prices:
                ft = p.get("fuel_type") or ""
                if ft:
                    all_fuel_types.add(ft)
        fuel_type_cols = sorted(all_fuel_types)

        # 9. Find cheapest station per fuel type (from all stations with prices)
        cheapest = {}
        for nid, prices in price_map.items():
            station = station_cache[nid]
            name    = station["trading_name"]
            brand   = station["brand_name"]
            dist    = station["distance_miles"]
            addr    = station.get("address") or ""
            for p in prices:
                ft    = p.get("fuel_type") or ""
                pence = p.get("price")
                if ft and pence is not None:
                    pence = float(pence)
                    if ft not in cheapest or pence < cheapest[ft]["price"]:
                        cheapest[ft] = {"price": pence, "name": name, "brand": brand, "distance": dist, "address": addr}

        # 10. Render hero callout
        hero_lines = []
        for ft in fuel_type_cols:
            if ft not in cheapest:
                continue
            c         = cheapest[ft]
            brand_str = f" ({c['brand']})" if c["brand"] and c["brand"] != c["name"] else ""
            addr_str  = f", {c['address']}" if c.get("address") else ""
            hero_lines.append(f"### Cheapest {fuel_label(ft)}: {c['price']:.1f}p/L")
            hero_lines.append("")
            hero_lines.append(f"- {c['name']}{brand_str}{addr_str}")
            hero_lines.append("")

        # 11. Render price table — all local stations sorted by distance then name
        fuel_label_cols = [fuel_label(ft) for ft in fuel_type_cols]
        header_cols     = ["Station", "Address"] + fuel_label_cols + ["As of"]
        table_lines     = [
            "| " + " | ".join(header_cols) + " |",
            "| " + " | ".join(["---"] * len(header_cols)) + " |",
        ]

        priced_stations = sorted(
            price_map.keys(),
            key=lambda nid: (station_cache[nid]["distance_miles"], (station_cache[nid]["trading_name"] or "").lower())
        )

        for nid in priced_stations:
            station      = station_cache[nid]
            name         = station["trading_name"]
            label        = f"&#x2605; {name}" if station["is_supermarket"] else name
            addr_col     = station.get("address") or ""
            as_of        = station.get("prices_updated") or "?"
            price_lookup = {p["fuel_type"]: p for p in price_map[nid] if p.get("fuel_type")}

            row = [label, addr_col]
            for ft in fuel_type_cols:
                if ft in price_lookup:
                    pence = price_lookup[ft].get("price")
                    row.append(f"{float(pence):.1f}p" if pence is not None else "-")
                else:
                    row.append("-")
            row.append(as_of)
            table_lines.append("| " + " | ".join(row) + " |")

        # 12. Assemble and write output
        output  = "\n".join(hero_lines)
        output += "\n## Full Local Data\n\n"
        output += "\n".join(table_lines)
        output += f"\n\n*Last updated: {updated}*"

        md = root / "_pages/fuel-prices.md"
        md_contents = md.open().read()
        md_contents = helper.replace_chunk(md_contents, "fuel_marker", output)
        md.open("w").write(md_contents)
        print("Fuel prices page updated successfully.")

    except FileNotFoundError:
        print("File does not exist, unable to proceed")
