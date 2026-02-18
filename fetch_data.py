#!/usr/bin/env python3
"""
BTC vs Global M2 Data Fetcher
Collects M2 money supply data from 4 central banks and BTC price data.

Sources:
  - US M2: FRED API (M2SL)
  - Eurozone M2: ECB Data Portal API (BSI)
  - Japan M2: BOJ flat file download
  - Korea M2: ECOS API (한국은행)
  - Exchange Rates: FRED API (EXJPUS, DEXUSEU, EXKOUS)
  - BTC Price: data/BTC_USD.csv (provided externally)
"""

import os
import sys
import json
import csv
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from collections import defaultdict
import xml.etree.ElementTree as ET

# ============================================================
# CONFIG
# ============================================================
FRED_API_KEY = os.environ.get('FRED_API_KEY', '')
ECOS_API_KEY = os.environ.get('ECOS_API_KEY', '')

OUTPUT_FILE = 'data/m2_btc_data.json'
BTC_CSV = 'data/BTC_USD.csv'

# FRED series IDs
FRED_SERIES = {
    'us_m2': 'M2SL',           # US M2 (Billions USD, Monthly SA)
    'fx_jpyusd': 'EXJPUS',     # JPY per USD (Monthly)
    'fx_usdeur': 'DEXUSEU',    # USD per EUR (Daily -> monthly avg)
    'fx_krwusd': 'EXKOUS',     # KRW per USD (Monthly)
}

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
        'frequency': 'm',  # monthly
        'aggregation_method': 'avg',
    })
    url = f'https://api.stlouisfed.org/fred/series/observations?{params}'
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        
        result = {}
        for obs in data.get('observations', []):
            date = obs['date'][:7]  # YYYY-MM
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
    
    Series: BSI.M.U2.Y.V.M30.A.1.U2.2300.Z01.E
    = Monthly, Euro Area, M2, outstanding amounts, EUR
    """
    key = 'M.U2.Y.V.M30.A.1.U2.2300.Z01.E'
    url = f'https://data-api.ecb.europa.eu/service/data/BSI/{key}?startPeriod=2004-01&format=csvdata'
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
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
        return result
    except Exception as e:
        print(f"  ECB M2 ERROR: {e}")
        return {}


# ============================================================
# BOJ (Japan M2) - Flat file
# ============================================================
def fetch_boj_m2():
    """Fetch Japan M2 from BOJ Time-Series Data.
    
    Downloads the Money Stock flat file and extracts M2 data.
    Series code: MA'MAM1NAM2M2MO (M2, Average Outstanding, Monthly)
    """
    # BOJ provides flat files in CSV format
    url = 'https://www.stat-search.boj.or.jp/ssi/mtshtml/m.csv'
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode('shift_jis', errors='replace')
        
        result = {}
        lines = text.strip().split('\n')
        
        # Find M2 row - look for the M2 indicator
        m2_row_idx = None
        for i, line in enumerate(lines):
            if 'M2' in line and ('平残' in line or 'Average' in line or 'MAM1NAM2' in line):
                m2_row_idx = i
                break
        
        if m2_row_idx is not None:
            # Parse dates from header and values from M2 row
            # BOJ flat files have specific format
            pass
        
        # Alternative: try the JSON endpoint
        if not result:
            result = fetch_boj_m2_json()
        
        print(f"  BOJ M2: {len(result)} observations")
        return result
    except Exception as e:
        print(f"  BOJ M2 ERROR: {e}")
        return fetch_boj_m2_json()


def fetch_boj_m2_json():
    """Fallback: Fetch BOJ M2 via search API."""
    # Use the BOJ time series search with specific code
    code = "MA'MAM1NAM2M2MO"
    url = f'https://www.stat-search.boj.or.jp/ssi/cgi-bin/famecgi2?cgi=$nme_a000_en&lstID={urllib.parse.quote(code)}&stYM=200401&edYM=202612&session=&lstOutput=2'
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode('utf-8', errors='replace')
        
        result = {}
        # Parse CSV response
        lines = text.strip().split('\n')
        for line in lines:
            parts = line.split(',')
            if len(parts) >= 2:
                date_str = parts[0].strip().strip('"')
                val_str = parts[1].strip().strip('"')
                # Try to parse as YYYY/MM format
                try:
                    if '/' in date_str:
                        y, m = date_str.split('/')
                        date_key = f"{y}-{m.zfill(2)}"
                        result[date_key] = float(val_str)
                except (ValueError, IndexError):
                    continue
        
        return result
    except Exception as e:
        print(f"  BOJ M2 JSON fallback ERROR: {e}")
        return {}


# ============================================================
# ECOS API (Korea M2)
# ============================================================
def fetch_ecos_m2():
    """Fetch Korea M2 from Bank of Korea ECOS API.
    
    통계표코드: 101Y003 (M2 광의통화)
    항목코드: BBGA00 (M2 평잔)
    """
    if not ECOS_API_KEY:
        print("  ECOS: No API key, skipping")
        return {}
    
    # ECOS API URL format
    stat_code = '101Y003'
    item_code = 'BBGA00'
    start = '200401'
    end = '202612'
    
    url = (
        f'https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_API_KEY}/json/kr/1/1000/'
        f'{stat_code}/M/{start}/{end}/{item_code}'
    )
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        
        result = {}
        rows = data.get('StatisticSearch', {}).get('row', [])
        for row in rows:
            time = row.get('TIME', '')
            val = row.get('DATA_VALUE', '')
            if time and val:
                # TIME format: YYYYMM
                date_key = f"{time[:4]}-{time[4:6]}"
                result[date_key] = float(val)  # 억원
        
        print(f"  ECOS M2: {len(result)} observations")
        return result
    except Exception as e:
        print(f"  ECOS M2 ERROR: {e}")
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
def convert_to_usd_trillions(m2_local, fx_rates, country, unit_info):
    """Convert local currency M2 to USD trillions.
    
    Args:
        m2_local: dict of {YYYY-MM: value_in_local}
        fx_rates: dict of exchange rate data from FRED
        country: 'eurozone', 'japan', 'korea'
        unit_info: dict describing units
    """
    result = {}
    
    for date, local_val in m2_local.items():
        if country == 'eurozone':
            # ECB data is in Millions EUR, need USD/EUR rate
            usdeur = fx_rates.get('fx_usdeur', {}).get(date)
            if usdeur:
                usd_val = (local_val * 1e6 * usdeur) / 1e12  # to trillions USD
                result[date] = round(usd_val, 2)
        
        elif country == 'japan':
            # BOJ data is in 100 millions JPY (億円), need JPY/USD rate
            jpyusd = fx_rates.get('fx_jpyusd', {}).get(date)
            if jpyusd and jpyusd > 0:
                # Value is in 100 million JPY
                usd_val = (local_val * 1e8 / jpyusd) / 1e12  # to trillions USD
                result[date] = round(usd_val, 2)
        
        elif country == 'korea':
            # ECOS data is in 억원 (100 million KRW), need KRW/USD rate
            krwusd = fx_rates.get('fx_krwusd', {}).get(date)
            if krwusd and krwusd > 0:
                usd_val = (local_val * 1e8 / krwusd) / 1e12  # to trillions USD
                result[date] = round(usd_val, 2)
    
    return result


def assemble_data():
    """Fetch all data and assemble into final JSON."""
    print("=" * 50)
    print(f"BTC vs Global M2 Data Fetch - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    # ---- Fetch data ----
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
    
    # ---- Convert to USD Trillions ----
    print("\n[Converting currencies...]")
    
    # US M2 is already in Billions USD
    us_m2 = {}
    for date, val in us_m2_raw.items():
        us_m2[date] = round(val / 1000, 2)  # Billions -> Trillions
    print(f"  US M2: {len(us_m2)} months in USD trillions")
    
    eu_m2 = convert_to_usd_trillions(eu_m2_raw, fx_rates, 'eurozone', {})
    print(f"  EU M2: {len(eu_m2)} months in USD trillions")
    
    jp_m2 = convert_to_usd_trillions(jp_m2_raw, fx_rates, 'japan', {})
    print(f"  JP M2: {len(jp_m2)} months in USD trillions")
    
    kr_m2 = convert_to_usd_trillions(kr_m2_raw, fx_rates, 'korea', {})
    print(f"  KR M2: {len(kr_m2)} months in USD trillions")
    
    # ---- Compute Global M2 ----
    print("\n[Computing Global M2...]")
    all_dates = sorted(set(us_m2.keys()) | set(eu_m2.keys()) | set(jp_m2.keys()) | set(kr_m2.keys()))
    
    global_m2 = {}
    m2_components = {'us': {}, 'eurozone': {}, 'japan': {}, 'korea': {}}
    
    for date in all_dates:
        us = us_m2.get(date)
        eu = eu_m2.get(date)
        jp = jp_m2.get(date)
        kr = kr_m2.get(date)
        
        # Only include dates where we have at least US + 1 other
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
    
    # ---- Build output ----
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
    
    # ---- Save ----
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\n✅ Saved to {OUTPUT_FILE}")
    print(f"   File size: {os.path.getsize(OUTPUT_FILE):,} bytes")
    

if __name__ == '__main__':
    if not FRED_API_KEY:
        print("⚠️  FRED_API_KEY not set. Set it via environment variable.")
        print("   export FRED_API_KEY=your_key_here")
        sys.exit(1)
    
    assemble_data()
