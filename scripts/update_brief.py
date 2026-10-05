import csv
import io
import json
import math
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

HEADERS = {
    "User-Agent": "Mozilla/5.0 CHRIS-Investment-Brief/2.0",
    "Accept": "*/*",
}

TAIPEI = timezone(timedelta(hours=8))

def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8-sig")

# ============================================================
# Yahoo public market data
# Observed prices, not an official exchange real-time feed.
# ============================================================

def yahoo(symbol):
    encoded = urllib.parse.quote(symbol, safe="")
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + encoded
        + "?range=10d&interval=1d"
    )

    raw = json.loads(get(url))
    result = raw["chart"]["result"]

    if not result:
        raise RuntimeError("Yahoo returned no result")

    item = result[0]
    timestamps = item.get("timestamp", [])
    closes = item["indicators"]["quote"][0]["close"]

    pairs = [
        (timestamp, float(close))
        for timestamp, close in zip(timestamps, closes)
        if timestamp is not None
        and close is not None
        and math.isfinite(float(close))
        and float(close) > 0
    ]

    if len(pairs) < 2:
        raise RuntimeError("Not enough valid Yahoo observations")

    previous = pairs[-2][1]
    latest = pairs[-1][1]
    change = (latest / previous - 1) * 100

    timezone_name = item.get("meta", {}).get(
        "exchangeTimezoneName", "UTC"
    )

    try:
        exchange_timezone = ZoneInfo(timezone_name)
    except Exception:
        exchange_timezone = timezone.utc

    as_of = (
        datetime.fromtimestamp(pairs[-1][0], timezone.utc)
        .astimezone(exchange_timezone)
        .date()
        .isoformat()
    )

    return latest, change, as_of

def market_item(name, symbol):
    try:
        value, change, as_of = yahoo(symbol)
        return {
            "name": name,
            "value": f"{value:,.2f}",
            "change": f"{change:+.2f}%",
            "tone": "up" if change > 0 else (
                "down" if change < 0 else ""
            ),
            "status": "VERIFIED",
            "as_of": as_of,
            "source": "Yahoo Finance public market feed",
        }
    except Exception as error:
        print(f"[MARKET ERROR] {name}: {error}")
        return {
            "name": name,
            "value": "—",
            "change": "SOURCE ERROR",
            "tone": "warn",
            "status": "SOURCE ERROR",
            "as_of": "—",
            "source": "Yahoo Finance public market feed",
        }

# ============================================================
# Official U.S. Treasury daily par yield curve
# ============================================================

def treasury_yields():
    current_year = datetime.now(timezone.utc).year
    output = []

    for year in (current_year, current_year - 1):
        url = (
            "https://home.treasury.gov/"
            "resource-center/data-chart-center/"
            "interest-rates/pages/xml"
            "?data=daily_treasury_yield_curve"
            f"&field_tdr_date_value={year}"
        )

        try:
            root = ET.fromstring(get(url))

            for entry in root.iter():
                if entry.tag.split("}")[-1] != "properties":
                    continue

                values = {
                    child.tag.split("}")[-1]:
                    child.text.strip() if child.text else None
                    for child in entry
                }

                date_raw = values.get("NEW_DATE")
                if not date_raw:
                    continue

                def number(key):
                    value = values.get(key)
                    if value in (None, "", "null"):
                        return None
                    try:
                        number_value = float(value)
                        return (
                            number_value
                            if math.isfinite(number_value)
                            else None
                        )
                    except Exception:
                        return None

                output.append({
                    "date": date_raw[:10],
                    "2y": number("BC_2YEAR"),
                    "10y": number("BC_10YEAR"),
                    "30y": number("BC_30YEAR"),
                })

        except Exception as error:
            print(f"[TREASURY ERROR] {year}: {error}")

    unique = {row["date"]: row for row in output}
    rows = sorted(
        unique.values(), key=lambda row: row["date"]
    )

    if not rows:
        raise RuntimeError(
            "U.S. Treasury returned no valid observations"
        )

    return rows

# ============================================================
# Official Cboe VIX
# ============================================================

def cboe_vix():
    url = (
        "https://cdn.cboe.com/api/global/"
        "us_indices/daily_prices/VIX_History.csv"
    )

    reader = csv.DictReader(io.StringIO(get(url)))
    rows = []

    for row in reader:
        date_raw = row.get("DATE") or row.get("Date")
        close_raw = row.get("CLOSE") or row.get("Close")

        if not date_raw or not close_raw:
            continue

        parsed_date = None
        for format_string in (
            "%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"
        ):
            try:
                parsed_date = datetime.strptime(
                    date_raw.strip(), format_string
                ).date().isoformat()
                break
            except ValueError:
                pass

        if not parsed_date:
            continue

        try:
            close = float(close_raw)
        except Exception:
            continue

        if not math.isfinite(close) or close < 0:
            continue

        rows.append((parsed_date, close))

    rows.sort(key=lambda row: row[0])

    if not rows:
        raise RuntimeError(
            "Cboe returned no valid VIX observations"
        )

    return rows

# ============================================================
# Official New York Fed EFFR
# ============================================================

def nyfed_effr():
    urls = [
        "https://markets.newyorkfed.org/api/"
        "rates/unsecured/effr/last/1.json",
        "https://markets.newyorkfed.org/api/"
        "rates/all/latest.json",
    ]

    last_error = None

    for url in urls:
        try:
            payload = json.loads(get(url))
            candidates = []

            if isinstance(payload, dict):
                for key in ("refRates", "rates", "data"):
                    value = payload.get(key)
                    if isinstance(value, list):
                        candidates.extend(value)
            elif isinstance(payload, list):
                candidates = payload

            for row in candidates:
                if not isinstance(row, dict):
                    continue

                rate_type = str(
                    row.get("type")
                    or row.get("rateType")
                    or row.get("rate_type")
                    or ""
                ).upper()

                if (
                    "all/latest" in url
                    and "EFFR" not in rate_type
                ):
                    continue

                rate_raw = (
                    row.get("percentRate")
                    or row.get("rate")
                    or row.get("percent_rate")
                )

                effective_date = (
                    row.get("effectiveDate")
                    or row.get("effective_date")
                    or row.get("date")
                )

                if rate_raw is None or not effective_date:
                    continue

                value = float(rate_raw)
                if not math.isfinite(value):
                    continue

                return str(effective_date)[:10], value

        except Exception as error:
            last_error = error
            print(f"[NY FED ERROR] {url}: {error}")

    raise RuntimeError(
        f"NY Fed EFFR unavailable: {last_error}"
    )

# ============================================================
# Fetch observed market data
# ============================================================

symbols = [
    ("S&P 500", "^GSPC"),
    ("NASDAQ", "^IXIC"),
    ("SOX", "^SOX"),
    ("DXY", "DX-Y.NYB"),
    ("USD/TWD", "TWD=X"),
    ("GOLD", "GC=F"),
    ("WTI", "CL=F"),
]

market = {}
tickers = []

for name, symbol in symbols:
    item = market_item(name, symbol)
    market[name] = item
    tickers.append(item)

treasury_ok = False
cboe_ok = False
nyfed_ok = False

treasury = []
vix_rows = []

y2 = None
y10 = None
y30 = None
treasury_date = "—"

vix = None
vix_date = "—"

effr = None
effr_date = "—"

try:
    treasury = treasury_yields()
    latest = treasury[-1]

    treasury_date = latest["date"]
    y2 = latest["2y"]
    y10 = latest["10y"]
    y30 = latest["30y"]

    treasury_ok = all(
        value is not None for value in (y2, y10, y30)
    )
except Exception as error:
    print(f"[TREASURY FATAL] {error}")

try:
    vix_rows = cboe_vix()
    vix_date, vix = vix_rows[-1]
    cboe_ok = True
except Exception as error:
    print(f"[CBOE FATAL] {error}")

