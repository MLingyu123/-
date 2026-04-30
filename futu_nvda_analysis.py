#!/usr/bin/env python3
"""
使用 Futu OpenD 拉取 NVDA 实时/分时数据，并做简单“今晚暴跌归因”分析。

依赖：
  pip install futu pandas

使用前：
  1) 启动 Futu OpenD（默认 127.0.0.1:11111）
  2) 在 OpenD 中允许本机连接与对应市场权限

示例：
  python futu_nvda_analysis.py --date 2026-04-30
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

import pandas as pd
from futu import OpenQuoteContext, RET_OK, SubType


@dataclass
class SymbolSnapshot:
    code: str
    name: str
    last_price: float
    open_price: float
    high_price: float
    low_price: float
    prev_close_price: float
    volume: float
    turnover: float

    @property
    def pct_change(self) -> float:
        if self.prev_close_price == 0:
            return 0.0
        return (self.last_price - self.prev_close_price) / self.prev_close_price * 100


def get_snapshot(quote_ctx: OpenQuoteContext, codes: List[str]) -> Dict[str, SymbolSnapshot]:
    ret, df = quote_ctx.get_market_snapshot(codes)
    if ret != RET_OK:
        raise RuntimeError(f"get_market_snapshot failed: {df}")

    out: Dict[str, SymbolSnapshot] = {}
    for _, r in df.iterrows():
        out[r["code"]] = SymbolSnapshot(
            code=r["code"],
            name=r["name"],
            last_price=float(r["last_price"]),
            open_price=float(r["open_price"]),
            high_price=float(r["high_price"]),
            low_price=float(r["low_price"]),
            prev_close_price=float(r["prev_close_price"]),
            volume=float(r["volume"]),
            turnover=float(r["turnover"]),
        )
    return out


def get_intraday_bars(quote_ctx: OpenQuoteContext, code: str) -> pd.DataFrame:
    ret, data = quote_ctx.get_cur_kline(code, num=390, ktype="K_1M", autype="qfq")
    if ret != RET_OK:
        raise RuntimeError(f"get_cur_kline failed for {code}: {data}")
    return data.copy()


def detect_selloff_window(kline_df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    df = kline_df.copy()
    df["pct_1m"] = (df["close"] / df["close"].shift(1) - 1) * 100
    drops = df.nsmallest(top_n, "pct_1m")[["time_key", "open", "close", "volume", "pct_1m"]]
    return drops.reset_index(drop=True)


def analyze_cross_section(snaps: Dict[str, SymbolSnapshot], target: str = "US.NVDA") -> str:
    target_snap = snaps[target]
    peer_codes = [c for c in snaps if c != target]

    peer_moves = [snaps[c].pct_change for c in peer_codes]
    peer_avg = sum(peer_moves) / len(peer_moves) if peer_moves else 0.0
    diff = target_snap.pct_change - peer_avg

    if target_snap.pct_change < -3 and diff < -2:
        style = "个股主导抛压（更像仓位/估值/事件驱动）"
    elif target_snap.pct_change < 0 and peer_avg < 0:
        style = "板块与大盘共振下跌（系统性风险偏好回落）"
    else:
        style = "波动较中性，需结合消息面进一步确认"

    return (
        f"NVDA当日涨跌幅: {target_snap.pct_change:.2f}%\n"
        f"对照组平均涨跌幅: {peer_avg:.2f}%\n"
        f"相对差值(NVDA-对照): {diff:.2f}pct\n"
        f"归因判断: {style}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--date", default=datetime.utcnow().strftime("%Y-%m-%d"))
    parser.add_argument(
        "--benchmarks",
        nargs="*",
        default=["US.AMD", "US.SMH", "US.QQQ"],
        help="对照标的，默认 AMD/SMH/QQQ",
    )
    parser.add_argument("--output_csv", default="nvda_intraday_1m.csv")
    args = parser.parse_args()

    target = "US.NVDA"
    codes = [target] + args.benchmarks

    quote_ctx = OpenQuoteContext(host=args.host, port=args.port)
    try:
        ret, msg = quote_ctx.subscribe([target], [SubType.K_1M], is_first_push=False)
        if ret != RET_OK:
            raise RuntimeError(f"subscribe failed: {msg}")

        snaps = get_snapshot(quote_ctx, codes)
        nvda_kline = get_intraday_bars(quote_ctx, target)

        nvda_kline.to_csv(args.output_csv, index=False)
        selloff_windows = detect_selloff_window(nvda_kline, top_n=5)

        print("=" * 72)
        print(f"日期: {args.date} | 标的: {target}")
        print("=" * 72)

        print("\n[1] 实时快照")
        for code in codes:
            s = snaps[code]
            print(
                f"{code:8s} {s.name:15s} 最新 {s.last_price:8.2f} | "
                f"开 {s.open_price:8.2f} 高 {s.high_price:8.2f} 低 {s.low_price:8.2f} | "
                f"涨跌 {s.pct_change:6.2f}%"
            )

        print("\n[2] NVDA 1分钟最大跌幅时段（Top5）")
        print(selloff_windows.to_string(index=False))

        print("\n[3] 自动归因")
        print(analyze_cross_section(snaps, target=target))

        print(f"\n已导出1分钟K线到: {args.output_csv}")
    finally:
        quote_ctx.close()


if __name__ == "__main__":
    main()
