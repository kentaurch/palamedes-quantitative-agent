#!/usr/bin/env python3
"""
portfolio_construction.py — Correlation-Based Portfolio Optimizer for Palamedes

Reads historical returns, computes correlation matrix, finds max diversification
allocation using inverse-volatility and minimum-correlation approaches.

Usage:
    python3 portfolio_construction.py --simulate
    python3 portfolio_construction.py --assets btc,eth,sol --json
    python3 portfolio_construction.py --simulate --assets btc,eth,sol,ada,link,doge --json
"""

import argparse
import json
import os
import sys
import time
import functools
import math
import random
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

# ── Cache Decorator ─────────────────────────────────────────────────────────────

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

DEFAULT_RISK_FREE_RATE = 0.05  # 5% annual

# ── Simulated Data Generator ────────────────────────────────────────────────────


def simulate_asset_returns(asset_names, periods=252, seed=42):
    """
    Generate correlated simulated returns for a set of assets.

    Creates a realistic correlation structure:
    - Assets in same "sector" are more correlated
    - BTC and ETH are moderately correlated with everything
    - DeFi tokens are highly correlated with each other
    """
    random.seed(seed)

    n = len(asset_names)
    if n == 0:
        return {}

    # Define sectors for realistic correlation
    sectors = {
        "btc": "store_of_value",
        "bitcoin": "store_of_value",
        "eth": "layer1",
        "ethereum": "layer1",
        "sol": "layer1",
        "solana": "layer1",
        "ada": "layer1",
        "cardano": "layer1",
        "link": "oracle",
        "chainlink": "oracle",
        "uni": "defi",
        "uniswap": "defi",
        "aave": "defi",
        "mkr": "defi",
        "maker": "defi",
        "doge": "meme",
        "dogecoin": "meme",
    }

    # Simulate market factor (common to all)
    market_returns = [random.gauss(0.0003, 0.02) for _ in range(periods)]

    returns = {}
    for asset in asset_names:
        sector = sectors.get(asset.lower(), "other")
        # Sector-specific beta to market
        if sector == "store_of_value":
            beta = 0.7
        elif sector == "layer1":
            beta = 1.0
        elif sector == "defi":
            beta = 1.3
        elif sector == "oracle":
            beta = 1.1
        elif sector == "meme":
            beta = 1.5
        else:
            beta = 1.0

        # Idiosyncratic volatility (lower for larger assets)
        if asset.lower() in ("btc", "bitcoin", "eth", "ethereum"):
            idio_vol = 0.03
        elif asset.lower() in ("sol", "solana", "link", "chainlink"):
            idio_vol = 0.04
        else:
            idio_vol = 0.05

        asset_returns = []
        for m in market_returns:
            idio = random.gauss(0.0002, idio_vol)
            r = beta * m + idio
            asset_returns.append(r)

        returns[asset] = asset_returns

    return returns


# ── Portfolio Optimization ──────────────────────────────────────────────────────


def compute_correlation_matrix(returns_dict):
    """
    Compute correlation matrix from return series.

    Args:
        returns_dict: Dict mapping asset name -> list of daily returns

    Returns:
        (assets, matrix) where matrix[i][j] = correlation between assets i and j
    """
    assets = list(returns_dict.keys())
    n = len(assets)
    if n < 2:
        return assets, [[1.0]]

    # Build return arrays
    matrix = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for j in range(n):
            ri = returns_dict[assets[i]]
            rj = returns_dict[assets[j]]

            if len(ri) != len(rj) or len(ri) < 2:
                matrix[i][j] = 1.0 if i == j else 0.0
                continue

            # Pearson correlation
            mean_i = sum(ri) / len(ri)
            mean_j = sum(rj) / len(rj)

            cov = sum((ri[k] - mean_i) * (rj[k] - mean_j) for k in range(len(ri)))
            var_i = sum((ri[k] - mean_i) ** 2 for k in range(len(ri)))
            var_j = sum((rj[k] - mean_j) ** 2 for k in range(len(rj)))

            denom = (var_i * var_j) ** 0.5
            matrix[i][j] = cov / denom if denom > 0 else 0.0

    return assets, matrix