try:
    effr_date, effr = nyfed_effr()
    nyfed_ok = True
except Exception as error:
    print(f"[NYFED FATAL] {error}")

tickers.insert(3, {
    "name": "US 10Y",
    "value": f"{y10:.2f}%" if y10 is not None else "—",
    "change": (
        treasury_date if y10 is not None else "SOURCE ERROR"
    ),
    "tone": "up" if y10 is not None else "warn",
    "status": (
        "VERIFIED" if y10 is not None else "SOURCE ERROR"
    ),
    "as_of": treasury_date,
    "source": "U.S. Department of the Treasury",
})

tickers.append({
    "name": "VIX",
    "value": f"{vix:.2f}" if vix is not None else "—",
    "change": vix_date if vix is not None else "SOURCE ERROR",
    "tone": "up" if vix is not None else "warn",
    "status": (
        "VERIFIED" if vix is not None else "SOURCE ERROR"
    ),
    "as_of": vix_date,
    "source": "Cboe Global Markets",
})

tickers.append({
    "name": "MOVE",
    "value": "—",
    "change": "NO FEED",
    "tone": "warn",
    "status": "NO FEED",
    "as_of": "—",
    "source": "No licensed automated feed",
})

# Missing sectors are omitted, never replaced with fake 0%.
sector_symbols = [
    ("能源", "XLE"),
    ("工業", "XLI"),
    ("資訊科技", "XLK"),
    ("公用事業", "XLU"),
    ("金融", "XLF"),
    ("非必需消費", "XLY"),
    ("必需消費", "XLP"),
    ("原材料", "XLB"),
    ("房地產", "XLRE"),
    ("通訊服務", "XLC"),
    ("醫療保健", "XLV"),
]

sectors = []

for name, symbol in sector_symbols:
    try:
        value, change, as_of = yahoo(symbol)
        sectors.append({
            "name": name,
            "change": round(change, 2),
            "status": "VERIFIED",
            "as_of": as_of,
            "source": "Yahoo Finance public market feed",
        })
    except Exception as error:
        print(f"[SECTOR ERROR] {name}: {error}")

# Align VIX history to actual Treasury dates.
# Missing observations remain null; no interpolation.
history_rows = [
    row for row in treasury if row.get("10y") is not None
][-20:]

dates = [row["date"][5:] for row in history_rows]
yields = [row["10y"] for row in history_rows]
vix_map = dict(vix_rows)
vix_history = [
    vix_map.get(row["date"]) for row in history_rows
]

def rate(name, value, as_of, source):
    return {
        "name": name,
        "value": (
            f"{value:.2f}%" if value is not None else "—"
        ),
        "date": (
            as_of if value is not None else "SOURCE ERROR"
        ),
        "status": (
            "VERIFIED" if value is not None else "SOURCE ERROR"
        ),
        "source": source,
    }

def commodity(name):
    item = market[name]
    return {
        "name": name,
        "value": item["value"],
        "change": item["change"],
        "tone": item["tone"],
        "status": item["status"],
        "as_of": item["as_of"],
        "source": item["source"],
    }

market_ok = all(
    item["status"] == "VERIFIED" for item in market.values()
)
official_ok = treasury_ok and cboe_ok and nyfed_ok

overall_status = (
    "AUTO · VERIFIED"
    if official_ok and market_ok
    else "AUTO · PARTIAL"
)

now = datetime.now(TAIPEI).strftime(
    "%Y-%m-%d %H:%M Asia/Taipei"
)

# Preserve the existing frontend data structure.
data = {
    "status": overall_status,
    "updated_at": now,
    "bluf": {
        "headline": "全球市場、官方利率與波動率監控",
        "brief": (
            "CHRIS 投資早報採可驗證市場資料。"
            "美債殖利率使用 U.S. Treasury，"
            "VIX 使用 Cboe，EFFR 使用 New York Fed。"
            "無法取得的資料不以推算值取代。"
        ),
        "theme": "Verified Data · Rates · Markets · Volatility",
    },
    "risk_note": (
        "US Treasury 與 VIX 使用官方發布資料；"
        "EFFR 使用 New York Fed 官方資料。"
        "股指、FX、商品與產業 ETF 為公開市場行情來源，"
        "非交易所授權即時 feed。"
        "MOVE 尚未接入授權來源，因此不估算。"
    ),
    "tickers": tickers,
    "risk": [
        {
            "name": "VIX",
            "value": f"{vix:.2f}" if vix is not None else "—",
            "label": (
                vix_date if vix is not None else "SOURCE ERROR"
            ),
            "tone": "up" if vix is not None else "warn",
        },
        {
            "name": "US 10Y",
            "value": f"{y10:.2f}%" if y10 is not None else "—",
            "label": (
                treasury_date
                if y10 is not None
                else "SOURCE ERROR"
            ),
            "tone": "up" if y10 is not None else "warn",
        },
        {
            "name": "MOVE",
            "value": "—",
            "label": "NO FEED",
            "tone": "warn",
        },
    ],
    "news": [
        {
            "title": "Rates",
            "summary": (
                f"U.S. Treasury 官方 10 年期殖利率 "
                f"{y10:.2f}%，資料日 {treasury_date}。"
                if y10 is not None
                else "U.S. Treasury 官方資料暫時無法取得。"
            ),
        },
        {
            "title": "Equities",
            "summary": (
                "追蹤 S&P 500、NASDAQ、SOX "
                "最近有效交易日收盤表現。"
            ),
        },
        {
            "title": "Volatility",
            "summary": (
                f"Cboe VIX {vix:.2f}，資料日 {vix_date}。"
                if vix is not None
                else "Cboe VIX 官方資料暫時無法取得。"
            ),
        },
        {
            "title": "Cross Asset",
            "summary": (
                "同步觀察美元、黃金與 WTI，"
                "作為跨資產風險訊號。"
            ),
        },
    ],
    "watch": [
        ["US 10Y", "長端利率方向"],
        ["SOX", "科技風險偏好"],
        ["VIX", "股票波動率"],
        ["WTI", "能源與通膨風險"],
        ["DXY", "美元趨勢"],
    ],
    "sectors": sectors,
    "rates": [
        rate("US 2Y", y2, treasury_date, "U.S. Treasury"),
        rate("US 10Y", y10, treasury_date, "U.S. Treasury"),
        rate("US 30Y", y30, treasury_date, "U.S. Treasury"),
        rate(
            "EFFR", effr, effr_date,
            "Federal Reserve Bank of New York"
        ),
    ],
    "commodities": [
        commodity("WTI"), commodity("GOLD")
    ],
    "calendar": [{
        "time": "—",
        "country": "—",
        "event": "NO VERIFIED ECONOMIC CALENDAR FEED",
        "actual": "—",
        "forecast": "—",
        "previous": "—",
    }],
    "vol_signals": [
        [
            "VIX",
            f"{vix:.2f}" if vix is not None else "SOURCE ERROR"
        ],
        ["MOVE", "NO LICENSED FEED"],
        [
            "US 10Y",
            f"{y10:.2f}%" if y10 is not None else "SOURCE ERROR"
        ],
        ["Regime", "Verified Data"],
    ],
    "fed": [
        [
            "EFFR",
            f"{effr:.2f}%" if effr is not None else "SOURCE ERROR"
        ],
        ["Data Date", effr_date],
        ["Source", "Federal Reserve Bank of New York"],
    ],
    "levels": [
        {"name": "S&P 500", "text": market["S&P 500"]["value"]},
        {"name": "NASDAQ", "text": market["NASDAQ"]["value"]},
        {"name": "SOX", "text": market["SOX"]["value"]},
        {
            "name": "US 10Y",
            "text": f"{y10:.2f}%" if y10 is not None else "—",
        },
        {"name": "DXY", "text": market["DXY"]["value"]},
    ],
    "chris_view": "",
    "sources": [
        {
            "name": "U.S. Treasury",
            "ok": treasury_ok,
            "note": (
                f"Official · {treasury_date}"
                if treasury_ok else "SOURCE ERROR"
            ),
        },
        {
            "name": "Cboe VIX",
            "ok": cboe_ok,
            "note": (
                f"Official · {vix_date}"
                if cboe_ok else "SOURCE ERROR"
            ),
        },
        {
            "name": "New York Fed EFFR",
            "ok": nyfed_ok,
            "note": (
                f"Official · {effr_date}"
                if nyfed_ok else "SOURCE ERROR"
            ),
        },
        {
            "name": "Yahoo Market Feed",
            "ok": market_ok,
            "note": (
                "Public market data · not official exchange feed"
            ),
        },
        {
            "name": "MOVE",
            "ok": False,
            "note": "NO LICENSED FEED · no estimation used",
        },
        {
            "name": "CHRIS CIO Layer",
            "ok": True,
            "note": "Analysis only · does not create market data",
        },
    ],
    "history": {
        "dates": dates,
        "dgs10": yields,
        "vix": vix_history,
    },
    "curve": [y2, y10, y30],
}

