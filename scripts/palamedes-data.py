#!/usr/bin/env python3
"""
palamedes-data.py — Quantitative Data Fetcher for Palamedes Quantitative Analysis

Fetches market data for quantitative modeling — price history, volatility,
correlation data, and risk metrics from public APIs.

Usage:
    python3 palamedes-data.py --coin bitcoin
    python3 palamedes-data.py --coin eth,sol --json
    python3 palamedes-data.py --list-coins
    python3 palamedes-data.py --coin btc --days 365 --json

Dependencies: urllib (stdlib). Optional: requests, numpy.
"""

import argparse
import json
import os
import sys
import time
import functools
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Retry Decorator ─────────────────────────────────────────────────────────────

def retry(max_attempts=3, delay=2):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    print(f'  [retry] Attempt {attempt+1} failed: {e}. Retrying in {delay}s...', file=sys.stderr)
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

# ── Cache Decorator (file-based with TTL) ──────────────────────────────────────

CACHE_DIR = os.path.expanduser('~/.cache/telos-agents')
os.makedirs(CACHE_DIR, exist_ok=True)

def cached(ttl_seconds=300):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f'{func.__name__}_{hash(str(args) + str(sorted(kwargs.items())))}'
            cache_path = os.path.join(CACHE_DIR, f'{cache_key}.json')
            if os.path.exists(cache_path):
                age = time.time() - os.path.getmtime(cache_path)
                if age < ttl_seconds:
                    with open(cache_path) as f:
                        return json.load(f)
            result = func(*args, **kwargs)
            if result is not None:
                with open(cache_path, 'w') as f:
                    json.dump(result, f)
            return result
        return wrapper
    return decorator

# ── Configuration ───────────────────────────────────────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

COIN_IDS = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "cardano": "cardano", "ada": "cardano",
    "ripple": "ripple", "xrp": "ripple",
    "polkadot": "polkadot", "dot": "polkadot",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "chainlink": "chainlink", "link": "chainlink",
    "polygon": "matic-network", "matic": "matic-network",
    "arbitrum": "arbitrum", "arb": "arbitrum",
    "optimism": "optimism", "op": "optimism",
    "sui": "sui",
    "aptos": "aptos", "apt": "aptos",
    "near": "near",
    "injective": "injective-protocol", "inj": "injective-protocol",
    "render": "render-token", "rndr": "render-token",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "litecoin": "litecoin", "ltc": "litecoin",
    "uni": "uniswap", "uniswap": "uniswap",
    "link": "chainlink",
    "aave": "aave",
    "maker": "makerdao", "mkr": "makerdao",
}

# ── Helpers ─────────────────────────────────────────────────────────────────────


def _fetch(url, timeout=15):
    """Fetch JSON from a URL. Returns dict or None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Palamedes/3.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"_error": str(e)}


def _fmt(val, suffix=""):
    """Format a number with commas, optional suffix."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if v >= 1_000_000_000:
            return f"${v / 1_000_000_000:,.2f}B{suffix}"
        if v >= 1_000_000:
            return f"${v / 1_000_000:,.2f}M{suffix}"
        if v >= 1_000:
            return f"${v:,.0f}{suffix}"
        return f"{v:,.4f}"
    except (ValueError, TypeError):
        return str(val)


