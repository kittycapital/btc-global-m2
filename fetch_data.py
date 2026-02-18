#!/usr/bin/env python3
"""
BTC vs Global M2 Data Fetcher v3
Collects M2 money supply data from 4 central banks and BTC price data.

Sources:
  - US M2: FRED API (M2SL)
  - Eurozone M2: ECB Data Portal API (BSI.M.U2.Y.V.M20.X.1.U2.2300.Z01.E)
  - Japan M2: BOJ Main Time-series Statistics HTML table scrape
  - Korea M2: ECOS API (한국은행) - auto-discovers correct stat/item codes
  - Exchange Rates: FRED API (EXJPUS, DEXUSEU, EXKOUS)
  - BTC Price: data/BTC_USD.csv
"""

import os
import sys
import json
import csv
import re
import urllib.request
import urllib.parse
from datetime import datetime
from collections import defaultdict
from html.parser import HTMLParser

# ============================================================
# CONFIG
# ============================================================
FRED_API_KEY = os.environ.get('FRED_API_KEY', '')
ECOS_API_KEY = os.environ.get('ECOS_API_KEY', '')

OUTPUT_FILE = 'data/m2_btc_data.json'
BTC_CSV = 'data/BTC_USD.csv'

FRED_SERIES = {
    'us_m2': 'M2SL',
    'fx_jpyusd': 'EXJPUS',
    'fx_usdeur': 'DEXUSEU',
    'fx_krwusd': 'EXKOUS',
}

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; HerdvibBot/1.0)'}


# ============================================================
# FRED API
# ============================================================
def fetch_fred(series_id, start_date='2004-01-01'):
    params = urllib.parse.urlencode({
        'series_id': series_id,
        'api_key': FRED_API_KEY,
        'file_type': 'json',
        'observation_start': start_date,
        'frequency': 'm',
        'aggregation_method': 'avg',
    })
    url = f'https://api.stlouisfed.org/fred/series/observations?{params}'
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        result = {}
        for obs in data.get('observations', []):
            date = obs['date'][:7]
            val = obs['value']
            if val != '.' and val != '':
                result[date] = float(val)
        print(f"  FRED {series_id}: {len(result)} observations")
        return result
    except Exception as e:
        print(f"  FRED {series_id} ERROR: {e}")
        return {}


# ============================================================
# ECB API (Eurozone M2)
# ============================================================
def fetch_ecb_m2():
    """BSI.M.U2.Y.V.M20.X.1.U2.2300.Z01.E (SA, Outstanding, Millions EUR)"""
    key = 'M.U2.Y.V.M20.X.1.U2.2300.Z01.E'
    url = f'https://data-api.ecb.europa.eu/service/data/BSI/{key}?startPeriod=2004-01&format=csvdata'
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
        result = {}
        reader = csv.DictReader(text.splitlines())
        for row in reader:
            period = row.get('TIME_PERIOD', '')
            value = row.get('OBS_VALUE', '')
            if period and value:
                result[period] = float(value)
        print(f"  ECB M2: {len(result)} observations")
        if result:
            dates = sorted(result.keys())
            print(f"  ECB range: {dates[0]} to {dates[-1]}")
        return result
    except Exception as e:
        print(f"  ECB M2 ERROR: {e}")
        # Fallback: non-seasonally adjusted
        try:
            key2 = 'M.U2.N.V.M20.X.1.U2.2300.Z01.E'
            url2 = f'https://data-api.ecb.europa.eu/service/data/BSI/{key2}?startPeriod=2004-01&format=csvdata'
            req2 = urllib.request.Request(url2, headers=HEADERS)
            with urllib.request.urlopen(req2, timeout=60) as resp2:
                text2 = resp2.read().decode()
            result2 = {}
            for row in csv.DictReader(text2.splitlines()):
                p = row.get('TIME_PERIOD', '')
                v = row.get('OBS_VALUE', '')
                if p and v:
                    result2[p] = float(v)
            print(f"  ECB M2 (NSA fallback): {len(result2)} observations")
            return result2
        except Exception as e2:
            print(f"  ECB M2 fallback ERROR: {e2}")
            return {}