# ============================================================
# Transparent analysis layer
# All spreads are DERIVED, not raw market observations.
# ============================================================

def enrich(data, treasury, today=None):
    today = today or datetime.now(
        ZoneInfo("America/New_York")
    ).date()

    # Calendar-day tolerance for weekends/publication delays.
    # This does not imply real-time freshness.
    max_age = 7

    def number(value):
        try:
            result = float(
                str(value).replace(",", "").replace("%", "")
            )
            return result if math.isfinite(result) else None
        except (ValueError, TypeError):
            return None

    def health(value, as_of, status="VERIFIED"):
        if status != "VERIFIED":
            return (
                status
                if status in ("SOURCE ERROR", "STALE", "NO FEED")
                else "SOURCE ERROR"
            )

        if number(value) is None:
            return "SOURCE ERROR"

        try:
            age = (today - date.fromisoformat(as_of)).days
            if 0 <= age <= max_age:
                return "VERIFIED"
            return "STALE" if age > max_age else "SOURCE ERROR"
        except (ValueError, TypeError):
            return "SOURCE ERROR"

    for group in ("tickers", "commodities", "rates", "sectors"):
        for item in data[group]:
            value = item.get("value", item.get("change"))
            item["status"] = health(
                value,
                item.get("as_of", item.get("date")),
                item["status"],
            )

            if item["status"] != "VERIFIED":
                if group == "rates":
                    item["date"] = (
                        item["status"]
                        + " · "
                        + item.get("date", "—")
                    )
                elif group != "sectors":
                    item["change"] = item["status"]
                    item["tone"] = "warn"

            elif (
                group in ("tickers", "commodities")
                and "%" in item["change"]
            ):
                change = number(item["change"])
                item["tone"] = (
                    "up" if change > 0
                    else "down" if change < 0
                    else ""
                )

    quotes = {item["name"]: item for item in data["tickers"]}
    rates = {item["name"]: item for item in data["rates"]}
    inputs = {}

    for name in (
        "S&P 500", "NASDAQ", "SOX", "VIX", "DXY", "GOLD", "WTI"
    ):
        item = quotes[name]
        inputs[name] = {
            "value": number(item["value"]),
            "change_pct": (
                number(item["change"])
                if "%" in item["change"] else None
            ),
            "as_of": item["as_of"],
            "status": item["status"],
            "source": item["source"],
        }

    for name in ("US 2Y", "US 10Y", "US 30Y", "EFFR"):
        item = rates[name]
        inputs[name] = {
            "value": number(item["value"]),
            "as_of": item["date"],
            "status": item["status"],
            "source": item["source"],
        }

    def spread(name, long_name, short_name, aligned=None):
        long_input = aligned or inputs[long_name]
        short_input = inputs[short_name]
        statuses = [
            long_input["status"], short_input["status"]
        ]

        status = next(
            (
                item
                for item in ("SOURCE ERROR", "NO FEED", "STALE")
                if item in statuses
            ),
            "VERIFIED",
        )

        if (
            status == "VERIFIED"
            and long_input["as_of"] != short_input["as_of"]
        ):
            status = "SOURCE ERROR"

        basis_points = None

        if status == "VERIFIED":
            basis_points = float(
                (
                    Decimal(str(long_input["value"]))
                    - Decimal(str(short_input["value"]))
                ) * 100
            )

        return {
            "name": name,
            "value": (
                f"{basis_points:+.1f} bp"
                if basis_points is not None else "—"
            ),
            "value_bp": basis_points,
            "date": (
                long_input["as_of"]
                if status == "VERIFIED" else status
            ),
            "status": status,
            "formula": f"({long_name} - {short_name}) × 100",
            "derived": True,
            "inputs": {
                long_name: long_input,
                short_name: short_input,
            },
            "source": "Derived from official observed rates",
        }

    # EFFR can be published with a lag.
    # Use an actual Treasury observation on its effective date.
    # Never interpolate or mix dates in this spread.
    effr_day = inputs["EFFR"]["as_of"]

    aligned_row = next(
        (
            row for row in treasury
            if row["date"] == effr_day
        ),
        None,
    )

    aligned_value = (
        aligned_row["10y"] if aligned_row else None
    )

    aligned = {
        "value": aligned_value,
        "as_of": effr_day,
        "status": health(aligned_value, effr_day),
        "source": "U.S. Treasury",
    }

    spreads = [
        spread("2Y–10Y (10Y−2Y)", "US 10Y", "US 2Y"),
        spread("10Y–30Y (30Y−10Y)", "US 30Y", "US 10Y"),
        spread("2Y–30Y (30Y−2Y)", "US 30Y", "US 2Y"),
        spread("10Y−EFFR", "US 10Y", "EFFR", aligned),
    ]

    data["spreads"] = spreads

    # Existing Rates table renders these without HTML changes.
    data["rates"].extend(spreads)

    signals = []

    def signal(name, dependencies, rule, function):
        bad = [
            f'{name}: {inputs[name]["status"]}'
            for name in dependencies
            if inputs[name]["status"] != "VERIFIED"
        ]

        text = "；".join(bad) if bad else function()

        signals.append({
            "name": name,
            "rule": rule,
            "inputs": dependencies,
            "status": "SOURCE ERROR" if bad else "VERIFIED",
            "text": text,
        })

    equities_names = ["S&P 500", "NASDAQ", "SOX"]

    aligned_equities = len({
        inputs[name]["as_of"] for name in equities_names
    }) == 1

    def equities_text():
        if not aligned_equities:
            return (
                "SOURCE ERROR：股指日期不一致，"
                "停止相對強弱比較。"
            )

        changes = {
            name: inputs[name]["change_pct"]
            for name in equities_names
        }

        technology_leads = (
            changes["NASDAQ"] > changes["S&P 500"]
            and changes["SOX"] > changes["S&P 500"]
        )

        return (
            "同日收盤漲跌："
            + "、".join(
                f"{name} {changes[name]:+.2f}%"
                for name in equities_names
            )
            + (
                "；科技與半導體當日領先大盤。"
                if technology_leads
                else "；科技與半導體未同步領先大盤。"
            )
        )

    signal(
        "Equities",
        equities_names,
        "Compare same-date daily percent changes; "
        "no multi-day trend inference.",
        equities_text,
    )

    signal(
        "Volatility",
        ["VIX"],
        "VIX <20 low; 20–<30 elevated; >=30 high.",
        lambda: (
            f'VIX {inputs["VIX"]["value"]:.2f}：'
            + (
                "低波動" if inputs["VIX"]["value"] < 20
                else "波動升高" if inputs["VIX"]["value"] < 30
                else "高波動"
            )
        ),
    )

    for item in spreads:
        signals.append({
            "name": item["name"],
            "rule": (
                item["formula"]
                + "; positive/zero/negative spread only, "
                "no causal inference."
            ),
            "status": item["status"],
            "inputs": list(item["inputs"]),
            "text": (
                f'{item["name"]} {item["value"]}'
                f'（{item["date"]}）'
                if item["value_bp"] is not None
                else f'{item["name"]}: {item["status"]}'
            ),
        })

    signal(
        "Dollar",
        ["DXY"],
        "Daily change >0 rising; <0 falling; =0 unchanged.",
        lambda: (
            f'DXY 當日 {inputs["DXY"]["change_pct"]:+.2f}%；美元'
            + (
                "走強" if inputs["DXY"]["change_pct"] > 0
                else "走弱" if inputs["DXY"]["change_pct"] < 0
                else "持平"
            )
        ),
    )

    signal(
        "Gold",
        ["GOLD"],
        "Daily change >=1% flags a large rise; "
        "not evidence of safe-haven causality.",
        lambda: (
            f'黃金當日 {inputs["GOLD"]["change_pct"]:+.2f}%'
            + (
                "；達單日上漲 1% 觀察門檻。"
                if inputs["GOLD"]["change_pct"] >= 1
                else "。"
            )
        ),
    )

    signal(
        "Oil",
        ["WTI"],
        "WTI daily change >=2% flags energy-price pressure; "
        "no inflation forecast.",
        lambda: (
            f'WTI 當日 {inputs["WTI"]["change_pct"]:+.2f}%'
            + (
                "；達單日上漲 2% 能源價格壓力門檻。"
                if inputs["WTI"]["change_pct"] >= 2
                else "。"
            )
        ),
    )

    complete = (
        all(
            item["status"] == "VERIFIED"
            for item in inputs.values()
        )
        and all(
            item["status"] == "VERIFIED"
            for item in spreads
        )
        and aligned_equities
    )

    regime = "INSUFFICIENT DATA"

    if complete:
        changes = [
            inputs[name]["change_pct"]
            for name in equities_names
        ]
        volatility = inputs["VIX"]["value"]

        if (
            volatility >= 30
            or (
                volatility >= 20
                and sum(change < 0 for change in changes) >= 2
            )
        ):
            regime = "Risk-Off"
        elif (
            volatility < 20
            and all(change > 0 for change in changes)
        ):
            regime = "Risk-On"
        else:
            regime = "Mixed"

    rules = {
        "version": "1.0",
        "freshness": (
            "0–7 calendar days relative to America/New_York; "
            "future/invalid dates = SOURCE ERROR; "
            "older = STALE. This is not a real-time guarantee."
        ),
        "regime": (
            "Require all market/rate inputs and four "
            "synchronized spreads; same-date equities. "
            "Risk-Off: VIX>=30 OR VIX>=20 and "
            ">=2 negative indices. "
            "Else Risk-On: VIX<20 and all three indices positive. "
            "Else Mixed."
        ),
        "spreads": (
            "Long minus short in basis points. "
            "10Y−EFFR uses Treasury observation on "
            "EFFR effective date; no interpolation."
        ),
        "interpretation": (
            "Single-session descriptive rules, "
            "not forecasts or recommendations. "
            "Gold/DXY/WTI/curve are contextual signals, "
            "not regime votes."
        ),
    }

    data["risk_regime"] = {
        "name": regime,
        "status": "VERIFIED" if complete else "SOURCE ERROR",
        "rules_version": "1.0",
    }

    data["analysis"] = {
        "inputs": inputs,
        "signals": signals,
        "rules": rules,
    }

    view = (
        f"CHRIS 觀點｜{regime}。"
        + " ".join(item["text"] for item in signals)
        + " 判讀僅反映已公布數據與單日變動；"
        "不推論資金流向或未來走勢。"
    )

    data["chris_view"] = view

    data["bluf"] = {
        "headline": f"CHRIS 觀點｜{regime}",
        "brief": view,
        "theme": "Rule-based · Verified Inputs · " + regime,
    }

    data["vol_signals"] = [
        [key, regime if key == "Regime" else value]
        for key, value in data["vol_signals"]
    ]

    data["vol_signals"].extend([
        [
            item["name"],
            item["value"]
            if item["status"] == "VERIFIED"
            else item["status"],
        ]
        for item in spreads
    ])

    for item in data["risk"]:
        if (
            item["name"] in quotes
            and quotes[item["name"]]["status"] != "VERIFIED"
        ):
            item.update(
                label=quotes[item["name"]]["status"],
                tone="warn",
            )

    source_members = {
        "U.S. Treasury": ["US 2Y", "US 10Y", "US 30Y"],
        "Cboe VIX": ["VIX"],
        "New York Fed EFFR": ["EFFR"],
        "Yahoo Market Feed": [
            "S&P 500", "NASDAQ", "SOX", "DXY", "GOLD", "WTI"
        ],
    }

    for item in data["sources"]:
        if item["name"] in source_members:
            bad = [
                f'{name}: {inputs[name]["status"]}'
                for name in source_members[item["name"]]
                if inputs[name]["status"] != "VERIFIED"
            ]
            if bad:
                item.update(ok=False, note="；".join(bad))

        if item["name"] == "CHRIS CIO Layer":
            item.update(
                ok=complete,
                note=f"Rules 1.0 · {regime} · analysis only",
            )

    problems = [
        f'{item["name"]}: {item["status"]}'
        for group in ("tickers", "rates", "sectors")
        for item in data[group]
        if item["status"] != "VERIFIED"
    ]

    available_sectors = {
        item["name"] for item in data["sectors"]
    }

    data["sector_errors"] = [
        {"name": name, "status": "SOURCE ERROR"}
        for name, symbol in sector_symbols
        if name not in available_sectors
    ]

    problems.extend(
        f'{item["name"]}: SOURCE ERROR'
        for item in data["sector_errors"]
    )

    data["risk_note"] += (
        " 新鮮度門檻：7 個日曆日；各資料日期可能不同。"
        + "；".join(problems)
    )

    data["status"] = (
        "AUTO · VERIFIED"
        if complete
        and not data["sector_errors"]
        and all(
            item["status"] == "VERIFIED"
            for item in data["sectors"]
        )
        else "AUTO · PARTIAL"
    )

    # Reject NaN/Infinity instead of emitting invalid JSON.
    json.dumps(data, allow_nan=False)
    return data

