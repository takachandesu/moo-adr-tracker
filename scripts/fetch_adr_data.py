"""日本株ADR乖離率データ取得スクリプト（中央値ベース推定版）"""

from __future__ import annotations
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
MASTER_PATH = ROOT / "scripts" / "adr_list.json"
OUTPUT_PATH = ROOT / "public" / "adr-data.json"
JST = timezone(timedelta(hours=9))


def load_master():
    with MASTER_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _scalar(val):
    try:
        while hasattr(val, "iloc"):
            val = val.iloc[0] if len(val) > 0 else None
        while isinstance(val, (list, tuple)):
            val = val[0] if val else None
        if val is None or pd.isna(val):
            return None
        return float(val)
    except Exception:
        return None


def get_close_series(df, ticker=None):
    try:
        if df is None or df.empty:
            return None
        if ticker and isinstance(df.columns, pd.MultiIndex):
            lvl0 = set(df.columns.get_level_values(0))
            if ticker in lvl0:
                sub = df[ticker]
                if "Close" in sub.columns:
                    return sub["Close"].dropna()
            elif "Close" in lvl0:
                close = df["Close"]
                if ticker in close.columns:
                    return close[ticker].dropna()
            return None
        else:
            close = df["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            return close.dropna()
    except Exception:
        return None


def latest_close(df, ticker=None):
    series = get_close_series(df, ticker)
    if series is None or len(series) == 0:
        return None
    return _scalar(series.iloc[-1])


def latest_date(df, ticker=None):
    series = get_close_series(df, ticker)
    if series is None or len(series) == 0:
        return None
    try:
        return series.index[-1].strftime("%Y-%m-%d")
    except Exception:
        return None


def batch_download(tickers, period="15d"):
    for attempt in range(3):
        try:
            df = yf.download(
                tickers, period=period, progress=False,
                group_by="ticker", auto_adjust=False, threads=True,
            )
            if df is not None and not df.empty:
                return df
        except Exception as e:
            print(f"[warn] batch download attempt {attempt + 1} failed: {e}", file=sys.stderr, flush=True)
        time.sleep(5)
    return pd.DataFrame()


def fetch_usdjpy():
    try:
        t = yf.Ticker("JPY=X")
        hist = t.history(period="10d", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        close = hist["Close"].dropna()
        if len(close) == 0:
            return None
        return _scalar(close.iloc[-1])
    except Exception as e:
        print(f"[error] USD/JPY fetch failed: {e}", file=sys.stderr, flush=True)
        return None


def estimate_ratio(us_df, jp_df, us_ticker, jp_ticker, fx, n_days=5):
    """過去n日（当日除く）の中央値を比率として返す。丸めない。"""
    try:
        us_series = get_close_series(us_df, us_ticker)
        jp_series = get_close_series(jp_df, jp_ticker)
        if us_series is None or jp_series is None:
            return None
        if len(us_series) < 2 or len(jp_series) < 2:
            return None

        m = min(n_days + 1, len(us_series), len(jp_series))
        ratios = []
        for i in range(2, m + 1):
            u = _scalar(us_series.iloc[-i])
            j = _scalar(jp_series.iloc[-i])
            if u and j and u > 0 and j > 0:
                ratios.append(u * fx / j)

        if not ratios:
            return None

        ratios.sort()
        return ratios[len(ratios) // 2]
    except Exception:
        return None


def compute_divergence(adr, us_df, jp_df, fx):
    us_ticker = adr["ticker"]
    jp_ticker = adr["jp_ticker"]
    if not jp_ticker:
        return None

    us_close = latest_close(us_df, us_ticker)
    jp_close = latest_close(jp_df, jp_ticker)
    us_date = latest_date(us_df, us_ticker)
    jp_date = latest_date(jp_df, jp_ticker)

    if us_close is None or jp_close is None or jp_close <= 0:
        return None

    ratio = estimate_ratio(us_df, jp_df, us_ticker, jp_ticker, fx)
    if ratio is None or ratio <= 0:
        ratio = float(adr.get("adr_ratio", 1.0))

    ny_implied_jp = (us_close * fx) / ratio
    divergence_pct = (ny_implied_jp / jp_close - 1.0) * 100.0

    return {
        "ticker": us_ticker,
        "jp_ticker": jp_ticker.replace(".T", ""),
        "name_en": adr["name_en"],
        "name_jp": adr["name_jp"],
        "level": adr["level"],
        "exchange": adr.get("exchange", ""),
        "adr_ratio": round(ratio, 4),
        "us_close_usd": round(us_close, 2),
        "jp_close_jpy": round(jp_close, 1),
        "ny_implied_jpy": round(ny_implied_jp, 1),
        "divergence_pct": round(divergence_pct, 2),
        "us_date": us_date,
        "jp_date": jp_date,
    }


def main():
    print(f"[info] fetch start: {datetime.now(JST).isoformat()}", flush=True)
    master = load_master()
    adrs = master["adrs"]
    print(f"[info] master count: {len(adrs)}", flush=True)

    us_tickers = [a["ticker"] for a in adrs]
    jp_tickers = [a["jp_ticker"] for a in adrs if a["jp_ticker"]]

    print("[info] fetching USD/JPY ...", flush=True)
    fx = fetch_usdjpy()
    if fx is None:
        print("[error] USD/JPY fetch failed", file=sys.stderr, flush=True)
        return 1
    print(f"[info] USD/JPY = {fx:.4f}", flush=True)

    print(f"[info] fetching {len(us_tickers)} US ADRs ...", flush=True)
    us_df = batch_download(us_tickers, period="15d")
    if us_df.empty:
        print("[error] US ADR fetch failed", file=sys.stderr, flush=True)
        return 1

    print(f"[info] fetching {len(jp_tickers)} Tokyo stocks ...", flush=True)
    jp_df = batch_download(jp_tickers, period="15d")
    if jp_df.empty:
        print("[error] Tokyo stocks fetch failed", file=sys.stderr, flush=True)
        return 1

    results = []
    skipped = []
    for adr in adrs:
        row = compute_divergence(adr, us_df, jp_df, fx)
        if row is None:
            skipped.append(adr["ticker"])
        else:
            results.append(row)
    print(f"[info] computed: {len(results)} / skipped: {len(skipped)}", flush=True)

    anomalies = [r for r in results if abs(r["divergence_pct"]) > 15.0]
    if anomalies:
        print(f"[warn] {len(anomalies)} anomalies (>15%):", flush=True)
        for a in sorted(anomalies, key=lambda x: abs(x["divergence_pct"]), reverse=True)[:10]:
            print(f"  {a['ticker']} ({a['jp_ticker']}) {a['name_jp']}: ratio={a['adr_ratio']}, divergence={a['divergence_pct']:+.2f}%", flush=True)

    results.sort(key=lambda x: x["divergence_pct"], reverse=True)
    best = results[:20]
    worst = list(reversed(results[-20:])) if len(results) >= 20 else list(reversed(results))

    output = {
        "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "usdjpy": round(fx, 2),
        "total_count": len(results),
        "skipped_count": len(skipped),
        "best": best,
        "worst": worst,
        "all": results,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[info] wrote {OUTPUT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
