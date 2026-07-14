"""
data_prep.py
Extract histdata.com EURUSD M1 ZIPs and resample to H1 UTC OHLC bars.

histdata.com timestamps are EST without DST adjustment (i.e., UTC-5 year-round).
We add 5 hours to convert to UTC, which keeps bar alignment with cTrader's UTC bars.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import sys
import zipfile
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
H1_OUT = DATA_DIR / "eurusd_h1_utc.csv"

EST_TO_UTC_HOURS = 5  # histdata uses EST without DST


def iter_m1_rows(zip_path: Path):
    """Yield (datetime_utc, o, h, l, c) tuples from a histdata M1 ZIP."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(csv_name) as f:
            for raw in f:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                fields = line.split(";")
                # Format: YYYYMMDD HHMMSS;O;H;L;C;V
                ts_str = fields[0]
                ts_est = dt.datetime.strptime(ts_str, "%Y%m%d %H%M%S")
                ts_utc = ts_est + dt.timedelta(hours=EST_TO_UTC_HOURS)
                o = float(fields[1])
                h = float(fields[2])
                l = float(fields[3])
                c = float(fields[4])
                yield (ts_utc, o, h, l, c)


def resample_m1_to_h1(rows):
    """Aggregate M1 rows into H1 bars keyed on the bar's start hour (UTC)."""
    current_hour = None
    cur_o = cur_h = cur_l = cur_c = None
    for ts, o, h, l, c in rows:
        hour_key = ts.replace(minute=0, second=0, microsecond=0)
        if current_hour is None:
            current_hour = hour_key
            cur_o, cur_h, cur_l, cur_c = o, h, l, c
            continue
        if hour_key != current_hour:
            yield (current_hour, cur_o, cur_h, cur_l, cur_c)
            current_hour = hour_key
            cur_o, cur_h, cur_l, cur_c = o, h, l, c
        else:
            cur_h = max(cur_h, h)
            cur_l = min(cur_l, l)
            cur_c = c  # close = last M1 close in the hour
    if current_hour is not None:
        yield (current_hour, cur_o, cur_h, cur_l, cur_c)


def main(years: list[int]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    zips = [DATA_DIR / f"eurusd_{y}.zip" for y in years]
    missing = [z for z in zips if not z.exists()]
    if missing:
        print(f"Missing ZIPs: {missing}", file=sys.stderr)
        sys.exit(1)

    print(f"Resampling {len(zips)} year(s) of M1 → H1 UTC...")
    h1_rows = []
    for zp in zips:
        print(f"  {zp.name}...", end="", flush=True)
        m1_rows = iter_m1_rows(zp)
        before = len(h1_rows)
        h1_rows.extend(resample_m1_to_h1(m1_rows))
        print(f" {len(h1_rows) - before} H1 bars")

    h1_rows.sort(key=lambda r: r[0])

    with open(H1_OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "open", "high", "low", "close"])
        for ts, o, h, l, c in h1_rows:
            w.writerow([ts.strftime("%Y-%m-%d %H:%M:%S"),
                        f"{o:.5f}", f"{h:.5f}", f"{l:.5f}", f"{c:.5f}"])

    print(f"Wrote {len(h1_rows)} H1 bars → {H1_OUT}")
    print(f"Range: {h1_rows[0][0]} → {h1_rows[-1][0]}")


if __name__ == "__main__":
    years = [int(y) for y in sys.argv[1:]] or [2023, 2024, 2025]
    main(years)