data = enrich(data, treasury)
overall_status = data["status"]

# Real observed history for the five CHRIS charts; never synthesize prices.
def build_level_history(treasury):
    output = []
    today = datetime.now(ZoneInfo('America/New_York')).date()
    for name, symbol in [('S&P 500', '^GSPC'), ('NASDAQ', '^IXIC'), ('SOX', '^SOX'), ('DXY', 'DX-Y.NYB')]:
        source = 'Yahoo Finance public market feed'
        try:
            url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol, safe='') + '?range=3mo&interval=1d'
            result = json.loads(get(url))['chart']['result']
            if not result:
                raise RuntimeError('No historical observations')
            item = result[0]
            exchange_tz = ZoneInfo(item['meta']['exchangeTimezoneName'])
            points = {}
            for timestamp, close in zip(item.get('timestamp', []), item['indicators']['quote'][0]['close']):
                if timestamp is None:
                    continue
                day = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(exchange_tz).date()
                if day > today:
                    continue
                value = float(close) if close is not None else None
                points[day.isoformat()] = value if value is not None and math.isfinite(value) and value > 0 else None
            rows = sorted(points.items())[-60:]
            valid = [(day, value) for day, value in rows if value is not None]
            if len(valid) < 2:
                raise RuntimeError('Fewer than two valid historical observations')
            as_of = valid[-1][0]
            status = 'STALE' if (today - datetime.strptime(as_of, '%Y-%m-%d').date()).days > 7 else 'VERIFIED'
            output.append(dict(name=name, dates=[day for day,value in rows], values=[value for day,value in rows], status=status, as_of=as_of, source=source))
        except Exception as error:
            print(f'[HISTORY ERROR] {name}: {error}')
            output.append(dict(name=name, dates=[], values=[], status='SOURCE ERROR', as_of='—', source=source))
    rows = [row for row in treasury if row['date'] <= today.isoformat()][-60:]
    valid = [row for row in rows if row['10y'] is not None and math.isfinite(row['10y'])]
    as_of = valid[-1]['date'] if valid else '—'
    status = 'SOURCE ERROR' if len(valid) < 2 else 'STALE' if (today - datetime.strptime(as_of, '%Y-%m-%d').date()).days > 7 else 'VERIFIED'
    output.insert(3, dict(name='US 10Y', dates=[row['date'] for row in rows], values=[row['10y'] for row in rows], status=status, as_of=as_of, source='U.S. Treasury', unit='%'))
    return output

