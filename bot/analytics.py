"""Numbers for the monthly report and the dashboard, read only from logs/ and state.json."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config
from .journal import load_state, read_jsonl

GREEN, AMBER, RED = "verde", "amarelo", "vermelho"


def load_all(cfg: Config) -> dict:
    d = Path(cfg.logs_dir)
    return dict(trades=read_jsonl(d / "trades.jsonl"), equity=read_jsonl(d / "equity.jsonl"),
                shield=read_jsonl(d / "shield.jsonl"), decisions=read_jsonl(d / "decisions.jsonl"),
                system=read_jsonl(d / "system.jsonl"), state=load_state(cfg.state_file) or {})


def closed_trades(recs: list[dict]) -> pd.DataFrame:
    rows = [r for r in recs if r.get("event") == "close"]
    if not rows:
        return pd.DataFrame(columns=["id", "symbol", "entry_time", "exit_time", "pnl", "R", "net",
                                     "reason", "funding_usdt", "equity_after"])
    df = pd.DataFrame(rows)
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, format="ISO8601")
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True, format="ISO8601")
    return df.sort_values(["exit_time", "id"]).reset_index(drop=True)


def equity_frame(recs: list[dict]) -> pd.DataFrame:
    if not recs:
        return pd.DataFrame(columns=["time", "equity_mark_usdt"])
    df = pd.DataFrame(recs)
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    return df.sort_values("time").reset_index(drop=True)


def end_time(data: dict) -> pd.Timestamp:
    """'Now' for a report: the latest log record (live: about now; replay: the replay's end)."""
    ts = [pd.Timestamp(r["time"]) for k in ("equity", "shield", "system", "decisions")
          for r in data[k][-1:] if r.get("time")]
    return max(ts) if ts else pd.Timestamp.now(tz="UTC")


def max_drawdown(values) -> float:
    v = np.asarray(list(values), float)
    if len(v) == 0:
        return 0.0
    peak = np.maximum.accumulate(v)
    return float(np.max(1 - v / peak))


def streaks(pnl) -> tuple[int, int]:
    cur = best = 0
    for x in pnl:
        cur = cur + 1 if x <= 0 else 0
        best = max(best, cur)
    return cur, best


def summary(tr: pd.DataFrame, start=None, end=None) -> dict:
    w = tr
    if start is not None:
        w = w[w["exit_time"] >= start]
    if end is not None:
        w = w[w["exit_time"] <= end]
    n = len(w)
    out = dict(trades=n, pnl=float(w["pnl"].sum()) if n else 0.0,
               win_rate=float((w["pnl"] > 0).mean()) if n else math.nan,
               exp_R=float(w["R"].mean()) if n else math.nan,
               total_R=float(w["R"].sum()) if n else 0.0,
               funding=float(w["funding_usdt"].sum()) if n else 0.0,
               best_R=float(w["R"].max()) if n else math.nan,
               worst_R=float(w["R"].min()) if n else math.nan)
    if n >= 2:
        se = float(w["R"].std(ddof=1) / math.sqrt(n))
        out["exp_R_lo"], out["exp_R_hi"] = out["exp_R"] - 1.96 * se, out["exp_R"] + 1.96 * se
    else:
        out["exp_R_lo"] = out["exp_R_hi"] = math.nan
    return out


def equity_points(cfg: Config, eq: pd.DataFrame, start=None) -> pd.Series:
    if len(eq) == 0:
        return pd.Series([cfg.starting_equity_usdt], dtype=float)
    s = eq.set_index("time")["equity_mark_usdt"].astype(float)
    if start is not None:
        s = s[s.index >= start]
    return s


def health(cfg: Config, tr: pd.DataFrame, eq: pd.DataFrame, end: pd.Timestamp) -> dict:
    """S16 card: 'first sign it is weakening' (amber) and 'kill' (red) rules."""
    m = cfg.monitoring
    checks = []
    n_all = len(tr)
    w12 = tr[tr["exit_time"] >= end - pd.DateOffset(months=int(m.get("weakening_rolling_months",
                                                                      12)))]
    min_n = int(m.get("min_trades_for_verdict", 30))
    thr = float(m.get("weakening_min_exp_R", 0.05))
    e12 = float(w12["R"].mean()) if len(w12) else math.nan
    if len(w12) < min_n:
        checks.append(dict(name="expectativa 12 meses", level=AMBER, value=e12,
                           text=f"Ainda poucos trades em papel ({len(w12)} < {min_n}): cedo demais "
                                f"para concluir se o setup enfraqueceu."))
    elif e12 < thr:
        checks.append(dict(name="expectativa 12 meses", level=AMBER, value=e12,
                           text=f"SINAL DE ENFRAQUECIMENTO: a expectativa dos últimos 12 meses é "
                                f"{e12:+.3f}R por trade, abaixo do limite de {thr:+.2f}R do cartão "
                                f"S16."))
    else:
        checks.append(dict(name="expectativa 12 meses", level=GREEN, value=e12,
                           text=f"Expectativa dos últimos 12 meses {e12:+.3f}R por trade (limite "
                                f"{thr:+.2f}R): OK."))
    cur, best = streaks(tr["pnl"].to_numpy()) if n_all else (0, 0)
    lim = int(m.get("weakening_max_losing_streak", 15))
    checks.append(dict(name="perdas seguidas", level=AMBER if best > lim else GREEN, value=best,
                       text=(f"SINAL DE ENFRAQUECIMENTO: {best} perdas seguidas (cartão: mais de "
                             f"{lim} é sinal de alarme)." if best > lim else
                             f"Maior sequência de perdas {best} (alarme acima de {lim}); "
                             f"sequência atual {cur}.")))
    series = eq["equity_mark_usdt"].astype(float).to_numpy() if len(eq) else np.array([])
    dd = max_drawdown(np.r_[cfg.starting_equity_usdt, series])
    kdd = float(m.get("kill_max_drawdown", 0.30))
    wdd = float(m.get("weakening_max_drawdown", 0.28))
    lvl = RED if dd > kdd else AMBER if dd > wdd else GREEN
    checks.append(dict(name="drawdown máximo", level=lvl, value=dd,
                       text=(f"PARAR O PAPER-TRADING: drawdown {dd:.1%} acima de {kdd:.0%}."
                             if lvl == RED else
                             f"SINAL DE ENFRAQUECIMENTO: drawdown {dd:.1%} acima de {wdd:.0%}."
                             if lvl == AMBER else
                             f"Drawdown máximo {dd:.1%} (alarme {wdd:.0%}, parar {kdd:.0%}).")))
    kn = int(m.get("kill_rolling_trades", 100))
    if n_all >= kn:
        e100 = float(tr["R"].iloc[-kn:].mean())
        checks.append(dict(name=f"expectativa últimos {kn}", level=RED if e100 < 0 else GREEN,
                           value=e100,
                           text=(f"PARAR O PAPER-TRADING: expectativa dos últimos {kn} trades "
                                 f"{e100:+.3f}R, abaixo de zero." if e100 < 0 else
                                 f"Expectativa dos últimos {kn} trades {e100:+.3f}R: OK.")))
    order = {GREEN: 0, AMBER: 1, RED: 2}
    overall = max((c["level"] for c in checks), key=order.get)
    return dict(level=overall, checks=checks)


def shield_counts(events: list[dict], start=None) -> dict:
    out: dict = {}
    for e in events:
        if start is not None and pd.Timestamp(e["time"]) < start:
            continue
        key = e["type"] if e["type"] != "BLOCK" else "BLOCK " + e.get("rule", "?")
        out[key] = out.get(key, 0) + 1
    return out