def compute_volatilities(returns_dict):
    """Compute annualized volatility for each asset."""
    vols = {}
    for asset, rets in returns_dict.items():
        if len(rets) < 2:
            vols[asset] = 0.0
            continue
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
        vol = var_r ** 0.5
        annualized_vol = vol * (252 ** 0.5)
        vols[asset] = round(annualized_vol, 4)
    return vols


def compute_expected_returns(returns_dict):
    """Compute annualized expected returns."""
    exp_rets = {}
    for asset, rets in returns_dict.items():
        if not rets:
            exp_rets[asset] = 0.0
            continue
        mean_daily = sum(rets) / len(rets)
        annualized = (1 + mean_daily) ** 252 - 1
        exp_rets[asset] = round(annualized, 4)
    return exp_rets


def inverse_volatility_allocation(volatilities):
    """
    Allocate weights inversely proportional to volatility.
    Higher volatility -> lower allocation.
    This is a simple risk-parity approach.
    """
    if not volatilities:
        return {}
    total_inv_vol = sum(1.0 / max(v, 0.001) for v in volatilities.values())
    weights = {}
    for asset, vol in volatilities.items():
        weights[asset] = (1.0 / max(vol, 0.001)) / total_inv_vol
    return weights


def minimum_correlation_allocation(assets, corr_matrix):
    """
    Allocate to minimize portfolio correlation / maximize diversification.

    Uses a simplified approach: assets with lower average correlation
    to the rest of the portfolio get higher weights.
    """
    n = len(assets)
    if n < 2:
        return {assets[0]: 1.0} if assets else {}

    # Average correlation of each asset to all others
    avg_correlations = []
    for i in range(n):
        others = [corr_matrix[i][j] for j in range(n) if j != i]
        avg_corr = sum(others) / len(others) if others else 0
        avg_correlations.append(avg_corr)

    # Inverse of avg correlation (lower correlation -> higher weight)
    min_val = min(avg_correlations) if avg_correlations else 1
    # Shift to make all positive
    shifted = [c - min_val + 0.01 for c in avg_correlations]
    inv_corr = [1.0 / max(s, 0.001) for s in shifted]

    total_inv = sum(inv_corr)
    weights = {}
    for i, asset in enumerate(assets):
        weights[asset] = inv_corr[i] / total_inv if total_inv > 0 else 1.0 / n

    return weights