data['level_history'] = build_level_history(treasury)
if any(item['status'] != 'VERIFIED' for item in data['level_history']):
    data['status'] = 'AUTO · PARTIAL'
    data['risk_note'] += ' 走勢圖：' + '；'.join(item['name'] + ': ' + item['status'] for item in data['level_history'] if item['status'] != 'VERIFIED')
data['sources'].append(dict(name='CHRIS Chart History', ok=all(item['status']=='VERIFIED' for item in data['level_history']), note='Observed daily closes / official yields · no interpolation'))
overall_status = data['status']

# MOVE: observed public delayed data; not an official ICE API.
from html.parser import HTMLParser

MOVE_URL = 'https://www.investing.com/indices/ice-bofaml-move-historical-data'
MOVE_SOURCE = 'Investing.com · ICE BofAML MOVE · delayed public data'

class MoveTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = self.row = self.cell = None
        self.heading = None
        self.headings = []

    def handle_starttag(self, tag, attrs):
        if tag == 'h1':
            self.heading = []
        if tag == 'table':
            self.table = []
        elif tag == 'tr' and self.table is not None:
            self.row = []
        elif tag in ('th', 'td') and self.row is not None:
            self.cell = []

    def handle_data(self, text):
        if self.heading is not None:
            self.heading.append(text)
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if tag == 'h1' and self.heading is not None:
            self.headings.append(' '.join(''.join(self.heading).split()))
            self.heading = None
        if tag in ('th', 'td') and self.cell is not None:
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == 'table' and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def parse_move_html(text, today):
    parser = MoveTableParser()
    parser.feed(text)
    if not any(title.startswith('ICE BofAML MOVE') for title in parser.headings):
        raise RuntimeError('MOVE instrument identity not confirmed')
    tables = [table for table in parser.tables if table and table[0] == ['Date', 'Price', 'Open', 'High', 'Low', 'Vol.', 'Change %']]
    if len(tables) != 1:
        raise RuntimeError('MOVE historical table missing or ambiguous')
    observations = {}
    for cells in tables[0][1:]:
        if len(cells) != 7:
            raise RuntimeError('MOVE row format changed')
        day = datetime.strptime(cells[0], '%b %d, %Y').date()
        value = float(cells[1].replace(',', ''))
        change = float(cells[6].replace('%', '').replace(',', ''))
        if day > today or not math.isfinite(value) or value <= 0 or not math.isfinite(change):
            raise RuntimeError('Invalid MOVE observation')
        observation = {'date': day.isoformat(), 'value': value, 'change_pct': change}
        if day.isoformat() in observations and observations[day.isoformat()] != observation:
            raise RuntimeError('Conflicting MOVE observations')
        observations[day.isoformat()] = observation
    rows = sorted(observations.values(), key=lambda row: row['date'])
    if len(rows) < 2:
        raise RuntimeError('MOVE history insufficient')
    latest, previous = rows[-1], rows[-2]
    # Validate the published change; never replace it with a computed number.
    calculated = (latest['value'] / previous['value'] - 1) * 100
    if abs(calculated - latest['change_pct']) > 0.02:
        raise RuntimeError('MOVE close and published change disagree')
    return rows


def update_move(data):
    today = datetime.now(ZoneInfo('America/New_York')).date()
    item = {'name': 'MOVE', 'value': '—', 'change': 'SOURCE ERROR', 'tone': 'warn', 'status': 'SOURCE ERROR', 'as_of': '—', 'source': MOVE_SOURCE, 'source_url': MOVE_URL, 'data_type': 'DELAYED'}
    rows = []
    error = None
    try:
        rows = parse_move_html(get(MOVE_URL), today)
        latest = rows[-1]
        age = (today - datetime.strptime(latest['date'], '%Y-%m-%d').date()).days
        status = 'STALE' if age > 7 else 'VERIFIED'
        change = latest['change_pct']
        item.update(value=f"{latest['value']:.2f}", change=f'{change:+.2f}%' if status == 'VERIFIED' else 'STALE', tone=('up' if change > 0 else 'down' if change < 0 else '') if status == 'VERIFIED' else 'warn', status=status, as_of=latest['date'], change_pct=change)
    except Exception as exc:
        error = str(exc)
        print(f'[MOVE ERROR] {error}')
    data['tickers'] = [item if row['name'] == 'MOVE' else row for row in data['tickers']]
    data['risk'] = [dict(name='MOVE', value=item['value'], label=item['as_of'] + ' · DELAYED' if item['status'] == 'VERIFIED' else item['status'], tone=item['tone'], status=item['status'], as_of=item['as_of'], source=MOVE_SOURCE) if row['name'] == 'MOVE' else row for row in data['risk']]
    data['vol_signals'] = [[name, item['value'] + ' · ' + item['as_of'] + ' · DELAYED' if item['status'] == 'VERIFIED' else item['status']] if name == 'MOVE' else [name, value] for name, value in data['vol_signals']]
    data['sources'] = [dict(name='MOVE', ok=item['status'] == 'VERIFIED', note=MOVE_SOURCE + ' · ' + item['status'] + ' · ' + item['as_of'], url=MOVE_URL) if row['name'] == 'MOVE' else row for row in data['sources']]
    data['move_history'] = {'dates': [row['date'] for row in rows], 'values': [row['value'] for row in rows], 'status': item['status'], 'as_of': item['as_of'], 'source': MOVE_SOURCE, 'source_url': MOVE_URL, 'error': error}
    data['risk_note'] = data['risk_note'].replace('MOVE 尚未接入授權來源，因此不估算。', 'MOVE 使用 Investing.com 公開延遲歷史行情，非官方 ICE API。').replace('MOVE: NO FEED', '')
    if item['status'] != 'VERIFIED':
        data['status'] = 'AUTO · PARTIAL'
        data['risk_note'] += ' MOVE: ' + item['status']
    return data


data = update_move(data)
overall_status = data['status']

# Expanded blocks: only sourced observations and explicitly derived statistics.
import re
import html as html_module
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor

TODAY_NY = datetime.now(ZoneInfo('America/New_York')).date()

def source_error(name, source, status='SOURCE ERROR'):
    return dict(name=name,value='—',change=status,tone='warn',status=status,as_of='—',source=source)

def quote_health(item):
    if item['status'] == 'VERIFIED':
        try:
            age=(TODAY_NY-datetime.strptime(item['as_of'],'%Y-%m-%d').date()).days
            if age<0: raise ValueError('Future observation')
            if age>7:item.update(status='STALE',change='STALE',tone='warn')
        except Exception:item.update(status='SOURCE ERROR',change='SOURCE ERROR',tone='warn')
    return item

