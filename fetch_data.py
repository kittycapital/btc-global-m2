#!/usr/bin/env python3
"""
BTC vs Global M2 Data Fetcher
Collects M2 money supply data from 4 central banks and BTC price data.

Sources:
  - US M2: FRED API (M2SL)
  - Eurozone M2: ECB Data Portal API (BSI) - M.U2.Y.V.M20.X.1.U2.2300.Z01.E
  - Japan M2: BOJ Main Time-series Statistics HTML table scrape
  - Korea M2: ECOS API (한국은행) - 101Y002 / BBGA00
  - Exchange Rates: FRED API (EXJPUS, DEXUSEU, EXKOUS)
  - BTC Price: data/BTC_USD.csv (provided externally)
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
    'us_m2': 'M2SL',           # US M2 (Billions USD, Monthly SA)
    'fx_jpyusd': 'EXJPUS',     # JPY per USD (Monthly)
    'fx_usdeur': 'DEXUSEU',    # USD per EUR (Daily -> monthly avg)
    'fx_krwusd': 'EXKOUS',     # KRW per USD (Monthly)
}

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; HerdvibBot/1.0)'}


# ============================================================
# FRED API
# ============================================================
def fetch_fred(series_id, start_date='2004-01-01'):
    """Fetch data from FRED API."""
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
    """Fetch Eurozone M2 from ECB Data Portal API.

    Correct series key: BSI.M.U2.Y.V.M20.X.1.U2.2300.Z01.E
      - M = Monthly
      - U2 = Euro area
      - Y = Working day and seasonally adjusted
      - V = MFIs, central government and post office
      - M20 = Monetary aggregate M2
      - X = All currencies combined
      - 1 = Outstanding amounts at the end of the period
      - U2 = Euro area (changing composition)
      - 2300 = Non-MFIs excluding central government
      - Z01 = All currencies combined
      - E = Euro (unit)
    """
    key = 'M.U2.Y.V.M20.X.1.U2.2300.Z01.E'
    url = f'https://data-api.ecb.europa.eu/service/data/BSI/{key}?startPeriod=2004-01&format=csvdata'

    print(f"  ECB URL: {url}")

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
                result[period] = float(value)  # Millions EUR

        print(f"  ECB M2: {len(result)} observations")
        if result:
            dates = sorted(result.keys())
            print(f"  ECB range: {dates[0]} to {dates[-1]}")
            print(f"  ECB latest: {result[dates[-1]]:,.0f} millions EUR")
        return result
    except Exception as e:
        print(f"  ECB M2 ERROR: {e}")
        return fetch_ecb_m2_fallback()


def fetch_ecb_m2_fallback():
    """Fallback: try non-seasonally adjusted M2."""
    key = 'M.U2.N.V.M20.X.1.U2.2300.Z01.E'
    url = f'https://data-api.ecb.europa.eu/service/data/BSI/{key}?startPeriod=2004-01&format=csvdata'
    print(f"  ECB fallback URL: {url}")

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

        print(f"  ECB M2 (fallback NSA): {len(result)} observations")
        return result
    except Exception as e:
        print(f"  ECB M2 fallback ERROR: {e}")
        return {}


# ============================================================
# BOJ (Japan M2) - HTML Table Scrape
# ============================================================
class BOJTableParser(HTMLParser):
    """Parse the BOJ main time-series statistics HTML table."""

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
    """Fetch Japan M2 from BOJ Main Time-series Statistics HTML table.

    Page: https://www.stat-search.boj.or.jp/ssi/mtshtml/md02_m_1_en.html
    Table columns (0-indexed):
      0: Date (YYYY/MM)
      1-8: Percent changes (M2, M3, M1, L, CC, DM, QM, CD)
      9-16: Average Amounts Outstanding (M2, M3, M1, L, CC, DM, QM, CD)

    We want column 9: M2 Average Amounts Outstanding (100 million yen)
    Data starts from 2003/04.
    """
    url = 'https://www.stat-search.boj.or.jp/ssi/mtshtml/md02_m_1_en.html'
    print(f"  BOJ URL: {url}")

    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode('utf-8', errors='replace')

        parser = BOJTableParser()
        parser.feed(text)

        result = {}
        m2_col_idx = 9  # M2 Average Amounts Outstanding

        for row in parser.rows:
            if len(row) <= m2_col_idx:
                continue

            date_str = row[0].strip()
            match = re.match(r'^(\d{4})/(\d{2})$', date_str)
            if not match:
                continue

            year, month = match.groups()
            value_str = row[m2_col_idx].strip().replace(',', '')

            if value_str and value_str != 'ND' and value_str != '':
                try:
                    value = float(value_str)
                    date_key = f"{year}-{month}"
                    result[date_key] = value  # 100 million yen (億円)
                except ValueError:
                    continue

        print(f"  BOJ M2: {len(result)} observations")
        if result:
            dates = sorted(result.keys())
            print(f"  BOJ range: {dates[0]} to {dates[-1]}")
            print(f"  BOJ latest: {result[dates[-1]]:,.0f} (100M JPY)")
        return result
    except Exception as e:
        print(f"  BOJ M2 ERROR: {e}")
        return {}


# ============================================================
# ECOS API (Korea M2)
# ============================================================
def fetch_ecos_m2():
    """Fetch Korea M2 from Bank of Korea ECOS API.

    After 2022-05 API restructuring:
      통계표코드: 101Y002 (통화 및 유동성)
      항목코드: BBGA00 (M2 평잔)
      주기: M (월간)
    """
    if not ECOS_API_KEY:
        print("  ECOS: No API key set, skipping")
        return {}

    stat_code = '101Y002'
    item_code = 'BBGA00'
    start = '200401'
    end = '202612'

    url = (
        f'https://ecos.bok.or.kr/api/StatisticSearch/'
        f'{ECOS_API_KEY}/json/kr/1/1000/'
        f'{stat_code}/M/{start}/{end}/{item_code}'
    )

    print(f"  ECOS URL: .../{stat_code}/M/{start}/{end}/{item_code}")

    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            data = json.loads(raw)

        if 'StatisticSearch' not in data:
            print(f"  ECOS response keys: {list(data.keys())}")
            if 'RESULT' in data:
                print(f"  ECOS error: {data['RESULT']}")
            return fetch_ecos_m2_fallback()

        result = {}
        rows = data.get('StatisticSearch', {}).get('row', [])
        for row in rows:
            time = row.get('TIME', '')
            val = row.get('DATA_VALUE', '')
            if time and val:
                date_key = f"{time[:4]}-{time[4:6]}"
                val_clean = val.replace(',', '')
                try:
                    result[date_key] = float(val_clean)  # 억원
                except ValueError:
                    continue

        print(f"  ECOS M2: {len(result)} observations")
        if result:
            dates = sorted(result.keys())
            print(f"  ECOS range: {dates[0]} to {dates[-1]}")
            print(f"  ECOS latest: {result[dates[-1]]:,.0f} (억원)")
        else:
            # 0 results, try fallback
            print("  ECOS: 0 results, trying fallback codes...")
            return fetch_ecos_m2_fallback()

        return result
    except Exception as e:
        print(f"  ECOS M2 ERROR: {e}")
        return fetch_ecos_m2_fallback()


def fetch_ecos_m2_fallback():
    """Try alternative ECOS stat/item codes for M2."""
    alt_codes = [
        ('101Y003', 'BBGA00'),   # M2 구성내역
        ('101Y002', 'BBHS01'),   # Alternative item code
        ('101Y002', 'BBGA00A'),  # Another variant
    ]

    for stat_code, item_code in alt_codes:
        url = (
            f'https://ecos.bok.or.kr/api/StatisticSearch/'
            f'{ECOS_API_KEY}/json/kr/1/1000/'
            f'{stat_code}/M/200401/202612/{item_code}'
        )
        print(f"  ECOS trying: {stat_code}/{item_code}")

        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())

            if 'StatisticSearch' in data:
                rows = data['StatisticSearch'].get('row', [])
                if rows:
                    result = {}
                    for row in rows:
                        time = row.get('TIME', '')
                        val = row.get('DATA_VALUE', '').replace(',', '')
                        if time and val:
                            try:
                                date_key = f"{time[:4]}-{time[4:6]}"
                                result[date_key] = float(val)
                            except ValueError:
                                continue
                    if result:
                        print(f"  ECOS fallback OK ({stat_code}/{item_code}): {len(result)} obs")
                        return result
            else:
                err = data.get('RESULT', {})
                print(f"  ECOS {stat_code}/{item_code}: {err.get('MESSAGE', 'error')}")
        except Exception as e:
            print(f"  ECOS {stat_code}/{item_code}: {e}")

    print("  ECOS: All attempts failed")
    return {}


# ============================================================
# BTC PRICE (from CSV)
# ============================================================
def load_btc_csv():
    """Load BTC price from CSV and compute monthly averages."""
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
                    ym = date[:7]
                    monthly[ym].append(float(close))
                except ValueError:
                    continue

    result = {}
    for ym, prices in sorted(monthly.items()):
        result[ym] = round(sum(prices) / len(prices), 2)

    print(f"  BTC CSV: {len(result)} months")
    return result


# ============================================================
# DATA ASSEMBLY
# ============================================================
def convert_to_usd_trillions(m2_local, fx_rates, country):
    """Convert local currency M2 to USD trillions."""
    result = {}

    for date, local_val in m2_local.items():
        if country == 'eurozone':
            # ECB: Millions EUR → Trillions USD
            usdeur = fx_rates.get('fx_usdeur', {}).get(date)
            if usdeur:
                usd_val = (local_val * 1e6 * usdeur) / 1e12
                result[date] = round(usd_val, 2)

        elif country == 'japan':
            # BOJ: 億円 (100 million JPY) → Trillions USD
            jpyusd = fx_rates.get('fx_jpyusd', {}).get(date)
            if jpyusd and jpyusd > 0:
                usd_val = (local_val * 1e8 / jpyusd) / 1e12
                result[date] = round(usd_val, 2)

        elif country == 'korea':
            # ECOS: 억원 (100 million KRW) → Trillions USD
            krwusd = fx_rates.get('fx_krwusd', {}).get(date)
            if krwusd and krwusd > 0:
                usd_val = (local_val * 1e8 / krwusd) / 1e12
                result[date] = round(usd_val, 2)

    return result


def assemble_data():
    """Fetch all data and assemble into final JSON."""
    print("=" * 60)
    print(f"BTC vs Global M2 Data Fetch - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
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

    # ---- Convert ----
    print("\n[Converting currencies...]")

    us_m2 = {}
    for date, val in us_m2_raw.items():
        us_m2[date] = round(val / 1000, 2)  # Billions → Trillions
    print(f"  US M2: {len(us_m2)} months in USD trillions")

    eu_m2 = convert_to_usd_trillions(eu_m2_raw, fx_rates, 'eurozone')
    print(f"  EU M2: {len(eu_m2)} months in USD trillions")

    jp_m2 = convert_to_usd_trillions(jp_m2_raw, fx_rates, 'japan')
    print(f"  JP M2: {len(jp_m2)} months in USD trillions")

    kr_m2 = convert_to_usd_trillions(kr_m2_raw, fx_rates, 'korea')
    print(f"  KR M2: {len(kr_m2)} months in USD trillions")

    # ---- Global M2 ----
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
            total = sum(available)
            global_m2[date] = round(total, 2)
            if us: m2_components['us'][date] = us
            if eu: m2_components['eurozone'][date] = eu
            if jp: m2_components['japan'][date] = jp
            if kr: m2_components['korea'][date] = kr

    print(f"  Global M2: {len(global_m2)} months")
    if global_m2:
        dates_sorted = sorted(global_m2.keys())
        print(f"  Range: {dates_sorted[0]} to {dates_sorted[-1]}")
        print(f"  Latest: ${global_m2[dates_sorted[-1]]}T")

    # ---- Output ----
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
    print(f"   US M2:    {'✅' if len(us_m2) > 100 else '⚠️ '}  {len(us_m2)} months")
    print(f"   EU M2:    {'✅' if len(eu_m2) > 100 else '⚠️ '}  {len(eu_m2)} months")
    print(f"   JP M2:    {'✅' if len(jp_m2) > 100 else '⚠️ '}  {len(jp_m2)} months")
    print(f"   KR M2:    {'✅' if len(kr_m2) > 100 else '⚠️ '}  {len(kr_m2)} months")
    print(f"   BTC:      {'✅' if len(btc_monthly) > 100 else '⚠️ '}  {len(btc_monthly)} months")
    print(f"   Global:   {'✅' if len(global_m2) > 100 else '⚠️ '}  {len(global_m2)} months")


if __name__ == '__main__':
    if not FRED_API_KEY:
        print("⚠️  FRED_API_KEY not set.")
        print("   export FRED_API_KEY=your_key_here")
        sys.exit(1)

    assemble_data()