def _pct(val):
    """Format a percentage."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.2f}%"
    except (ValueError, TypeError):
        return str(val)


def _staleness_warning(timestamp_str, max_age_minutes=60):
    """Print a warning if data is older than max_age_minutes."""
    if not timestamp_str:
        return
    try:
        if isinstance(timestamp_str, (int, float)):
            ts = datetime.fromtimestamp(timestamp_str, tz=timezone.utc)
        else:
            ts = datetime.fromisoformat(str(timestamp_str).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        if age > max_age_minutes:
            print(f"  [stale] Data is {age:.0f} minutes old (threshold: {max_age_minutes}m)", file=sys.stderr)
    except Exception:
        pass


# ── Data Fetchers ───────────────────────────────────────────────────────────────


@retry(max_attempts=2, delay=2)
@cached(ttl_seconds=180)
def coin_price_history(coin_id, days=90, vs_currency="usd"):
    """Fetch historical price data for quantitative analysis."""
    url = f"{COINGECKO_BASE}/coins/{coin_id}/market_chart?vs_currency={vs_currency}&days={days}"
    data = _fetch(url)
    if not data or "prices" not in data:
        return None

    prices = data.get("prices", [])
    market_caps = data.get("market_caps", [])
    total_volumes = data.get("total_volumes", [])

    if not prices:
        return None

    # Extract OHLC-like data from price points
    price_values = [p[1] for p in prices]
    returns = []
    for i in range(1, len(price_values)):
        if price_values[i-1] > 0:
            returns.append((price_values[i] - price_values[i-1]) / price_values[i-1])

    return {
        "coin_id": coin_id,
        "days": days,
        "data_points": len(prices),
        "current_price": prices[-1][1],
        "open_price": prices[0][1],
        "high_90d": max(price_values),
        "low_90d": min(price_values),
        "price_change_pct": ((prices[-1][1] - prices[0][1]) / prices[0][1] * 100) if prices[0][1] > 0 else 0,
        "volatility_daily": _compute_volatility(returns),
        "mean_daily_return": sum(returns) / len(returns) if returns else 0,
        "max_daily_return": max(returns) if returns else 0,
        "min_daily_return": min(returns) if returns else 0,
        "latest_market_cap": market_caps[-1][1] if market_caps else None,
        "latest_volume": total_volumes[-1][1] if total_volumes else None,
    }


@cached(ttl_seconds=600)
def coin_ohlc(coin_id, days=30):
    """Fetch OHLC (candlestick) data for technical quant analysis."""
    url = f"{COINGECKO_BASE}/coins/{coin_id}/ohlc?vs_currency=usd&days={days}"
    data = _fetch(url)
    if not data or not isinstance(data, list):
        return None

    candles = []
    for c in data:
        if len(c) >= 5:
            candles.append({
                "timestamp": c[0],
                "open": c[1],
                "high": c[2],
                "low": c[3],
                "close": c[4],
            })

    return {
        "coin_id": coin_id,
        "days": days,
        "candles_count": len(candles),
        "candles": candles,
    }


@cached(ttl_seconds=1800)
def global_market_data():
    """Fetch global crypto market metrics."""
    data = _fetch(f"{COINGECKO_BASE}/global")
    if not data or "data" not in data:
        return None
    d = data["data"]
    return {
        "total_market_cap_usd": d.get("total_market_cap", {}).get("usd"),
        "total_volume_24h_usd": d.get("total_volume", {}).get("usd"),
        "btc_dominance": d.get("market_cap_percentage", {}).get("btc"),
        "eth_dominance": d.get("market_cap_percentage", {}).get("eth"),
        "market_cap_change_24h": d.get("market_cap_change_percentage_24h_usd"),
        "active_cryptos": d.get("active_cryptocurrencies"),
        "total_markets": d.get("markets"),
    }


@cached(ttl_seconds=600)
def defillama_protocols():
    """Fetch top DeFi protocols by TVL for correlation context."""
    data = _fetch("https://api.llama.fi/protocols")
    if not data or not isinstance(data, list):
        return None
    top = sorted(data, key=lambda x: x.get("tvl", 0), reverse=True)[:20]
    return [
        {
            "name": p.get("name"),
            "symbol": p.get("symbol"),
            "tvl": p.get("tvl"),
            "chain": p.get("chain"),
            "change_1d": p.get("change_1d"),
            "change_7d": p.get("change_7d"),
        }
        for p in top
    ]


@cached(ttl_seconds=600)
def fear_greed_index():
    """Fetch Fear & Greed Index as a quantifiable sentiment factor."""
    data = _fetch("https://api.alternative.me/fng/?limit=30")
    if not data or "data" not in data:
        return None
    values = [int(v.get("value", 50)) for v in data["data"]]
    return {
        "current_value": values[0] if values else 50,
        "current_classification": data["data"][0].get("value_classification", "Neutral") if data["data"] else "Neutral",
        "mean_30d": sum(values) / len(values) if values else 50,
        "min_30d": min(values) if values else 0,
        "max_30d": max(values) if values else 100,
        "std_30d": (sum((v - sum(values)/len(values))**2 for v in values) / len(values)) ** 0.5 if values else 0,
        "values": values,
    }


def _compute_volatility(returns):
    """Compute daily volatility (standard deviation of returns)."""
    if not returns or len(returns) < 2:
        return 0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return variance ** 0.5


# ── Stale Data Detection ────────────────────────────────────────────────────────


def check_data_freshness(report, max_age_minutes=60):
    """Check for stale data in report."""
    stale_sections = []
    now = datetime.now(timezone.utc)

    for key, value in report.items():
        if isinstance(value, dict):
            ts = value.get("timestamp")
            if ts:
                try:
                    if isinstance(ts, (int, float)):
                        age = (now - datetime.fromtimestamp(ts, tz=timezone.utc)).total_seconds() / 60
                    else:
                        age = (now - datetime.fromisoformat(str(ts).replace("Z", "+00:00"))).total_seconds() / 60
                    if age > max_age_minutes:
                        stale_sections.append(key)
                except Exception:
                    pass

    if stale_sections:
        print(f"  [stale] Data freshness warning: {', '.join(stale_sections)} data is >{max_age_minutes}m old", file=sys.stderr)
    return stale_sections


# ── Report Builder ──────────────────────────────────────────────────────────────


def build_report(coins_str, days=90, json_mode=False):
    """Build a comprehensive quantitative data report."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    coins = [c.strip() for c in coins_str.split(",")]
    coin_ids = [COIN_IDS.get(c.lower(), c.lower()) for c in coins]

    report = {
        "timestamp": timestamp,
        "agent": "palamedes-quantitative",
        "coins": coins,
        "coin_ids": coin_ids,
    }

    # Global market data
    try:
        report["global_market"] = global_market_data()
    except Exception as e:
        report["global_market"] = {"_error": str(e)}

    # Fear & Greed as quant factor
    try:
        report["fear_greed_factor"] = fear_greed_index()
    except Exception as e:
        report["fear_greed_factor"] = {"_error": str(e)}

    # Per-coin price history
    report["price_data"] = {}
    for cid in coin_ids:
        try:
            report["price_data"][cid] = coin_price_history(cid, days=days)
        except Exception as e:
            report["price_data"][cid] = {"_error": str(e)}

    # Per-coin OHLC (shorter period)
    report["ohlc_data"] = {}
    for cid in coin_ids:
        try:
            report["ohlc_data"][cid] = coin_ohlc(cid, days=min(days, 30))
        except Exception as e:
            report["ohlc_data"][cid] = {"_error": str(e)}

    # Top DeFi protocols for correlation context
    try:
        report["defi_tvl_top"] = defillama_protocols()
    except Exception as e:
        report["defi_tvl_top"] = {"_error": str(e)}

    # Staleness check
    check_data_freshness(report)

    if json_mode:
        return json.dumps(report, indent=2)

    return _format_human_report(report)


