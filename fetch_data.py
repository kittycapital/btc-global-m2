#!/usr/bin/env python3
"""
BTC vs Global M2 Data Fetcher v4
3 countries: US + Eurozone + Japan (Korea removed due to ECOS API limitations)

Sources:
  - US M2: FRED API (M2SL) - Billions USD
  - Eurozone M2: ECB Data Portal (BSI.M.U2.Y.V.M20.X.1.U2.2300.Z01.E) - Millions EUR
  - Japan M2: BOJ HTML table scrape (md02_m_1_en.html) - 100M JPY
  - Exchange Rates: FRED (EXJPUS, DEXUSEU)
  - BTC: data/BTC_USD.csv
"""

import os, sys, json, csv, re, urllib.request, urllib.parse
from datetime import datetime
from collections import defaultdict
from html.parser import HTMLParser

FRED_API_KEY = os.environ.get('FRED_API_KEY', '')
OUTPUT_FILE = 'data/m2_btc_data.json'
BTC_CSV = 'data/BTC_USD.csv'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; HerdvibBot/1.0)'}


def fetch_fred(series_id, start='2004-01-01'):
    params = urllib.parse.urlencode({
        'series_id': series_id, 'api_key': FRED_API_KEY,
        'file_type': 'json', 'observation_start': start,
        'frequency': 'm', 'aggregation_method': 'avg',
    })
    url = f'https://api.stlouisfed.org/fred/series/observations?{params}'
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        result = {}
        for obs in data.get('observations', []):
            v = obs['value']
            if v != '.' and v != '':
                result[obs['date'][:7]] = float(v)
        print(f"  FRED {series_id}: {len(result)} obs")
        return result
    except Exception as e:
        print(f"  FRED {series_id} ERROR: {e}")
        return {}


def fetch_ecb_m2():
    key = 'M.U2.Y.V.M20.X.1.U2.2300.Z01.E'
    url = f'https://data-api.ecb.europa.eu/service/data/BSI/{key}?startPeriod=2004-01&format=csvdata'
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
        result = {}
        for row in csv.DictReader(text.splitlines()):
            p, v = row.get('TIME_PERIOD',''), row.get('OBS_VALUE','')
            if p and v: result[p] = float(v)
        print(f"  ECB M2: {len(result)} obs")
        return result
    except Exception as e:
        print(f"  ECB ERROR: {e}")
        try:  # fallback NSA
            key2 = 'M.U2.N.V.M20.X.1.U2.2300.Z01.E'
            url2 = f'https://data-api.ecb.europa.eu/service/data/BSI/{key2}?startPeriod=2004-01&format=csvdata'
            req2 = urllib.request.Request(url2, headers=HEADERS)
            with urllib.request.urlopen(req2, timeout=60) as r2:
                t2 = r2.read().decode()
            res2 = {}
            for row in csv.DictReader(t2.splitlines()):
                p, v = row.get('TIME_PERIOD',''), row.get('OBS_VALUE','')
                if p and v: res2[p] = float(v)
            print(f"  ECB M2 (NSA): {len(res2)} obs")
            return res2
        except Exception as e2:
            print(f"  ECB fallback ERROR: {e2}")
            return {}


class BOJParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = self.in_row = self.in_cell = False
        self.current_row, self.rows, self.cell_text = [], [], ''
    def handle_starttag(self, tag, attrs):
        if tag == 'table': self.in_table = True
        elif tag == 'tr' and self.in_table: self.in_row = True; self.current_row = []
        elif tag in ('td','th') and self.in_row: self.in_cell = True; self.cell_text = ''
    def handle_endtag(self, tag):
        if tag == 'table': self.in_table = False
        elif tag == 'tr' and self.in_row:
            self.in_row = False
            if self.current_row: self.rows.append(self.current_row)
        elif tag in ('td','th') and self.in_cell:
            self.in_cell = False; self.current_row.append(self.cell_text.strip())
    def handle_data(self, data):
        if self.in_cell: self.cell_text += data