# ============================================================
# BOJ (Japan M2) - HTML Table Scrape
# ============================================================
class BOJTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_row = []
        self.rows = []
        self.cell_text = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.in_table = True
        elif tag == 'tr' and self.in_table:
            self.in_row = True
            self.current_row = []
        elif tag in ('td', 'th') and self.in_row:
            self.in_cell = True
            self.cell_text = ''

    def handle_endtag(self, tag):
        if tag == 'table':
            self.in_table = False
        elif tag == 'tr' and self.in_row:
            self.in_row = False
            if self.current_row:
                self.rows.append(self.current_row)
        elif tag in ('td', 'th') and self.in_cell:
            self.in_cell = False
            self.current_row.append(self.cell_text.strip())

    def handle_data(self, data):
        if self.in_cell:
            self.cell_text += data


def fetch_boj_m2():
    """Scrape BOJ md02_m_1_en.html, column 9 = M2 Average Outstanding (億円)"""
    url = 'https://www.stat-search.boj.or.jp/ssi/mtshtml/md02_m_1_en.html'
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode('utf-8', errors='replace')
        parser = BOJTableParser()
        parser.feed(text)
        result = {}
        m2_col = 9
        for row in parser.rows:
            if len(row) <= m2_col:
                continue
            match = re.match(r'^(\d{4})/(\d{2})$', row[0].strip())
            if not match:
                continue
            y, m = match.groups()
            val = row[m2_col].strip().replace(',', '')
            if val and val != 'ND':
                try:
                    result[f"{y}-{m}"] = float(val)
                except ValueError:
                    pass
        print(f"  BOJ M2: {len(result)} observations")
        if result:
            dates = sorted(result.keys())
            print(f"  BOJ range: {dates[0]} to {dates[-1]}")
        return result
    except Exception as e:
        print(f"  BOJ M2 ERROR: {e}")
        return {}


# ============================================================
# ECOS API (Korea M2) - Auto-discover correct item codes
# ============================================================
def ecos_api_call(path):
    """Make ECOS API call and return parsed JSON."""
    url = f'https://ecos.bok.or.kr/api/{path}'
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def ecos_discover_m2_items(stat_code):
    """Use StatisticItemList to find available item codes for a stat table."""
    path = f'StatisticItemList/{ECOS_API_KEY}/json/kr/1/500/{stat_code}'
    try:
        data = ecos_api_call(path)
        if 'StatisticItemList' not in data:
            return []
        items = data['StatisticItemList'].get('row', [])
        return items
    except Exception as e:
        print(f"    ItemList error for {stat_code}: {e}")
        return []


def ecos_search(stat_code, item_code, cycle='M', start='200401', end='202612'):
    """Fetch data from ECOS StatisticSearch."""
    path = (
        f'StatisticSearch/{ECOS_API_KEY}/json/kr/1/1000/'
        f'{stat_code}/{cycle}/{start}/{end}/{item_code}'
    )
    data = ecos_api_call(path)
    if 'StatisticSearch' not in data:
        return {}
    result = {}
    for row in data['StatisticSearch'].get('row', []):
        time = row.get('TIME', '')
        val = row.get('DATA_VALUE', '').replace(',', '')
        if time and val:
            try:
                result[f"{time[:4]}-{time[4:6]}"] = float(val)
            except ValueError:
                pass
    return result


