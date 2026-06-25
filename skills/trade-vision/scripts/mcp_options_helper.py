"""
MCP helper for the TradingView MCP options tools.

Receives JSON args via --json-args, runs the requested action, prints a
single-line JSON result to stdout.

Actions:
  - chain       → options_screen.screen() output (full chain with greeks)
  - expirations → list of available expiration dates with DTE
  - screen      → filtered/screened options for CCs
  - greeks      → BS call price + greeks for hypothetical option
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone

import options_screen


def action_chain(args):
    calls, spot, meta = options_screen.screen(
        ticker=args["symbol"],
        min_dte=args.get("min_dte", 0),
        max_dte=args.get("max_dte", 7),
        prefer=args.get("prefer", "auto"),
        spot_override=args.get("spot_override"),
    )
    return {
        "ticker": args["symbol"].upper(),
        "spot": spot,
        "source": meta.get("source"),
        "meta": meta,
        "calls": [options_screen.asdict(c) if hasattr(options_screen, "asdict") else c.__dict__
                  for c in calls],
    }


def action_expirations(args):
    """List available expiration dates for a symbol with DTE."""
    # Use yfinance directly for expirations (cheap)
    import yfinance as yf
    t = yf.Ticker(args["symbol"])
    expirations = list(t.options)
    today = datetime.now(timezone.utc).date()
    exps = []
    for exp_str in expirations:
        try:
            exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = (exp_dt - today).days
            exps.append({"expiration": exp_str, "dte": dte})
        except ValueError:
            continue
    return {
        "ticker": args["symbol"].upper(),
        "expirations": exps,
        "count": len(exps),
    }


def action_screen(args):
    """Screen options by buffer / DTE / delta."""
    calls, spot, meta = options_screen.screen(
        ticker=args["symbol"],
        min_dte=0,
        max_dte=args.get("max_dte", 7),
        prefer=args.get("prefer", "auto"),
    )
    spot_override = args.get("spot_override") or spot

    min_buffer = args.get("min_buffer_pct", 2.0) / 100.0
    max_buffer = args.get("max_buffer_pct", 15.0) / 100.0
    max_delta = args.get("max_delta", 0.50)

    filtered = []
    for c in calls:
        if spot_override <= 0:
            continue
        buffer = (c.strike - spot_override) / spot_override
        if buffer < min_buffer or buffer > max_buffer:
            continue
        if c.delta > max_delta:
            continue
        # Score: prefer moderate buffer, decent premium, low spread
        premium_pct = c.mid / spot_override if spot_override > 0 else 0
        spread_pct = c.spread_pct
        score = (
            0.50 * min(premium_pct * 100, 10)  # cap at 10% premium
            - 0.20 * spread_pct * 100          # penalize wide spreads
            + 0.30 * (1 - c.delta) * 100       # prefer lower delta (more OTM)
        )
        filtered.append({
            "strike": c.strike,
            "expiration": c.expiration,
            "dte": max(0, (datetime.fromisoformat(c.expiration).date()
                           - datetime.now(timezone.utc).date()).days),
            "bid": c.bid,
            "ask": c.ask,
            "mid": c.mid,
            "delta": c.delta,
            "iv": c.iv,
            "volume": c.volume,
            "open_interest": c.open_interest,
            "spread_pct": c.spread_pct,
            "buffer_pct": round(buffer * 100, 2),
            "premium_pct": round(premium_pct * 100, 3),
            "score": round(score, 2),
        })

    # Sort by score descending
    filtered.sort(key=lambda x: x["score"], reverse=True)

    return {
        "ticker": args["symbol"].upper(),
        "spot": spot_override,
        "source": meta.get("source"),
        "filters": {
            "min_buffer_pct": args.get("min_buffer_pct", 2.0),
            "max_buffer_pct": args.get("max_buffer_pct", 15.0),
            "max_dte": args.get("max_dte", 7),
            "max_delta": args.get("max_delta", 0.50),
        },
        "n_candidates": len(filtered),
        "candidates": filtered[:20],  # top 20
    }


def action_greeks(args):
    """Compute BS call price + greeks for a hypothetical option."""
    S = args["spot"]
    K = args["strike"]
    T = args["days_to_expiry"] / 365.0
    sigma = args["iv"]
    r = args.get("rfr", 0.045)

    price, greeks = options_screen.bs_call(S, K, T, r, sigma)
    return {
        "spot": S,
        "strike": K,
        "days_to_expiry": args["days_to_expiry"],
        "iv": sigma,
        "rfr": r,
        "price": round(price, 4),
        "delta": round(greeks["delta"], 4),
        "gamma": round(greeks["gamma"], 6),
        "theta_per_day": round(greeks["theta"], 4),
        "vega_per_1pct_iv": round(greeks["vega"], 4),
    }


def main():
    if len(sys.argv) != 3 or sys.argv[1] != "--json-args":
        print(f"usage: {sys.argv[0]} --json-args '<json>'", file=sys.stderr)
        sys.exit(1)

    args = json.loads(sys.argv[2])
    action = args.get("action")

    if action == "chain":
        result = action_chain(args)
    elif action == "expirations":
        result = action_expirations(args)
    elif action == "screen":
        result = action_screen(args)
    elif action == "greeks":
        result = action_greeks(args)
    else:
        result = {"error": f"unknown action: {action}"}

    # Output the result as a single-line JSON to stdout
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()