extra_symbols=[('Dow Jones','^DJI'),('Russell 2000','^RUT'),('台灣加權','^TWII'),('日經225','^N225'),('韓國KOSPI','^KS11'),('香港恆生','^HSI'),('新加坡海峽','^STI'),('澳洲ASX 200','^AXJO'),('STOXX Europe 600','^STOXX'),('德國DAX','^GDAXI'),('法國CAC 40','^FCHI'),('英國FTSE 100','^FTSE'),('EUR/USD','EURUSD=X'),('USD/JPY','JPY=X'),('AUD/USD','AUDUSD=X'),('Brent 期貨','BZ=F'),('白銀期貨','SI=F'),('黃金現貨','XAUUSD=X'),('白銀現貨','XAGUSD=X')]
with ThreadPoolExecutor(max_workers=4) as pool:
    extra=dict(zip([name for name,_ in extra_symbols],pool.map(lambda pair:quote_health(market_item(*pair)),extra_symbols)))
all_quotes={q['name']:q for q in data['tickers']}
all_quotes.update(extra)
data['tickers'].extend([extra['Dow Jones'],extra['Russell 2000'],extra['Brent 期貨']])
data['markets']={
    'us':[all_quotes[n] for n in ['S&P 500','NASDAQ','Dow Jones','Russell 2000','SOX']],
    'asia':[extra[n] for n in ['台灣加權','日經225','韓國KOSPI','香港恆生','新加坡海峽','澳洲ASX 200']],
    'europe':[extra[n] for n in ['STOXX Europe 600','德國DAX','法國CAC 40','英國FTSE 100']],
    'fx':[all_quotes['DXY'],extra['EUR/USD'],extra['USD/JPY'],extra['AUD/USD'],all_quotes['USD/TWD']],
    'energy':[all_quotes['WTI'],extra['Brent 期貨']],
    'metals':[all_quotes['GOLD'],extra['白銀期貨'],extra['黃金現貨'],extra['白銀現貨']],
}
data['markets']['europe_rates']=[source_error('德國10Y', 'No connected official European yield feed','NO FEED'),source_error('法德10Y利差','Requires same-date official French/German yields','NO FEED')]
data['sector_errors']=[dict(name=name,status='SOURCE ERROR') for name,_ in sector_symbols if name not in {s['name'] for s in data['sectors']}]
for name in [x['name'] for x in data['sector_errors']]:
    data['sectors'].append(dict(name=name,change=None,status='SOURCE ERROR',as_of='—',source='Yahoo Finance sector ETF proxy'))
for item in data['sectors']:
    item['source']='Yahoo Finance · SPDR sector ETF proxy (not GICS index return)'
data['sectors'].sort(key=lambda x:x['change'] if isinstance(x.get('change'),(int,float)) else -float('inf'),reverse=True)

# Headlines are publisher headlines, not generated news stories.
def rss_feed(name,url):
    result=dict(source=name,source_url=url,status='SOURCE ERROR',items=[])
    try:
        root=ET.fromstring(get(url))
        for entry in root.findall('.//item'):
            title=entry.findtext('title')
            link=entry.findtext('link')
            when=entry.findtext('pubDate')
            if not title or not link or not when:continue
            published=parsedate_to_datetime(when)
            if published.tzinfo is None:raise ValueError('Missing news timezone')
            if published>datetime.now(timezone.utc):continue
            age=(datetime.now(timezone.utc)-published).total_seconds()/86400
            if age>14:continue
            result['items'].append(dict(title=html_module.unescape(title),url=link,as_of=published.astimezone(TAIPEI).strftime('%Y-%m-%d %H:%M'),source=name,status='VERIFIED' if age<=7 else 'STALE'))
        result['items'].sort(key=lambda x:x['as_of'],reverse=True)
        result['items']=result['items'][:10]
        result['status']='VERIFIED' if any(x['status']=='VERIFIED' for x in result['items']) else 'STALE' if result['items'] else 'SOURCE ERROR'
    except Exception as e:result['error']=str(e);print('[NEWS ERROR]',name,e)
    return result
feeds=[('Federal Reserve','https://www.federalreserve.gov/feeds/press_all.xml'),('ECB','https://www.ecb.europa.eu/rss/press.html'),('Yahoo Asia','https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EN225,%5ETWII,%5EHSI&region=US&lang=en-US'),('EIA','https://www.eia.gov/rss/todayinenergy.xml'),('Yahoo Markets','https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,NVDA,TSLA,AAPL&region=US&lang=en-US')]
with ThreadPoolExecutor(max_workers=4) as pool:news_feeds=list(pool.map(lambda pair:rss_feed(*pair),feeds))
by_feed={f['source']:f for f in news_feeds}
regions=[('美國','Federal Reserve'),('歐洲','ECB'),('亞洲','Yahoo Asia'),('全球／能源','EIA')]
data['global_focus']=[dict(region=region,**by_feed[name]) for region,name in regions]
middle_items=[x for f in news_feeds for x in f['items'] if re.search(r'\b(Iran|Israel|Middle East|Saudi|OPEC|Gulf)\b',x['title'],re.I)]
data['global_focus'].insert(3,dict(region='中東',source='Publisher RSS keyword filter',source_url='https://www.eia.gov/todayinenergy/',status='VERIFIED' if middle_items else 'NO FEED',items=middle_items[:3]))
NEWS_PROVIDERS = [('Reuters', 'reuters.com'), ('Bloomberg', 'bloomberg.com'), ('Yahoo Finance', 'finance.yahoo.com')]
def publisher_news(name, domain):
    query = 'site:'+domain+' ("Wall Street" OR "US stocks" OR "U.S. stocks" OR Nasdaq OR "S&P 500") when:7d'
    url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode(dict(q=query,hl='en-US',gl='US',ceid='US:en'))
    result=dict(source=name+' via Google News RSS',source_url=url,status='SOURCE ERROR',items=[])
    try:
        root=ET.fromstring(get(url)); seen=set()
        for entry in root.findall('.//item'):
            try:
                publisher=entry.find('source')
                if publisher is None or (publisher.text or '').strip()!=name:continue
                host=urllib.parse.urlparse(publisher.get('url','')).hostname or ''
                if host not in (domain,'www.'+domain):continue
                title=html_module.unescape(entry.findtext('title') or '').removesuffix(' - '+name).strip()
                if not re.search(r'\b(stocks?|equities|Nasdaq|earnings|Wall (?:Street|St)|Dow|S&P|NYSE|Nvidia|Apple|Microsoft|Tesla|Amazon|Alphabet|Meta)\b',title,re.I):continue
                link=entry.findtext('link') or ''; published=parsedate_to_datetime(entry.findtext('pubDate') or '')
                if not title or title in seen or urllib.parse.urlparse(link).hostname!='news.google.com' or published.tzinfo is None:continue
                age=(datetime.now(timezone.utc)-published).total_seconds()/86400
                if not 0<=age<=7:continue
                seen.add(title)
                result['items'].append(dict(title=title,url=link,as_of=published.astimezone(TAIPEI).strftime('%Y-%m-%d %H:%M'),published_at=published.isoformat(),source=name,publisher_url=publisher.get('url'),delivery='Google News RSS',status='VERIFIED'))
            except (ValueError,TypeError,OverflowError):continue
        result['items'].sort(key=lambda x:x['published_at'],reverse=True)
        result['items']=result['items'][:5]
        result['status']='VERIFIED' if result['items'] else 'NO FEED'
    except Exception as e:result['error']=str(e);print('[NEWS ERROR]',name,e)
    return result

def market_news():
    attempts=[]
    for name,domain in NEWS_PROVIDERS:
        result=publisher_news(name,domain)
        attempts.append(dict(source=name,status=result['status'],count=len(result['items']),source_url=result['source_url'],error=result.get('error')))
        if result['status']=='VERIFIED' and result['items']:
            result['attempts']=attempts
            result['fallback_used']=len(attempts)>1
            return result
    return dict(source='Reuters → Bloomberg → Yahoo Finance',source_url=attempts[-1]['source_url'],status='SOURCE ERROR',items=[],attempts=attempts,fallback_used=True,error='No verified recent headlines from any provider')