def fetch_boj_m2():
    url = 'https://www.stat-search.boj.or.jp/ssi/mtshtml/md02_m_1_en.html'
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode('utf-8', errors='replace')
        parser = BOJParser()
        parser.feed(text)
        result = {}
        for row in parser.rows:
            if len(row) <= 9: continue
            m = re.match(r'^(\d{4})/(\d{2})$', row[0].strip())
            if not m: continue
            val = row[9].strip().replace(',','')
            if val and val != 'ND':
                try: result[f"{m.group(1)}-{m.group(2)}"] = float(val)
                except ValueError: pass
        print(f"  BOJ M2: {len(result)} obs")
        return result
    except Exception as e:
        print(f"  BOJ ERROR: {e}")
        return {}


def load_btc_csv():
    if not os.path.exists(BTC_CSV):
        print(f"  BTC CSV not found: {BTC_CSV}")
        return {}
    monthly = defaultdict(list)
    with open(BTC_CSV, 'r') as f:
        for row in csv.DictReader(f):
            d, c = row.get('Date',''), row.get('Close','')
            if d and c:
                try: monthly[d[:7]].append(float(c))
                except: pass
    result = {ym: round(sum(p)/len(p), 2) for ym, p in sorted(monthly.items())}
    print(f"  BTC: {len(result)} months")
    return result


def assemble_data():
    print("=" * 60)
    print(f"BTC vs Global M2 v4 (US+EU+JP) - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    print("\n[1/5] US M2 from FRED...")
    us_m2_raw = fetch_fred('M2SL')

    print("\n[2/5] Exchange rates from FRED...")
    fx_jpyusd = fetch_fred('EXJPUS')
    fx_usdeur = fetch_fred('DEXUSEU')

    print("\n[3/5] Eurozone M2 from ECB...")
    eu_m2_raw = fetch_ecb_m2()

    print("\n[4/5] Japan M2 from BOJ...")
    jp_m2_raw = fetch_boj_m2()

    print("\n[5/5] BTC price...")
    btc = load_btc_csv()

    # Convert to USD Trillions
    print("\n[Converting...]")
    us_m2 = {d: round(v/1000, 2) for d, v in us_m2_raw.items()}
    print(f"  US: {len(us_m2)} months")

    eu_m2 = {}
    for d, v in eu_m2_raw.items():
        fx = fx_usdeur.get(d)
        if fx: eu_m2[d] = round((v * 1e6 * fx) / 1e12, 2)
    print(f"  EU: {len(eu_m2)} months")

    jp_m2 = {}
    for d, v in jp_m2_raw.items():
        fx = fx_jpyusd.get(d)
        if fx and fx > 0: jp_m2[d] = round((v * 1e8 / fx) / 1e12, 2)
    print(f"  JP: {len(jp_m2)} months")

    # Global M2 (US + EU + JP)
    print("\n[Global M2...]")
    all_dates = sorted(set(us_m2) | set(eu_m2) | set(jp_m2))
    global_m2, comp = {}, {'us': {}, 'eurozone': {}, 'japan': {}}

    for d in all_dates:
        us, eu, jp = us_m2.get(d), eu_m2.get(d), jp_m2.get(d)
        avail = [v for v in [us, eu, jp] if v is not None]
        if us and len(avail) >= 2:
            global_m2[d] = round(sum(avail), 2)
            if us: comp['us'][d] = us
            if eu: comp['eurozone'][d] = eu
            if jp: comp['japan'][d] = jp

    print(f"  Global M2: {len(global_m2)} months")
    if global_m2:
        ds = sorted(global_m2)
        print(f"  Range: {ds[0]} to {ds[-1]}, Latest: ${global_m2[ds[-1]]}T")

    output = {
        'last_updated': datetime.now().strftime('%Y-%m-%d'),
        'btc_monthly_avg': btc,
        'global_m2': global_m2,
        'm2_components': comp,
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n✅ {OUTPUT_FILE} ({os.path.getsize(OUTPUT_FILE):,} bytes)")
    for n, c in [('US',len(us_m2)),('EU',len(eu_m2)),('JP',len(jp_m2)),('BTC',len(btc)),('Global',len(global_m2))]:
        print(f"   {n:8s} {'✅' if c>100 else '⚠️'}  {c}")

if __name__ == '__main__':
    if not FRED_API_KEY:
        print("⚠️  FRED_API_KEY not set"); sys.exit(1)
    assemble_data()
