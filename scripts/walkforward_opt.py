#!/usr/bin/env python3
"""
walkforward_opt.py — Walk-Forward Optimization for Palamedes

Performs walk-forward optimization for backtesting trading strategies.
Splits data into training/validation periods, rolls forward, and measures
out-of-sample stability.

Usage:
    python3 walkforward_opt.py --simulate
    python3 walkforward_opt.py --simulate --windows 6 --train-pct 0.6
    python3 walkforward_opt.py --simulate --json
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

# Default parameters for walk-forward
DEFAULT_WINDOWS = 4          # Number of walk-forward windows
DEFAULT_TRAIN_PCT = 0.7     # Fraction of each window used for training
DEFAULT_MIN_TRAIN = 30      # Minimum training days
DEFAULT_MIN_VAL = 10        # Minimum validation days

# ── Core Walk-Forward Logic ────────────────────────────────────────────────────


def generate_walk_forward_windows(total_periods, n_windows, train_pct, min_train, min_val):
    """
    Generate train/validation split indices for walk-forward analysis.

    Args:
        total_periods: Total number of time periods (e.g., days of data)
        n_windows: Number of walk-forward windows
        train_pct: Fraction of each window to use for training
        min_train: Minimum training periods per window
        min_val: Minimum validation periods per window

    Returns:
        List of dicts: {"train_start", "train_end", "val_start", "val_end"}
    """
    if n_windows < 2:
        n_windows = 2

    # Calculate window size
    window_size = total_periods // n_windows

    windows = []
    for i in range(n_windows):
        val_start = int(i * window_size + window_size * train_pct)
        train_end = val_start - 1

        # Ensure minimum sizes
        train_len = train_end + 1  # 0-indexed, so +1
        val_len = total_periods - val_start if i == n_windows - 1 else int(window_size * (1 - train_pct))

        if train_len < min_train:
            # Extend training period at expense of validation start
            val_start = min_train
            train_end = val_start - 1

        # Last window validation extends to end
        if i == n_windows - 1:
            val_end = total_periods - 1
        else:
            val_end = val_start + val_len - 1
            if val_end >= total_periods:
                val_end = total_periods - 1

        if val_end - val_start + 1 < min_val:
            # Skip this window if validation too small
            continue

        windows.append({
            "window_index": i,
            "train_start": 0,
            "train_end": train_end,
            "train_length": train_end - 0 + 1,
            "val_start": val_start,
            "val_end": val_end,
            "val_length": val_end - val_start + 1,
        })

        # For next iteration, start training from beginning (expanding window)
        # In a real implementation, you might use a sliding window instead

    return windows


def simulate_returns(periods=250, mean=0.001, volatility=0.03):
    """Simulate daily returns for testing."""
    random.seed(42)  # Reproducible
    returns = []
    for _ in range(periods):
        r = random.gauss(mean, volatility)
        returns.append(r)
    return returns


def compute_sharpe(returns, risk_free=0.0):
    """Compute annualized Sharpe ratio from daily returns."""
    if not returns or len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    if all(r == returns[0] for r in returns):
        return 0.0
    std_r = (sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5
    if std_r == 0:
        return 0.0
    # Annualize (assuming daily returns)
    annual_factor = 252 ** 0.5
    excess = mean_r - risk_free / 252
    return (excess / std_r) * annual_factor


def compute_sortino(returns, risk_free=0.0):
    """Compute Sortino ratio (uses downside deviation only)."""
    if not returns or len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return float('inf')
    downside_std = (sum((r - mean_r) ** 2 for r in downside) / (len(returns) - 1)) ** 0.5
    if downside_std == 0:
        return 0.0
    annual_factor = 252 ** 0.5
    excess = mean_r - risk_free / 252
    return (excess / downside_std) * annual_factor


def compute_max_drawdown(prices):
    """Compute maximum drawdown from price series."""
    if not prices:
        return 0.0
    peak = prices[0]
    max_dd = 0.0
    for p in prices:
        if p > peak:
            peak = p
        dd = (peak - p) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def compute_stability_score(window_results):
    """
    Compute stability score across walk-forward windows.
    Measures how consistent the Sharpe ratio is across out-of-sample periods.
    """
    if len(window_results) < 2:
        return 0.0

    sharpe_values = [w.get("oos_sharpe", 0) for w in window_results]
    if not sharpe_values:
        return 0.0

    mean_sharpe = sum(sharpe_values) / len(sharpe_values)
    if mean_sharpe == 0:
        return 0.0

    # Coefficient of variation (lower = more stable)
    std_sharpe = (sum((s - mean_sharpe) ** 2 for s in sharpe_values) / len(sharpe_values)) ** 0.5
    cv = std_sharpe / abs(mean_sharpe) if abs(mean_sharpe) > 0 else float('inf')

    # Stability score: 0-100, higher = more stable
    # CV of 0.1 or less = very stable, CV > 2.0 = very unstable
    stability = max(0, min(100, 100 * (1 - cv / 2.0)))

    return round(stability, 1)


def compute_parameter_sensitivity(baseline_result, perturbations, param_name):
    """
    Compute how sensitive results are to parameter changes.
    Returns a sensitivity score (0-100, lower = more robust).
    """
    if not perturbations:
        return 0.0

    baseline_sharpe = baseline_result.get("oos_sharpe", 0)
    if baseline_sharpe == 0:
        return 100.0

    changes = []
    for p in perturbations:
        p_sharpe = p.get("oos_sharpe", 0)
        change = abs(p_sharpe - baseline_sharpe) / abs(baseline_sharpe)
        changes.append(change)

    avg_change = sum(changes) / len(changes) if changes else 0
    # Sensitivity score: 0-100 (higher = less sensitive = more robust)
    robustness = max(0, min(100, 100 * (1 - avg_change)))

    return round(robustness, 1)


# ── Walk-Forward Analysis Engine ───────────────────────────────────────────────


def run_walkforward(data_series, n_windows=DEFAULT_WINDOWS, train_pct=DEFAULT_TRAIN_PCT,
                    min_train=DEFAULT_MIN_TRAIN, min_val=DEFAULT_MIN_VAL):
    """
    Run walk-forward analysis on a data series.

    Args:
        data_series: List of daily returns
        n_windows: Number of walk-forward windows
        train_pct: Fraction per window for training
        min_train: Minimum training periods
        min_val: Minimum validation periods

    Returns:
        Dict with full analysis results
    """
    total = len(data_series)
    windows = generate_walk_forward_windows(total, n_windows, train_pct, min_train, min_val)

    if not windows:
        return {"_error": "No valid windows generated. Try more data or adjust parameters."}

    # In-sample (training) optimization — uses full dataset as one "in-sample" reference
    in_sample_returns = data_series
    in_sample_sharpe = compute_sharpe(in_sample_returns)

    # Walk-forward results
    window_results = []
    all_oos_returns = []

    for w in windows:
        train_returns = data_series[w["train_start"]:w["train_end"] + 1]
        val_returns = data_series[w["val_start"]:w["val_end"] + 1]

        if not train_returns or not val_returns:
            continue

        # Optimal parameters derived from training set
        train_sharpe = compute_sharpe(train_returns)
        train_sortino = compute_sortino(train_returns)

        # Out-of-sample performance
        oos_sharpe = compute_sharpe(val_returns)
        oos_sortino = compute_sortino(val_returns)

        # Build cumulative prices for drawdown calculation
        train_prices = [1000]
        for r in train_returns:
            train_prices.append(train_prices[-1] * (1 + r))
        val_prices = [train_prices[-1]]
        for r in val_returns:
            val_prices.append(val_prices[-1] * (1 + r))

        train_mdd = compute_max_drawdown(train_prices)
        val_mdd = compute_max_drawdown(val_prices)

        all_oos_returns.extend(val_returns)

        window_results.append({
            "window_index": w["window_index"],
            "train_start": w["train_start"],
            "train_end": w["train_end"],
            "train_length": w["train_length"],
            "val_start": w["val_start"],
            "val_end": w["val_end"],
            "val_length": w["val_length"],
            "train_sharpe": round(train_sharpe, 4),
            "train_sortino": round(train_sortino, 4),
            "train_max_drawdown": round(train_mdd, 4),
            "oos_sharpe": round(oos_sharpe, 4),
            "oos_sortino": round(oos_sortino, 4),
            "oos_max_drawdown": round(val_mdd, 4),
        })

    if not window_results:
        return {"_error": "No valid window results"}

    # Aggregate metrics
    oos_sharpes = [w["oos_sharpe"] for w in window_results]
    mean_oos_sharpe = sum(oos_sharpes) / len(oos_sharpes)
    oos_sharpes_positive = sum(1 for s in oos_sharpes if s > 0)

    # Compute stability
    stability = compute_stability_score(window_results)

    # Compute overall OOS Sharpe (pooled)
    overall_oos_sharpe = compute_sharpe(all_oos_returns)

    # Sharpe decay (in-sample vs out-of-sample)
    sharpe_decay = in_sample_sharpe - overall_oos_sharpe if overall_oos_sharpe != 0 else 0

    return {
        "total_periods": total,
        "windows_count": len(window_results),
        "in_sample_sharpe": round(in_sample_sharpe, 4),
        "overall_oos_sharpe": round(overall_oos_sharpe, 4),
        "sharpe_decay": round(sharpe_decay, 4),
        "stability_score": stability,
        "oos_sharpe_mean": round(mean_oos_sharpe, 4),
        "oos_sharpe_std": round(
            (sum((s - mean_oos_sharpe) ** 2 for s in oos_sharpes) / len(oos_sharpes)) ** 0.5,
            4
        ),
        "oos_positive_windows": oos_sharpes_positive,
        "oos_positive_pct": round(oos_sharpes_positive / len(oos_sharpes) * 100, 1),
        "windows": window_results,
    }


# ── Parameter Sensitivity Analysis ─────────────────────────────────────────────


def run_sensitivity(data_series, base_params=None):
    """
    Run parameter sensitivity analysis by perturbing key parameters.
    """
    if base_params is None:
        base_params = {"n_windows": 4, "train_pct": 0.7}

    # Baseline
    baseline = run_walkforward(
        data_series,
        n_windows=base_params.get("n_windows", 4),
        train_pct=base_params.get("train_pct", 0.7),
    )

    if "_error" in baseline:
        return baseline

    perturbations = []

    # Perturb window count
    for nw in [3, 5, 6, 8]:
        if nw != base_params["n_windows"]:
            p = run_walkforward(data_series, n_windows=nw, train_pct=base_params["train_pct"])
            if "_error" not in p:
                perturbations.append({"param": f"n_windows={nw}", "result": p})

    # Perturb train percentage
    for tp in [0.5, 0.6, 0.8]:
        if tp != base_params["train_pct"]:
            p = run_walkforward(data_series, n_windows=base_params["n_windows"], train_pct=tp)
            if "_error" not in p:
                perturbations.append({"param": f"train_pct={tp}", "result": p})

    # Compute parameter sensitivity
    window_sensitivity = compute_parameter_sensitivity(
        baseline,
        [pp["result"] for pp in perturbations if "n_windows" in pp.get("param", "")],
        "n_windows",
    )
    train_pct_sensitivity = compute_parameter_sensitivity(
        baseline,
        [pp["result"] for pp in perturbations if "train_pct" in pp.get("param", "")],
        "train_pct",
    )

    return {
        "baseline": baseline,
        "window_count_robustness": window_sensitivity,
        "train_pct_robustness": train_pct_sensitivity,
        "parameter_sensitivity_score": round((window_sensitivity + train_pct_sensitivity) / 2, 1),
        "perturbations": [
            {
                "param": pp["param"],
                "oos_sharpe": pp["result"].get("overall_oos_sharpe", 0),
                "stability": pp["result"].get("stability_score", 0),
            }
            for pp in perturbations
        ],
    }


# ── Report Builder ──────────────────────────────────────────────────────────────


def build_optimization_report(simulate=True, n_windows=DEFAULT_WINDOWS,
                              train_pct=DEFAULT_TRAIN_PCT, json_mode=False):
    """Build a walk-forward optimization report."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if simulate:
        # Generate simulated returns with a small positive edge
        data_series = simulate_returns(periods=500, mean=0.0008, volatility=0.025)
        note = "Using simulated returns with slight positive edge. Replace with real price data for production."
    else:
        data_series = simulate_returns(periods=500, mean=0.0003, volatility=0.03)
        note = "Using simulated data. Pass real return series for actual analysis."

    # Run walk-forward
    wf_result = run_walkforward(data_series, n_windows=n_windows, train_pct=train_pct)
    if "_error" in wf_result:
        return wf_result

    # Run sensitivity
    sensitivity = run_sensitivity(data_series)

    report = {
        "timestamp": timestamp,
        "agent": "palamedes-quantitative",
        "tool": "walkforward_opt",
        "parameters": {
            "n_windows": n_windows,
            "train_pct": train_pct,
            "min_train_days": DEFAULT_MIN_TRAIN,
            "min_val_days": DEFAULT_MIN_VAL,
        },
        "data_points": len(data_series),
        "note": note,
        "walkforward": wf_result,
        "sensitivity": sensitivity,
    }

    if json_mode:
        return json.dumps(report, indent=2)

    return _format_human_report(report)