data['headlines']=market_news()
data['news']=[dict(title=n['title'],summary=n['as_of']+' · '+n['source']+' via Google News RSS',url=n['url']) for n in data['headlines']['items']]
for attempt in data['headlines']['attempts']:
    data['sources'].append(dict(name='US news · '+attempt['source'],ok=attempt['status']=='VERIFIED',note=attempt['status'],url=attempt['source_url']))

# CNN public data, with no reconstruction from other indicators.
cnn=dict(name='CNN Fear & Greed',value=None,rating=None,status='SOURCE ERROR',as_of='—',source='CNN Business',url='https://www.cnn.com/markets/fear-and-greed')
try:
    p=json.loads(get('https://production.dataviz.cnn.io/index/fearandgreed/graphdata'))['fear_and_greed']
    value=float(p['score']); stamp=datetime.fromisoformat(p['timestamp'].replace('Z','+00:00'))
    age=(datetime.now(timezone.utc)-stamp.astimezone(timezone.utc)).total_seconds()/86400
    if not math.isfinite(value) or not 0<=value<=100 or age<0:raise ValueError('Invalid CNN record')
    cnn.update(value=value,rating=str(p.get('rating','')),as_of=stamp.date().isoformat(),status='VERIFIED' if age<=7 else 'STALE')
except Exception as e:cnn['error']=str(e);print('[CNN ERROR]',e)
data['fear_greed']=cnn

# NY Fed publishes observed target bounds with the EFFR observation.
data['fed_policy']=dict(status='SOURCE ERROR',source='New York Fed',url='https://markets.newyorkfed.org/api/rates/all/latest.json')
try:
    p=json.loads(get(data['fed_policy']['url']))
    row=next(r for r in p['refRates'] if r.get('type')=='EFFR')
    lo,hi=float(row['targetRateFrom']),float(row['targetRateTo'])
    as_of=row['effectiveDate'];age=(TODAY_NY-datetime.strptime(as_of,'%Y-%m-%d').date()).days
    if not all(math.isfinite(v) for v in [lo,hi]) or lo>hi or age<0:raise ValueError('Invalid target bounds')
    data['fed_policy'].update(lower=lo,upper=hi,as_of=as_of,status='VERIFIED' if age<=7 else 'STALE')
except Exception as e:data['fed_policy']['error']=str(e)
data['fedwatch']=dict(status='NO FEED',value=None,source='CME FedWatch',url='https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html',note='No connected verified probability feed; never infer CME probabilities from prices or news.')

# Official FOMC calendar: parse future meeting dates from the published page.
data['fomc']=dict(status='SOURCE ERROR',meetings=[],source='Federal Reserve',url='https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm')
try:
    text=get(data['fomc']['url'])
    for year in [TODAY_NY.year,TODAY_NY.year+1]:
        match=re.search(str(year)+r' FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|\Z)',text,re.S)
        if not match:continue
        pairs=re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)',match.group(1),re.S)
        for month,days in pairs:
            numbers=re.findall(r'\d+',days)
            if not numbers:continue
            end=datetime.strptime(f'{year} {month.strip()} {numbers[-1]}','%Y %B %d').date()
            start=datetime.strptime(f'{year} {month.strip()} {numbers[0]}','%Y %B %d').date()
            if end>=TODAY_NY:data['fomc']['meetings'].append(dict(start=start.isoformat(),end=end.isoformat(),event='FOMC meeting',source='Federal Reserve',status='VERIFIED'))
    if not data['fomc']['meetings']:raise ValueError('No future official meeting dates parsed')
    data['fomc']['status']='VERIFIED'
except Exception as e:data['fomc']['error']=str(e)

# Official BLS release schedule in iCalendar, translated to Taipei time.
data['economic_calendar']=dict(status='SOURCE ERROR',events=[],source='BLS',url='https://www.bls.gov/schedule/news_release/bls.ics')
try:
    text=re.sub(r'\r?\n[ \t]','',get(data['economic_calendar']['url']))
    for block in text.split('BEGIN:VEVENT')[1:]:
        block=block.split('END:VEVENT')[0]
        dt=re.search(r'DTSTART([^:]*):([^\r\n]+)',block)
        title=re.search(r'SUMMARY:([^\r\n]+)',block)
        if not dt or not title:continue
        raw=dt.group(2).strip()
        if len(raw.rstrip('Z'))!=15:continue
        stamp=datetime.strptime(raw.rstrip('Z'),'%Y%m%dT%H%M%S')
        if raw.endswith('Z'):stamp=stamp.replace(tzinfo=timezone.utc)
        elif 'TZID=America/New_York' in dt.group(1) or 'TZID=US/Eastern' in dt.group(1):stamp=stamp.replace(tzinfo=ZoneInfo('America/New_York'))
        else:continue
        tw=stamp.astimezone(TAIPEI)
        if datetime.now(TAIPEI).date()<=tw.date()<=datetime.now(TAIPEI).date()+timedelta(days=45):
            data['economic_calendar']['events'].append(dict(time=tw.strftime('%Y-%m-%d %H:%M'),country='US',event=title.group(1).replace('\\,',','),actual=None,forecast=None,previous=None,status='VERIFIED',source='BLS'))
    data['economic_calendar']['events'].sort(key=lambda x:x['time'])
    if not data['economic_calendar']['events']:raise ValueError('No upcoming official BLS events')
    data['economic_calendar']['status']='VERIFIED'
except Exception as e:data['economic_calendar']['error']=str(e)

# BLS observed monthly releases. These are series values, not forecasts.
macro_series=[('非農就業人數','CES0000000001','千人'),('失業率','LNS14000000','%'),('平均時薪','CES0500000003','USD'),('CPI 指數','CUSR0000SA0','1982–84=100')]
def macro_record(spec):
    name,series,unit=spec
    result=dict(name=name,series=series,unit=unit,status='SOURCE ERROR',value=None,previous=None,forecast=None,as_of='—',source='BLS',url='https://api.bls.gov/publicAPI/v2/timeseries/data/'+series)
    try:
        p=json.loads(get(result['url']))
        if p.get('status')!='REQUEST_SUCCEEDED':raise ValueError(str(p.get('message')))
        rows=[r for r in p['Results']['series'][0]['data'] if re.fullmatch(r'M(0[1-9]|1[0-2])',r['period'])]
        rows.sort(key=lambda r:(r['year'],r['period']),reverse=True)
        if len(rows)<2:raise ValueError('Monthly observations insufficient')
        latest,prior=rows[:2];v,pv=float(latest['value']),float(prior['value'])
        period=latest['year']+'-'+latest['period'][1:]
        if not all(math.isfinite(n) for n in [v,pv]):raise ValueError('Non-finite BLS observation')
        month_start=datetime.strptime(period+'-01','%Y-%m-%d').date()
        if month_start>TODAY_NY:raise ValueError('Future BLS period')
        result.update(value=v,previous=pv,as_of=period,status='STALE' if (TODAY_NY-month_start).days>75 else 'VERIFIED',footnotes=latest.get('footnotes',[]))
    except Exception as e:result['error']=str(e)
    return result
with ThreadPoolExecutor(max_workers=2) as pool:data['economic_actuals']=list(pool.map(macro_record,macro_series))

# Observed ranges are calculations, not invented support/resistance.
def history_range(item):
    vals=[v for v in item['values'][-20:] if isinstance(v,(int,float)) and math.isfinite(v)]
    return dict(name=item['name'],low=min(vals) if vals else None,high=max(vals) if vals else None,as_of=item['as_of'],status=item['status'],method='Last 20 available observations: observed min/max; not support/resistance',source=item['source'],derived=True,unit=item.get('unit',''))
