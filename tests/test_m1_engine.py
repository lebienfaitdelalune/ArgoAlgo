"""Tests for backtest/m1_engine.py — the M1-fidelity validation engine.

The property that matters: conservative intra-bar ordering. The H1 engine's
trail-before-SL assumption manufactured a fake edge; this engine must test
SL/TP against each M1 bar BEFORE the trail moves on that bar.
"""

import csv
import datetime as dt
from array import array

import pytest

from backtest import m1_engine
from backtest.m1_engine import M1Backtest, M1Config
from models.trade_signal import TradeSignal
from utils.constants import Direction

YEAR = 1999  # synthetic year, never collides with real zips
BASE = 1.10000
PIP = 0.0001


class StubStrategy:
    """Signals BUY once at bar 30, SL 10 pips, TP 20 pips."""
    name = "Stub"

    def __init__(self, api=None, data_provider=None, logger=None, params=None):
        self._dp = data_provider
        self._fired = False

    def evaluate(self, symbol):
        idx = self._dp._cursor.idx
        if not self._fired and idx == 30:
            self._fired = True
            return TradeSignal(strategy_name=self.name, symbol=symbol,
                               direction=Direction.BUY, stop_loss_pips=10.0,
                               take_profit_pips=20.0, entry_price=BASE,
                               timestamp=dt.datetime(YEAR, 1, 1), metadata={})
        return TradeSignal(strategy_name=self.name, symbol=symbol,
                           direction=Direction.NONE, stop_loss_pips=0.0,
                           take_profit_pips=0.0, entry_price=0.0,
                           timestamp=dt.datetime(YEAR, 1, 1), metadata={})

    def should_close(self, position):
        return False


def build_fixture(tmp_path, m1_hours):
    """Write a flat H1 CSV and inject synthetic M1 bars into the engine cache.

    m1_hours: {hour_offset_from_start: [(high, low, close), ...]}
    """
    times = [dt.datetime(YEAR, 1, 4) + dt.timedelta(hours=i) for i in range(60)]
    csv_path = tmp_path / "h1.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "open", "high", "low", "close"])
        for i, t in enumerate(times):
            hi = max((b[0] for b in m1_hours.get(i, [])), default=BASE + PIP)
            lo = min((b[1] for b in m1_hours.get(i, [])), default=BASE - PIP)
            cl = m1_hours.get(i, [(0, 0, BASE)])[-1][2]
            w.writerow([t.strftime("%Y-%m-%d %H:%M:%S"),
                        f"{BASE:.5f}", f"{hi:.5f}", f"{lo:.5f}", f"{cl:.5f}"])

    highs, lows, closes = array("d"), array("d"), array("d")
    hour_index = {}
    i = 0
    for off, bars in sorted(m1_hours.items()):
        hour_index[times[off]] = [i, i + len(bars)]
        for h, l, c in bars:
            highs.append(h)
            lows.append(l)
            closes.append(c)
            i += 1
    m1_engine._M1_CACHE[("eurusd", YEAR)] = {"highs": highs, "lows": lows,
                                             "closes": closes, "hours": hour_index}
    return csv_path


@pytest.fixture(autouse=True)
def _clean_cache():
    yield
    m1_engine._M1_CACHE.pop(("eurusd", YEAR), None)


def _cfg(**kw):
    defaults = dict(tp_mode="rr", tp_rr=2.0, strat_exit=False,
                    year_from=YEAR, year_to=YEAR, max_sl_pips=None)
    defaults.update(kw)
    return M1Config(**defaults)


class TestConservativeOrdering:
    def test_sl_fires_before_tp_in_same_hour(self, tmp_path):
        # Hour 31 (first managed hour): M1 dips to SL, then rallies past TP.
        # Conservative ordering must record the LOSS.
        entry = BASE + 1 * PIP           # close + 1 pip spread
        sl = entry - 10 * PIP
        tp = entry + 20 * PIP
        path = build_fixture(tmp_path, {
            31: [(BASE, sl - PIP, sl),          # dip through SL
                 (tp + 5 * PIP, sl, tp + PIP)]  # then rally through TP
        })
        bt = M1Backtest(StubStrategy, path, _cfg())
        s = bt.run()
        assert s["trades"] == 1
        assert bt.trades[0].exit_reason == "SL"
        assert bt.trades[0].pnl_usd < 0

    def test_trail_cannot_save_same_bar_sl(self, tmp_path):
        # One M1 bar spikes up (would ratchet a tight trail above entry) AND
        # its low pierces the original SL. SL must fire at the ORIGINAL level:
        # the trail only moves after the bar's SL test.
        entry = BASE + 1 * PIP
        sl = entry - 10 * PIP
        path = build_fixture(tmp_path, {
            31: [(entry + 15 * PIP, sl - PIP, sl)],
        })
        cfg = _cfg(trail_trigger_atr=0.001, trail_distance_atr=0.001)
        bt = M1Backtest(StubStrategy, path, cfg)
        s = bt.run()
        assert s["trades"] == 1
        t = bt.trades[0]
        assert t.exit_reason == "SL"
        assert t.exit_price == pytest.approx(sl)

    def test_tp_fills_when_no_sl_touch(self, tmp_path):
        entry = BASE + 1 * PIP
        tp = entry + 20 * PIP
        path = build_fixture(tmp_path, {
            31: [(entry + 5 * PIP, entry - 2 * PIP, entry + 4 * PIP)],
            32: [(tp + 2 * PIP, entry + 3 * PIP, tp + PIP)],
        })
        bt = M1Backtest(StubStrategy, path, _cfg())
        s = bt.run()
        assert s["trades"] == 1
        assert bt.trades[0].exit_reason == "TP"
        assert bt.trades[0].pnl_usd > 0

    def test_trail_locks_profit_across_bars(self, tmp_path):
        # Bar A rallies (trail activates + ratchets AFTER surviving the bar),
        # bar B collapses -> exit at trailed stop, profit locked >= breakeven.
        entry = BASE + 1 * PIP
        path = build_fixture(tmp_path, {
            31: [(entry + 30 * PIP, entry, entry + 29 * PIP)],
            32: [(entry + 30 * PIP, entry - 20 * PIP, entry - 15 * PIP)],
        })
        cfg = _cfg(tp_mode="none", trail_trigger_atr=10.0,
                   trail_distance_atr=5.0)
        # ATR on flat synthetic data ~2 pips -> trigger ~20 pips, dist ~10 pips
        bt = M1Backtest(StubStrategy, path, cfg)
        s = bt.run()
        assert s["trades"] == 1
        t = bt.trades[0]
        assert t.exit_reason == "Trail"
        assert t.exit_price >= entry  # at least breakeven after trail activation