def _format_human_report(r):
    """Format the report for human reading."""
    lines = []
    lines.append(f"Palamedes Quantitative Data Report")
    lines.append(f"Generated: {r['timestamp']}")
    lines.append(f"Coins: {', '.join(r['coins'])}")
    lines.append("")

    # Global market
    gl = r.get("global_market", {})
    lines.append("── Global Market ──")
    if gl and "_error" not in gl:
        lines.append(f"  Total Market Cap   : {_fmt(gl.get('total_market_cap_usd'))}")
        lines.append(f"  24h Volume         : {_fmt(gl.get('total_volume_24h_usd'))}")
        lines.append(f"  BTC Dominance      : {gl.get('btc_dominance', 'N/A')}%")
        lines.append(f"  ETH Dominance      : {gl.get('eth_dominance', 'N/A')}%")
    else:
        lines.append("  (unavailable)")
    lines.append("")

    # Fear & Greed as factor
    fg = r.get("fear_greed_factor", {})
    lines.append("── Sentiment Factor ──")
    if fg and "_error" not in fg:
        lines.append(f"  Current            : {fg.get('current_value')} — {fg.get('current_classification')}")
        lines.append(f"  30d Mean           : {fg.get('mean_30d', 0):.1f}")
        lines.append(f"  30d Std Dev        : {fg.get('std_30d', 0):.1f}")
        lines.append(f"  30d Range          : {fg.get('min_30d')} — {fg.get('max_30d')}")
    else:
        lines.append("  (unavailable)")
    lines.append("")

    # Per-coin price data
    for cid, pd in r.get("price_data", {}).items():
        lines.append(f"── {cid.upper()} Price History ({pd.get('days', '?')}d) ──")
        if "_error" not in pd:
            lines.append(f"  Current Price      : {_fmt(pd.get('current_price'))}")
            lines.append(f"  Period Open        : {_fmt(pd.get('open_price'))}")
            lines.append(f"  Period Change      : {_pct(pd.get('price_change_pct'))}")
            lines.append(f"  Period High        : {_fmt(pd.get('high_90d'))}")
            lines.append(f"  Period Low         : {_fmt(pd.get('low_90d'))}")
            lines.append(f"  Data Points        : {pd.get('data_points', 0)}")
            lines.append(f"  Daily Volatility   : {_pct(pd.get('volatility_daily', 0))}")
            lines.append(f"  Mean Daily Return  : {_pct(pd.get('mean_daily_return', 0))}")
        else:
            lines.append(f"  {pd.get('_error')}")
        lines.append("")

    # DeFi top protocols
    defi = r.get("defi_tvl_top", [])
    if defi and "_error" not in defi:
        lines.append("── Top DeFi Protocols (TVL) ──")
        for p in defi[:8]:
            lines.append(f"  {p.get('name', '?'):20s} TVL: {_fmt(p.get('tvl')):>12s}  7d: {_pct(p.get('change_7d'))}")
        lines.append("")

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────────


def list_coins():
    """Print all known coin mappings."""
    print("Known Coin ID Mappings:")
    for name, cid in sorted(COIN_IDS.items()):
        print(f"  {name:15s} -> {cid}")
    print()
    print("Comma-separate multiple coins: --coin btc,eth,sol")


def main():
    parser = argparse.ArgumentParser(
        description="Palamedes — Quantitative Data Fetcher v3.0",
    )
    parser.add_argument("--coin", "-c", default="bitcoin", help="Coin name/ID or comma-separated list (default: bitcoin)")
    parser.add_argument("--days", "-d", type=int, default=90, help="Days of history (default: 90)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--list-coins", action="store_true", help="List known coin ID mappings")
    args = parser.parse_args()

    if args.list_coins:
        list_coins()
        return

    report = build_report(coins_str=args.coin, days=args.days, json_mode=args.json)
    print(report)


if __name__ == "__main__":
    main()