def compute_portfolio_metrics(weights, returns_dict, risk_free=DEFAULT_RISK_FREE_RATE):
    """
    Compute portfolio-level metrics: return, volatility, Sharpe, diversification ratio.
    """
    assets = list(weights.keys())
    if not assets or not returns_dict:
        return {}

    # Check we have returns for all weighted assets
    valid_assets = [a for a in assets if a in returns_dict and len(returns_dict[a]) > 0]
    if not valid_assets:
        return {}

    # Portfolio returns (weighted sum of asset returns)
    n_periods = min(len(returns_dict[a]) for a in valid_assets)
    portfolio_returns = []
    for t in range(n_periods):
        pr = sum(weights[a] * returns_dict[a][t] for a in valid_assets)
        portfolio_returns.append(pr)

    if not portfolio_returns:
        return {}

    # Portfolio daily metrics
    mean_daily = sum(portfolio_returns) / len(portfolio_returns)
    var_daily = sum((r - mean_daily) ** 2 for r in portfolio_returns) / (len(portfolio_returns) - 1)
    vol_daily = var_daily ** 0.5

    # Annualize
    annual_return = (1 + mean_daily) ** 252 - 1
    annual_vol = vol_daily * (252 ** 0.5)
    sharpe = (annual_return - risk_free) / annual_vol if annual_vol > 0 else 0

    # Diversification ratio = weighted avg vol / portfolio vol
    asset_vols = compute_volatilities(returns_dict)
    weighted_avg_vol = sum(weights.get(a, 0) * asset_vols.get(a, 0) for a in valid_assets)
    div_ratio = weighted_avg_vol / annual_vol if annual_vol > 0 else 1.0

    # Compute drawdown
    cum_returns = [1000]
    for r in portfolio_returns:
        cum_returns.append(cum_returns[-1] * (1 + r))

    peak = cum_returns[0]
    max_dd = 0
    for p in cum_returns:
        if p > peak:
            peak = p
        dd = (peak - p) / peak
        if dd > max_dd:
            max_dd = dd

    return {
        "annualized_return": round(annual_return, 4),
        "annualized_volatility": round(annual_vol, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "diversification_ratio": round(div_ratio, 4),
        "daily_metrics": {
            "mean_return": round(mean_daily, 6),
            "volatility": round(vol_daily, 6),
            "positive_days": sum(1 for r in portfolio_returns if r > 0),
            "total_days": len(portfolio_returns),
            "win_rate": round(sum(1 for r in portfolio_returns if r > 0) / len(portfolio_returns), 4),
        },
    }


# ── Report Builder ──────────────────────────────────────────────────────────────


def build_portfolio_report(asset_names=None, simulate=True, json_mode=False):
    """Build a portfolio construction report."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if asset_names is None:
        asset_names = ["BTC", "ETH", "SOL", "LINK", "AAVE", "DOGE"]

    if simulate:
        returns_dict = simulate_asset_returns([a.lower() for a in asset_names], periods=252)
        # Map back to original names
        mapped_returns = {}
        for orig in asset_names:
            mapped_returns[orig] = returns_dict.get(orig.lower(), [])
        returns_dict = mapped_returns
        note = "Using simulated correlated returns for demonstration."
    else:
        # Without real data source, fallback to simulation
        returns_dict = simulate_asset_returns([a.lower() for a in asset_names], periods=252)
        mapped_returns = {}
        for orig in asset_names:
            mapped_returns[orig] = returns_dict.get(orig.lower(), [])
        returns_dict = mapped_returns
        note = "Using simulated returns. Use palamedes-data.py to fetch real price data."

    # Validate we have data
    valid_assets = [a for a in asset_names if len(returns_dict.get(a, [])) > 0]
    if len(valid_assets) < 2:
        return {"_error": "Need at least 2 assets with return data"}

    # Compute correlation
    assets, corr_matrix = compute_correlation_matrix(returns_dict)
    volatilities = compute_volatilities(returns_dict)
    expected_returns = compute_expected_returns(returns_dict)

    # Inverse volatility portfolio
    iv_weights = inverse_volatility_allocation(volatilities)
    iv_metrics = compute_portfolio_metrics(iv_weights, returns_dict)

    # Minimum correlation portfolio
    mc_weights = minimum_correlation_allocation(assets, corr_matrix)
    mc_metrics = compute_portfolio_metrics(mc_weights, returns_dict)

    # Equal weight portfolio (benchmark)
    ew_weights = {a: 1.0 / len(assets) for a in assets}
    ew_metrics = compute_portfolio_metrics(ew_weights, returns_dict)

    report = {
        "timestamp": timestamp,
        "agent": "palamedes-quantitative",
        "tool": "portfolio_construction",
        "assets": asset_names,
        "note": note,
        "correlation_matrix": {
            "assets": assets,
            "matrix": [[round(c, 4) for c in row] for row in corr_matrix],
        },
        "asset_stats": {
            a: {
                "volatility": volatilities.get(a, 0),
                "expected_return": expected_returns.get(a, 0),
            }
            for a in asset_names
        },
        "portfolios": {
            "inverse_volatility": {
                "weights": {a: round(w, 4) for a, w in iv_weights.items()},
                "metrics": iv_metrics,
            },
            "minimum_correlation": {
                "weights": {a: round(w, 4) for a, w in mc_weights.items()},
                "metrics": mc_metrics,
            },
            "equal_weight": {
                "weights": {a: round(w, 4) for a, w in ew_weights.items()},
                "metrics": ew_metrics,
            },
        },
    }

    if json_mode:
        return json.dumps(report, indent=2)

    return _format_human_report(report)


def _format_human_report(r):
    """Format the portfolio report for human reading."""
    lines = []
    lines.append("Palamedes Portfolio Construction Report")
    lines.append(f"Generated: {r.get('timestamp', 'unknown')}")
    lines.append(f"Assets: {', '.join(r.get('assets', []))}")
    lines.append("")

    # Correlation matrix
    corr_data = r.get("correlation_matrix", {})
    assets = corr_data.get("assets", [])
    matrix = corr_data.get("matrix", [])

    lines.append("── Correlation Matrix ──")
    if assets and matrix:
        header = "         " + "  ".join(f"{a:>8s}" for a in assets)
        lines.append(header)
        for i, a in enumerate(assets):
            row_vals = "  ".join(f"{matrix[i][j]:>8.4f}" if j <= i else "         " for j in range(len(assets)))
            lines.append(f"  {a:6s}  {row_vals}")
    lines.append("")

    # Asset stats
    lines.append("── Asset Statistics (Annualized) ──")
    stats = r.get("asset_stats", {})
    lines.append(f"  {'Asset':10s} {'Volatility':>12s} {'Exp Return':>12s}")
    for a in r.get("assets", []):
        s = stats.get(a, {})
        lines.append(f"  {a:10s} {s.get('volatility', 0):>10.2%}  {s.get('expected_return', 0):>10.2%}")
    lines.append("")

    # Portfolio comparison
    lines.append("── Portfolio Comparison ──")
    portfolios = r.get("portfolios", {})

    for pname, pdata in portfolios.items():
        metrics = pdata.get("metrics", {})
        weights = pdata.get("weights", {})

        display_name = pname.replace("_", " ").title()
        lines.append(f"  [{display_name}]")
        for a, w in sorted(weights.items(), key=lambda x: -x[1]):
            lines.append(f"    {a:10s} {w:>7.1%}")
        lines.append(f"    {'Expected Return':20s}: {metrics.get('annualized_return', 0):>7.2%}")
        lines.append(f"    {'Volatility':20s}: {metrics.get('annualized_volatility', 0):>7.2%}")
        lines.append(f"    {'Sharpe Ratio':20s}: {metrics.get('sharpe_ratio', 0):>7.2f}")
        lines.append(f"    {'Max Drawdown':20s}: {metrics.get('max_drawdown', 0):>7.2%}")
        lines.append(f"    {'Diversification':20s}: {metrics.get('diversification_ratio', 0):>7.2f}x")
        win_rate = metrics.get("daily_metrics", {}).get("win_rate", 0)
        lines.append(f"    {'Win Rate':20s}: {win_rate:>7.1%}")
        lines.append("")

    lines.append(f"Note: {r.get('note', '')}")

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Palamedes — Portfolio Construction & Optimization v3.0",
    )
    parser.add_argument("--assets", "-a", default="BTC,ETH,SOL,LINK,AAVE,DOGE",
                        help="Comma-separated asset list (default: BTC,ETH,SOL,LINK,AAVE,DOGE)")
    parser.add_argument("--simulate", action="store_true", default=True,
                        help="Use simulated returns (default: True)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    asset_list = [a.strip() for a in args.assets.split(",")]
    report = build_portfolio_report(asset_names=asset_list, simulate=args.simulate, json_mode=args.json)
    print(report)


if __name__ == "__main__":
    main()