def fetch_ecos_m2():
    """Auto-discover and fetch Korea M2 data.

    Strategy:
    1. Query StatisticItemList for stat tables likely to contain M2
    2. Find items containing 'M2' or '광의통화' in name, with '평잔' (average)
    3. Query data and pick the one with the most observations
    """
    if not ECOS_API_KEY:
        print("  ECOS: No API key set, skipping")
        return {}

    # Stat codes that may contain M2 data
    stat_codes = ['101Y002', '101Y003', '101Y017']

    best_result = {}
    best_info = ''

    for stat_code in stat_codes:
        print(f"  ECOS: Discovering items for {stat_code}...")
        items = ecos_discover_m2_items(stat_code)

        if not items:
            print(f"    No items found for {stat_code}")
            continue

        # Filter for M2-related items
        m2_items = []
        for item in items:
            name = item.get('ITEM_NAME', '')
            code = item.get('ITEM_CODE', '')
            cycle = item.get('CYCLE', '')

            # Look for M2 평잔 (average outstanding) in monthly data
            is_m2 = ('M2' in name or '광의통화' in name)
            is_avg = ('평잔' in name or 'Average' in name or 'average' in name)
            # Also accept items that just say M2 without 평잔
            if is_m2:
                m2_items.append({
                    'code': code,
                    'name': name,
                    'cycle': cycle,
                    'is_avg': is_avg
                })

        if not m2_items:
            # If no M2 items found, print all items for debugging
            print(f"    No M2 items found. Available items:")
            for item in items[:20]:
                print(f"      {item.get('ITEM_CODE', '?')}: {item.get('ITEM_NAME', '?')}")
            continue

        # Sort: prefer 평잔 items
        m2_items.sort(key=lambda x: (not x['is_avg'], x['code']))

        print(f"    Found {len(m2_items)} M2-related items:")
        for item in m2_items[:5]:
            print(f"      {item['code']}: {item['name']}")

        # Try each M2 item and pick the one with most data
        for item in m2_items:
            try:
                result = ecos_search(stat_code, item['code'])
                count = len(result)
                print(f"    {stat_code}/{item['code']}: {count} observations")

                if count > len(best_result):
                    best_result = result
                    best_info = f"{stat_code}/{item['code']} ({item['name']})"

                if count > 200:
                    break  # Good enough
            except Exception as e:
                print(f"    {stat_code}/{item['code']}: ERROR {e}")

        if len(best_result) > 200:
            break

    if best_result:
        dates = sorted(best_result.keys())
        print(f"  ECOS M2: {len(best_result)} observations via {best_info}")
        print(f"  ECOS range: {dates[0]} to {dates[-1]}")
        print(f"  ECOS latest: {best_result[dates[-1]]:,.0f} (억원)")
    else:
        print("  ECOS M2: No data found after all attempts")
        # Last resort: try direct known codes
        for sc, ic in [('101Y002', 'BBHS01'), ('101Y003', 'BBGA00'),
                        ('101Y002', 'BBGA00A'), ('101Y002', 'BBIA00')]:
            try:
                result = ecos_search(sc, ic)
                if len(result) > len(best_result):
                    best_result = result
                    print(f"    Last resort {sc}/{ic}: {len(result)} observations")
            except:
                pass

    return best_result