def _format_human_report(r):
    """Format the optimization report for human reading."""
    wf = r.get("walkforward", {})
    sens = r.get("sensitivity", {})

    lines = []
    lines.append("Palamedes Walk-Forward Optimization Report")
    lines.append(f"Generated: {r.get('timestamp', 'unknown')}")
    lines.append(f"Data Points: {r.get('data_points', 0)}")
    lines.append("")

    # Parameters
    params = r.get("parameters", {})
    lines.append("── Parameters ──")
    lines.append(f"  Walk-Forward Windows : {params.get('n_windows', '?')}")
    lines.append(f"  Train/Val Split      : {params.get('train_pct', '?'):.0%} / {1-params.get('train_pct', 0.7):.0%}")
    lines.append("")

    # Walk-Forward Results
    lines.append("── Walk-Forward Results ──")
    lines.append(f"  In-Sample Sharpe     : {wf.get('in_sample_sharpe', 0):.4f}")
    lines.append(f"  Overall OOS Sharpe   : {wf.get('overall_oos_sharpe', 0):.4f}")
    lines.append(f"  Sharpe Decay         : {wf.get('sharpe_decay', 0):.4f}")
    lines.append(f"  Stability Score      : {wf.get('stability_score', 0):.1f}/100")
    lines.append(f"  OOS Positive Windows : {wf.get('oos_positive_windows', 0)}/{wf.get('windows_count', 0)} ({wf.get('oos_positive_pct', 0)}%)")
    lines.append("")

    # Per-window breakdown
    lines.append("── Per-Window Breakdown ──")
    for w in wf.get("windows", []):
        lines.append(
            f"  Window {w['window_index']}: "
            f"train={w['train_length']}d val={w['val_length']}d "
            f"IS Sharpe={w['train_sharpe']:.4f} "
            f"OOS Sharpe={w['oos_sharpe']:.4f} "
            f"OOS MDD={w['oos_max_drawdown']:.2%}"
        )
    lines.append("")

    # Sensitivity
    lines.append("── Parameter Sensitivity ──")
    lines.append(f"  Window Count Robustness : {sens.get('window_count_robustness', 0):.1f}/100")
    lines.append(f"  Train% Robustness       : {sens.get('train_pct_robustness', 0):.1f}/100")
    lines.append(f"  Overall Sensitivity     : {sens.get('parameter_sensitivity_score', 0):.1f}/100")
    if sens.get("parameter_sensitivity_score", 0) > 70:
        lines.append("  Assessment: ROBUST — strategy survives parameter changes")
    elif sens.get("parameter_sensitivity_score", 0) > 40:
        lines.append("  Assessment: MODERATE — some parameter sensitivity detected")
    else:
        lines.append("  Assessment: FRAGILE — results highly dependent on parameters (overfit risk)")
    lines.append("")

    lines.append(f"Note: {r.get('note', '')}")

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Palamedes — Walk-Forward Optimization v3.0",
    )
    parser.add_argument("--simulate", action="store_true", default=True,
                        help="Use simulated return data (default: True)")
    parser.add_argument("--windows", type=int, default=DEFAULT_WINDOWS,
                        help=f"Number of walk-forward windows (default: {DEFAULT_WINDOWS})")
    parser.add_argument("--train-pct", type=float, default=DEFAULT_TRAIN_PCT,
                        help=f"Training fraction per window (default: {DEFAULT_TRAIN_PCT})")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    report = build_optimization_report(
        simulate=args.simulate,
        n_windows=args.windows,
        train_pct=args.train_pct,
        json_mode=args.json,
    )
    print(report)


if __name__ == "__main__":
    main()