data['key_levels']=[history_range(item) for item in data.get('level_history',[])]
data['core_observations']=[s['text'] for s in data['analysis']['signals']]
data['sources'].extend([dict(name='Global news · '+f['source'],ok=f['status']=='VERIFIED',note=f['status'],url=f['source_url']) for f in news_feeds])
for name,obj in [('CNN Fear & Greed',cnn),('BLS Calendar',data['economic_calendar']),('FOMC Calendar',data['fomc']),('CME FedWatch',data['fedwatch'])]:
    data['sources'].append(dict(name=name,ok=obj['status']=='VERIFIED',note=obj['status'],url=obj['url']))
data['report_date']=datetime.now(TAIPEI).date().isoformat()
data['schema_version']='3.0'
data['coverage']=dict(status='COMPLETE' if all(q['status']=='VERIFIED' for q in extra.values()) else 'PARTIAL',note='All blocks implemented. Unconnected/failed feeds explicitly labeled; public quotes may include current daily bar.')
# Overall VERIFIED must not conceal missing requested feeds.
if data['headlines']['status']!='VERIFIED' or data['coverage']['status']=='PARTIAL' or any(obj['status']!='VERIFIED' for obj in [cnn,data['economic_calendar'],data['fomc'],data['fedwatch']]):
    data['status']='AUTO · PARTIAL'
overall_status=data['status']


# Reader-facing editorial data: translation uses only sourced headlines.
def translate_headline(text):
    url='https://translate.googleapis.com/translate_a/single?'+urllib.parse.urlencode(dict(client='gtx',sl='en',tl='zh-TW',dt='t',q=text))
    payload=json.loads(get(url))
    translated=''.join(part[0] for part in payload[0] if part and isinstance(part[0],str)).strip()
    if not translated or not re.search(r'[\u3400-\u9fff]',translated):raise ValueError('No Chinese translation returned')
    return translated

def prepare_chinese_news(feed):
    feed['items']=feed.get('items',[])[:5]
    def translate_item(item):
        item=dict(item)
        try:
            item['summary_zh']=translate_headline(item['title'])
            item['translation_status']='OK'
            item['summary_method']='Translated publisher headline only; not a full-article summary'
        except Exception as e:
            item['summary_zh']=None;item['translation_status']='SOURCE ERROR';item['translation_error']=str(e)
        return item
    with ThreadPoolExecutor(max_workers=3) as pool:feed['items']=list(pool.map(translate_item,feed['items']))
    return feed

WEEKLY_CALENDAR_URL='https://nfs.faireconomy.media/ff_calendar_thisweek.json'
ECONOMIC_TITLES={
'ISM Manufacturing PMI':'ISM 製造業 PMI','ISM Services PMI':'ISM 服務業 PMI',
'Non-Farm Employment Change':'非農就業人數變化','Unemployment Rate':'失業率',
'Average Hourly Earnings m/m':'平均時薪（月增率）','ADP Non-Farm Employment Change':'ADP 非農就業變化',
'ADP Weekly Employment Change':'ADP 每週就業變化','Unemployment Claims':'初領失業救濟金人數',
'CPI m/m':'CPI（月增率）','CPI y/y':'CPI（年增率）','Core CPI m/m':'核心 CPI（月增率）',
'PPI m/m':'PPI（月增率）','Core PPI m/m':'核心 PPI（月增率）',
'Core PCE Price Index m/m':'核心 PCE 物價指數（月增率）',
'Retail Sales m/m':'零售銷售（月增率）','Core Retail Sales m/m':'核心零售銷售（月增率）',
'Advance GDP q/q':'GDP 季增率（初值）','Prelim GDP q/q':'GDP 季增率（修正值）','Final GDP q/q':'GDP 季增率（終值）',
'JOLTS Job Openings':'JOLTS 職缺數','Trade Balance':'貿易收支',
'Prelim UoM Consumer Sentiment':'密西根大學消費者信心（初值）','Revised UoM Consumer Sentiment':'密西根大學消費者信心（修正值）',
'Prelim UoM Inflation Expectations':'密西根大學通膨預期（初值）','Revised UoM Inflation Expectations':'密西根大學通膨預期（修正值）',
'CB Consumer Confidence':'消費者信心指數','Durable Goods Orders m/m':'耐久財訂單（月增率）',
'Core Durable Goods Orders m/m':'核心耐久財訂單（月增率）','Industrial Production m/m':'工業生產（月增率）',
'Existing Home Sales':'成屋銷售','New Home Sales':'新屋銷售','Building Permits':'建築許可','Housing Starts':'新屋開工',
}
def weekly_calendar():
    today=datetime.now(TAIPEI).date();monday=today-timedelta(days=today.weekday());sunday=monday+timedelta(days=6)
    result=dict(status='SOURCE ERROR',events=[],source='Forex Factory / Fair Economy',url=WEEKLY_CALENDAR_URL,week_start=monday.isoformat(),week_end=sunday.isoformat(),timezone='Asia/Taipei')
    try:
        entries=json.loads(get(WEEKLY_CALENDAR_URL))
        if not isinstance(entries,list):raise ValueError('Invalid calendar response')
        seen=set();in_week=False
        for entry in entries:
            try:
                stamp=datetime.fromisoformat(entry['date'])
                if stamp.tzinfo is None:continue
                local=stamp.astimezone(TAIPEI)
                if not monday<=local.date()<=sunday:continue
                in_week=True
                if entry.get('country')!='USD':continue
                title=entry.get('title','')
                if re.search(r'Speaks|Minutes|Auction|Holiday|Meetings',title,re.I):continue
                if entry.get('impact') not in ('High','Medium') and title not in ECONOMIC_TITLES:continue
                label=ECONOMIC_TITLES.get(title)
                if not label:
                    try:label=translate_headline(title)
                    except Exception:label=title
                key=(title,local.strftime('%Y-%m-%d %H:%M'))
                if key in seen:continue
                seen.add(key)
                result['events'].append(dict(time=key[1],country='美國',event=label,original_title=title,actual=entry.get('actual') or None,forecast=entry.get('forecast') or None,previous=entry.get('previous') or None,status='VERIFIED',source=result['source'],impact=entry.get('impact')))
            except (ValueError,TypeError,KeyError):continue
        result['events'].sort(key=lambda x:x['time'])
        result['status']='VERIFIED' if result['events'] else 'NO FEED' if in_week else 'STALE'
    except Exception as e:result['error']=str(e)
    return result

data['headlines']=prepare_chinese_news(data['headlines'])
data['news']=[dict(title=n.get('summary_zh') or '中文翻譯暫時無法取得',summary=n.get('summary_zh') or 'SOURCE ERROR',url=n['url'],source=n['source']) for n in data['headlines']['items']]
data['economic_calendar']=weekly_calendar()
data['calendar']=data['economic_calendar']['events']
data['sources']=[s for s in data['sources'] if s['name']!='BLS Calendar']
data['sources'].append(dict(name='本週經濟行事曆',ok=data['economic_calendar']['status']=='VERIFIED',note=data['economic_calendar']['status'],url=WEEKLY_CALENDAR_URL))
if data['economic_calendar']['status']!='VERIFIED' or any(n.get('translation_status')!='OK' for n in data['headlines']['items']):data['status']='AUTO · PARTIAL'

Path("data").mkdir(exist_ok=True)

Path("data/brief.json").write_text(
    json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ),
    encoding="utf-8",
)

print(json.dumps(
    {
        "status": overall_status,
        "risk_regime": data["risk_regime"],
        "treasury": treasury_ok,
        "cboe_vix": cboe_ok,
        "nyfed_effr": nyfed_ok,
        "market": market_ok,
        "updated_at": now,
    },
    ensure_ascii=False,
    indent=2,
))

# Immutable first snapshot per Taipei report date; latest report stays mutable.
archive = Path("data/history")
archive.mkdir(parents=True, exist_ok=True)
key = data["report_date"]
snapshot = archive / (key + ".json")
if not snapshot.exists():
    snapshot.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
manifest = sorted(p.stem for p in archive.glob("????-??-??.json"))
(archive / "index.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
