import helper
import json
import os
import pathlib
import math
import datetime
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
RADIUS_MILES    = 15
EARTH_RADIUS_MI = 3958.8

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


def fetch_all_pages(path, token, extra_params=None):
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
                    print(f"  batch {batch}: 504 gateway timeout (attempt {attempt + 1}/4), retrying...")
                    if attempt == 3:
                        resp.raise_for_status()
                    continue
                break
            except requests.exceptions.ReadTimeout:
                print(f"  batch {batch}: read timeout (attempt {attempt + 1}/4), retrying...")
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
        print(f"  batch {batch}: {len(page)} records")
        if len(page) < 500:
            break
        batch += 1
    return results


def load_station_cache(cache_path):
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {}


def save_station_cache(cache_path, cache):
    cache_path.write_text(json.dumps(cache, indent=2))


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
        root        = pathlib.Path(__file__).parent.parent.resolve()
        cache_path  = root / "_data" / "fuel-stations.json"

        FUEL_KEY   = os.getenv("FUEL_KEY") or ""
        FUEL_TOKEN = os.getenv("FUEL_TOKEN") or ""

        if not FUEL_KEY or not FUEL_TOKEN:
            print("Error: FUEL_KEY and FUEL_TOKEN environment variables are required")
            raise SystemExit(1)

        # 1. Authenticate
        print("Authenticating...")
        access_token = get_access_token(FUEL_KEY, FUEL_TOKEN)
        print("  OK")

        yesterday     = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        seven_days    = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")

        # 2. Load station location cache (builds up over time)
        station_cache = load_station_cache(cache_path)
        print(f"Station cache: {len(station_cache)} known local stations")

        # 3. Fetch incremental fuel prices — this is the primary dataset
        print(f"Fetching fuel prices updated since {yesterday}...")
        all_prices = fetch_all_pages(PRICES_PATH, access_token, {"effective-start-timestamp": yesterday})
        print(f"  Price records returned: {len(all_prices)}")

        # 4. Find node_ids in the price batch not yet in our local cache
        price_node_ids = {r["node_id"] for r in all_prices}
        unknown_ids    = price_node_ids - set(station_cache.keys())
        print(f"  Unknown stations to look up: {len(unknown_ids)}")

        # 5. Fetch incremental PFS info to resolve unknown stations
        # Save cache after each batch so partial runs accumulate usefully
        if unknown_ids:
            print(f"Fetching PFS station info updated since {seven_days} (7-day window)...")
            try:
                batch_num = 1
                pfs_headers = {"Authorization": f"Bearer {access_token}"}
                cache_updated = 0
                while True:
                    params = {"batch-number": batch_num, "effective-start-timestamp": seven_days}
                    for attempt in range(4):
                        try:
                            r = requests.get(
                                BASE_URL + PFS_PATH,
                                headers=pfs_headers,
                                params=params,
                                timeout=(10, 90),
                            )
                            if r.status_code == 504:
                                print(f"  batch {batch_num}: 504 timeout (attempt {attempt + 1}/4), retrying...")
                                if attempt == 3:
                                    r.raise_for_status()
                                continue
                            break
                        except requests.exceptions.ReadTimeout:
                            print(f"  batch {batch_num}: read timeout (attempt {attempt + 1}/4), retrying...")
                            if attempt == 3:
                                raise
                    if r.status_code == 404:
                        break
                    r.raise_for_status()
                    page = r.json()
                    if not page:
                        break
                    for record in page:
                        nid = record.get("node_id")
                        if nid not in unknown_ids:
                            continue
                        entry = station_from_pfs_record(record)
                        if entry:
                            station_cache[nid] = entry
                            cache_updated += 1
                        else:
                            station_cache[nid] = None
                    # Save after every batch so partial runs accumulate
                    save_station_cache(cache_path, station_cache)
                    print(f"  batch {batch_num}: {len(page)} records ({cache_updated} local found so far)")
                    if len(page) < 500:
                        break
                    batch_num += 1
                print(f"  {cache_updated} new local stations added to cache")
            except Exception as e:
                print(f"  PFS info lookup stopped ({e}); progress saved, will continue next run")

        # 6. Build price map for local stations only
        price_map = {}
        for record in all_prices:
            nid    = record.get("node_id")
            cached = station_cache.get(nid)
            if cached:  # None means known non-local; missing means unseen
                price_map[nid] = record.get("fuel_prices") or []

        print(f"Local stations with updated prices: {len(price_map)}")

        updated = datetime.datetime.now().strftime("%-d %B %Y at %H:%M")

        if not price_map:
            output = f"*No price changes reported near Cheltenham since {yesterday}. Last checked: {updated}*"
            md = root / "_pages/fuel-prices.md"
            md_contents = md.open().read()
            md_contents = helper.replace_chunk(md_contents, "fuel_marker", output)
            md.open("w").write(md_contents)
            print("No local price updates — page updated with status message.")
            raise SystemExit(0)

        # 7. Collect all distinct fuel-type codes in this batch
        all_fuel_types = set()
        for prices in price_map.values():
            for p in prices:
                ft = p.get("fuel_type") or ""
                if ft:
                    all_fuel_types.add(ft)
        fuel_type_cols = sorted(all_fuel_types)

        # 8. Find cheapest station per fuel type
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

        # 9. Render hero callout
        hero_lines = []
        for ft in fuel_type_cols:
            if ft not in cheapest:
                continue
            c         = cheapest[ft]
            brand_str = f" ({c['brand']})" if c["brand"] and c["brand"] != c["name"] else ""
            addr_str  = f", {c['address']}" if c.get("address") else ""
            hero_lines.append(f"### Cheapest {fuel_label(ft)}: {c['price']:.1f}p/L")
            hero_lines.append("")
            hero_lines.append(f"- {c['name']}{brand_str}, {c['distance']:.1f} miles away{addr_str}")
            hero_lines.append("")

        # 10. Render price table
        fuel_label_cols = [fuel_label(ft) for ft in fuel_type_cols]
        header_cols     = ["Station", "Address", "Distance"] + fuel_label_cols
        table_lines     = [
            "| " + " | ".join(header_cols) + " |",
            "| " + " | ".join(["---"] * len(header_cols)) + " |",
        ]

        priced_stations = sorted(
            price_map.keys(),
            key=lambda nid: (station_cache[nid]["trading_name"] or "").lower()
        )

        for nid in priced_stations:
            station      = station_cache[nid]
            name         = station["trading_name"]
            label        = f"&#x2605; {name}" if station["is_supermarket"] else name
            addr_col     = station.get("address") or ""
            dist_str     = f"{station['distance_miles']:.1f} mi"
            price_lookup = {p["fuel_type"]: p for p in price_map[nid] if p.get("fuel_type")}

            row = [label, addr_col, dist_str]
            for ft in fuel_type_cols:
                if ft in price_lookup:
                    pence = price_lookup[ft].get("price")
                    row.append(f"{float(pence):.1f}p" if pence is not None else "-")
                else:
                    row.append("-")
            table_lines.append("| " + " | ".join(row) + " |")

        # 11. Assemble and write output
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