# ============================================================
# BTC PRICE (from CSV)
# ============================================================
def load_btc_csv():
    if not os.path.exists(BTC_CSV):
        print(f"  BTC CSV not found: {BTC_CSV}")
        return {}
    monthly = defaultdict(list)
    with open(BTC_CSV, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = row.get('Date', '')
            close = row.get('Close', '')
            if date and close:
                try:
                    monthly[date[:7]].append(float(close))
                except ValueError:
                    pass
    result = {}
    for ym, prices in sorted(monthly.items()):
        result[ym] = round(sum(prices) / len(prices), 2)
    print(f"  BTC CSV: {len(result)} months")
    return result


# ============================================================
# DATA ASSEMBLY
# ============================================================
def convert_to_usd_trillions(m2_local, fx_rates, country):
    result = {}
    for date, local_val in m2_local.items():
        if country == 'eurozone':
            usdeur = fx_rates.get('fx_usdeur', {}).get(date)
            if usdeur:
                result[date] = round((local_val * 1e6 * usdeur) / 1e12, 2)
        elif country == 'japan':
            jpyusd = fx_rates.get('fx_jpyusd', {}).get(date)
            if jpyusd and jpyusd > 0:
                result[date] = round((local_val * 1e8 / jpyusd) / 1e12, 2)
        elif country == 'korea':
            krwusd = fx_rates.get('fx_krwusd', {}).get(date)
            if krwusd and krwusd > 0:
                result[date] = round((local_val * 1e8 / krwusd) / 1e12, 2)
    return result


def assemble_data():
    print("=" * 60)
    print(f"BTC vs Global M2 Data Fetch v3 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    print("\n[1/6] Fetching US M2 from FRED...")
    us_m2_raw = fetch_fred(FRED_SERIES['us_m2'])

    print("\n[2/6] Fetching exchange rates from FRED...")
    fx_rates = {}
    for key, series in FRED_SERIES.items():
        if key.startswith('fx_'):
            fx_rates[key] = fetch_fred(series)

    print("\n[3/6] Fetching Eurozone M2 from ECB...")
    eu_m2_raw = fetch_ecb_m2()

    print("\n[4/6] Fetching Japan M2 from BOJ...")
    jp_m2_raw = fetch_boj_m2()

    print("\n[5/6] Fetching Korea M2 from ECOS...")
    kr_m2_raw = fetch_ecos_m2()

    print("\n[6/6] Loading BTC price data...")
    btc_monthly = load_btc_csv()

    # Convert
    print("\n[Converting currencies...]")
    us_m2 = {d: round(v / 1000, 2) for d, v in us_m2_raw.items()}
    print(f"  US M2: {len(us_m2)} months in USD trillions")

    eu_m2 = convert_to_usd_trillions(eu_m2_raw, fx_rates, 'eurozone')
    print(f"  EU M2: {len(eu_m2)} months in USD trillions")

    jp_m2 = convert_to_usd_trillions(jp_m2_raw, fx_rates, 'japan')
    print(f"  JP M2: {len(jp_m2)} months in USD trillions")

    kr_m2 = convert_to_usd_trillions(kr_m2_raw, fx_rates, 'korea')
    print(f"  KR M2: {len(kr_m2)} months in USD trillions")

    # Global M2
    print("\n[Computing Global M2...]")
    all_dates = sorted(
        set(us_m2.keys()) | set(eu_m2.keys()) |
        set(jp_m2.keys()) | set(kr_m2.keys())
    )

    global_m2 = {}
    m2_components = {'us': {}, 'eurozone': {}, 'japan': {}, 'korea': {}}

    for date in all_dates:
        us = us_m2.get(date)
        eu = eu_m2.get(date)
        jp = jp_m2.get(date)
        kr = kr_m2.get(date)
        available = [v for v in [us, eu, jp, kr] if v is not None]
        if us is not None and len(available) >= 2:
            global_m2[date] = round(sum(available), 2)
            if us: m2_components['us'][date] = us
            if eu: m2_components['eurozone'][date] = eu
            if jp: m2_components['japan'][date] = jp
            if kr: m2_components['korea'][date] = kr

    print(f"  Global M2: {len(global_m2)} months")
    if global_m2:
        ds = sorted(global_m2.keys())
        print(f"  Range: {ds[0]} to {ds[-1]}")
        print(f"  Latest: ${global_m2[ds[-1]]}T")

    # Output
    output = {
        'last_updated': datetime.now().strftime('%Y-%m-%d'),
        'btc_monthly_avg': btc_monthly,
        'global_m2': global_m2,
        'm2_components': m2_components,
        'metadata': {
            'us_m2_count': len(us_m2),
            'eu_m2_count': len(eu_m2),
            'jp_m2_count': len(jp_m2),
            'kr_m2_count': len(kr_m2),
            'btc_count': len(btc_monthly),
            'global_m2_count': len(global_m2),
        }
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*60}")
    print(f"✅ Saved to {OUTPUT_FILE}")
    print(f"   File size: {os.path.getsize(OUTPUT_FILE):,} bytes")
    print(f"\n📊 Data Summary:")
    for name, count in [
        ('US M2', len(us_m2)), ('EU M2', len(eu_m2)),
        ('JP M2', len(jp_m2)), ('KR M2', len(kr_m2)),
        ('BTC', len(btc_monthly)), ('Global', len(global_m2))
    ]:
        icon = '✅' if count > 100 else '⚠️ '
        print(f"   {name:10s} {icon}  {count} months")


if __name__ == '__main__':
    if not FRED_API_KEY:
        print("⚠️  FRED_API_KEY not set.")
        sys.exit(1)
    assemble_data()
