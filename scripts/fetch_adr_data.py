"""
日本株ADR乖離率データ取得スクリプト

各ADRについて:
  NY終値(USD) × USDJPY(NY引け) ÷ ADR比率 ÷ 東京終値(JPY) − 1
を計算し、ベスト20・ワースト20を JSON に書き出す。

出力: public/adr-data.json
"""

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


def load_master() -> dict:
    with MASTER_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def latest_close(df: pd.DataFrame, ticker: str | None = None) -> float | None:
    """yfinance のレスポンスから最新終値を返す。失敗時 None。"""
    try:
        if ticker and isinstance(df.columns, pd.MultiIndex):
            series = df[ticker]["Close"].dropna()
        else:
            series = df["Close"].dropna()
        if len(series) == 0:
            return None
        return float(series.iloc[-1])
    except (KeyError, AttributeError, IndexError):
        return None


def latest_date(df: pd.DataFrame, ticker: str | None = None) -> str | None:
    """最新終値の日付（YYYY-MM-DD）を返す。"""
    try:
        if ticker and isinstance(df.columns, pd.MultiIndex):
            series = df[ticker]["Close"].dropna()
        else:
            series = df["Close"].dropna()
        if len(series) == 0:
            return None
        return series.index[-1].strftime("%Y-%m-%d")
    except (KeyError, AttributeError, IndexError):
        return None


def batch_download(tickers: list[str], period: str = "10d") -> pd.DataFrame:
    """銘柄をまとめて取得。yfinance のレート制限対策で軽くリトライ。"""
    for attempt in range(3):
        try:
            df = yf.download(
                tickers,
                period=period,
                progress=False,
                group_by="ticker",
                auto_adjust=False,
                threads=True,
            )
            if df is not None and not df.empty:
                return df
        except Exception as e:
            print(f"[warn] batch download attempt {attempt + 1} failed: {e}", file=sys.stderr)
        time.sleep(5)
    return pd.DataFrame()


def fetch_usdjpy() -> float | None:
    """NY引け時刻に近い USDJPY 終値を取得。Yahoo の JPY=X を使用。"""
    df = yf.download("JPY=X", period="10d", progress=False, auto_adjust=False)
    return latest_close(df)


def compute_divergence(adr: dict, us_df: pd.DataFrame, jp_df: pd.DataFrame, fx: float) -> dict | None:
    """1銘柄の乖離率を計算して結果dictを返す。データ不足時はNone。"""
    us_ticker = adr["ticker"]
    jp_ticker = adr["jp_ticker"]
    ratio = float(adr.get("adr_ratio", 1.0))

    if not jp_ticker:
        # 東京市場非上場のレベル3 IPOなど。乖離率計算対象外。
        return None

    us_close = latest_close(us_df, us_ticker)
    jp_close = latest_close(jp_df, jp_ticker)
    us_date = latest_date(us_df, us_ticker)
    jp_date = latest_date(jp_df, jp_ticker)

    if us_close is None or jp_close is None or jp_close <= 0 or ratio <= 0:
        return None

    # NY終値の円換算（東京1株あたり）
    ny_implied_jp = (us_close * fx) / ratio
    divergence_pct = (ny_implied_jp / jp_close - 1.0) * 100.0

    return {
        "ticker": us_ticker,
        "jp_ticker": jp_ticker.replace(".T", ""),
        "name_en": adr["name_en"],
        "name_jp": adr["name_jp"],
        "level": adr["level"],
        "exchange": adr.get("exchange", ""),
        "us_close_usd": round(us_close, 2),
        "jp_close_jpy": round(jp_close, 1),
        "ny_implied_jpy": round(ny_implied_jp, 1),
        "divergence_pct": round(divergence_pct, 2),
        "us_date": us_date,
        "jp_date": jp_date,
    }


def main() -> int:
    print(f"[info] fetch start: {datetime.now(JST).isoformat()}")

    master = load_master()
    adrs = master["adrs"]
    print(f"[info] master count: {len(adrs)}")

    us_tickers = [a["ticker"] for a in adrs]
    jp_tickers = [a["jp_ticker"] for a in adrs if a["jp_ticker"]]

    print("[info] fetching USD/JPY ...")
    fx = fetch_usdjpy()
    if fx is None:
        print("[error] USD/JPY fetch failed", file=sys.stderr)
        return 1
    print(f"[info] USD/JPY = {fx:.4f}")

    print(f"[info] fetching {len(us_tickers)} US ADRs ...")
    us_df = batch_download(us_tickers, period="10d")
    if us_df.empty:
        print("[error] US ADR fetch failed", file=sys.stderr)
        return 1

    print(f"[info] fetching {len(jp_tickers)} Tokyo stocks ...")
    jp_df = batch_download(jp_tickers, period="10d")
    if jp_df.empty:
        print("[error] Tokyo stocks fetch failed", file=sys.stderr)
        return 1

    results: list[dict] = []
    skipped: list[str] = []
    for adr in adrs:
        row = compute_divergence(adr, us_df, jp_df, fx)
        if row is None:
            skipped.append(adr["ticker"])
        else:
            results.append(row)

    print(f"[info] computed: {len(results)} / skipped: {len(skipped)}")
    if skipped:
        print(f"[info] skipped tickers: {', '.join(skipped)}")

    # 異常乖離率の検出（ADR比率の設定ミスを早期発見するため）
    # ±15%超は通常ありえないので、ADR比率の見直しが必要
    anomalies = [r for r in results if abs(r["divergence_pct"]) > 15.0]
    if anomalies:
        print(f"[warn] {len(anomalies)} 銘柄で異常乖離(>15%)を検出。ADR比率の設定を確認してください:")
        for a in sorted(anomalies, key=lambda x: abs(x["divergence_pct"]), reverse=True):
            print(f"  {a['ticker']} ({a['jp_ticker']}) {a['name_jp']}: {a['divergence_pct']:+.2f}%")

    # 乖離率で降順ソート
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
        "all": results,  # 全件もJSONに保持（必要に応じて使用）
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[info] wrote {OUTPUT_PATH}")
    print(f"[info] top 3 best: {[(r['ticker'], r['divergence_pct']) for r in best[:3]]}")
    print(f"[info] top 3 worst: {[(r['ticker'], r['divergence_pct']) for r in worst[:3]]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
