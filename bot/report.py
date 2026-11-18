"""Monthly summary for the owner:  python -m bot.report --days 30

Reads only logs/ and state.json (works while the bot runs). Text in Portuguese.
"""
from __future__ import annotations

import argparse
import math
import sys

import pandas as pd

from . import analytics as A
from . import config as C

LEVEL_TXT = {A.GREEN: "VERDE", A.AMBER: "AMARELO", A.RED: "VERMELHO"}


def _m(x, d=2):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.{d}f}"


def build(cfg: C.Config, days: int = 30, until: str | None = None) -> str:
    data = A.load_all(cfg)
    end = pd.Timestamp(until, tz="UTC") if until else A.end_time(data)
    start = end - pd.Timedelta(days=days)
    tr = A.closed_trades(data["trades"])
    eq = A.equity_frame(data["equity"])
    st = data["state"]
    fx = cfg.eur_usd_estimate
    s = A.summary(tr, start, end)
    s_all = A.summary(tr)
    ref = cfg.backtest_reference
    L = []
    L.append("=" * 72)
    L.append(f" RELATÓRIO MENSAL - PAPER TRADING (sem dinheiro real) - setup {cfg.setup_id}")
    L.append(f" Período: {start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M} UTC ({days} dias)")
    L.append("=" * 72)
    if not data["equity"] and not data["trades"]:
        L.append("Ainda não há registos em logs/ (o bot ainda não correu).")
        return "\n".join(L)
    pts = A.equity_points(cfg, eq, start)
    last = float(pts.iloc[-1]) if len(pts) else cfg.starting_equity_usdt
    L.append(f"Capital (papel) agora: {_m(last)} USDT = {_m(last / fx)} EUR (câmbio ESTIMATE "
             f"{fx}); início {_m(cfg.starting_equity_usdt)} USDT.")
    L.append("")
    L.append("-- Trades fechados no período --")
    L.append(f"  trades: {s['trades']}   ganhos: {_m(100 * s['win_rate'], 1)}%   "
             f"P&L: {_m(s['pnl'])} USDT ({_m(s['pnl'] / fx)} EUR)   funding: "
             f"{_m(s['funding'], 3)} USDT")
    L.append(f"  expectativa: {_m(s['exp_R'], 3)}R por trade   total {_m(s['total_R'], 2)}R   "
             f"melhor {_m(s['best_R'], 2)}R   pior {_m(s['worst_R'], 2)}R")
    dd_p = A.max_drawdown(pts.to_numpy()) if len(pts) else 0.0
    dd_all = A.max_drawdown([cfg.starting_equity_usdt] +
                            list(eq["equity_mark_usdt"].astype(float))) if len(eq) else 0.0
    L.append(f"  drawdown máximo: {100 * dd_p:.1f}% no período, {100 * dd_all:.1f}% desde o "
             "início (capital marcado a mercado, de hora a hora)")
    if s["trades"]:
        w = tr[(tr["exit_time"] >= start) & (tr["exit_time"] <= end)]
        by = w.groupby("symbol").agg(n=("R", "size"), R=("R", "sum"), pnl=("pnl", "sum"))
        L.append("  por moeda: " + ", ".join(f"{k} {int(r.n)}x {r.R:+.1f}R"
                                            for k, r in by.sort_values("R").iterrows()))
    L.append("")
    L.append("-- Papel vs backtest (LAB, fora da amostra 2024-11..2026-10) --")
    L.append(f"  backtest: {ref.get('oos_exp_R', float('nan')):+.3f}R por trade, ganhos "
             f"{100 * ref.get('oos_win_rate', float('nan')):.1f}%, ~"
             f"{ref.get('trades_per_month', float('nan'))} trades/mês (sem limites do Shield); "
             f"2.º ano só {ref.get('oos_year2_exp_R', float('nan')):+.3f}R")
    for lab_, x in (("período", s), ("desde o início", s_all)):
        if x["trades"] >= 2:
            inside = x["exp_R_lo"] <= ref.get("oos_exp_R", 0) <= x["exp_R_hi"]
            L.append(f"  papel ({lab_}): {x['exp_R']:+.3f}R em {x['trades']} trades (intervalo 95% "
                     f"{x['exp_R_lo']:+.2f}..{x['exp_R_hi']:+.2f}R) -> "
                     + ("compatível com o backtest" if inside else
                        "DIFERENTE do backtest (fora do intervalo)"))
        else:
            L.append(f"  papel ({lab_}): {x['trades']} trades - poucos para comparar")
    L.append("  Nota: com 24% de trades ganhadores, dezenas de trades são precisos antes de "
             "qualquer conclusão.")
    L.append("")
    L.append("-- Shield (bloqueios e paragens) no período --")
    cnt = A.shield_counts(data["shield"], start)
    if not cnt:
        L.append("  nenhum evento")
    for k, v in sorted(cnt.items()):
        L.append(f"  {k}: {v}")
    for e in data["shield"]:
        if e["type"] == "HALT" and pd.Timestamp(e["time"]) >= start:
            L.append(f"  HALT {e['time'][:16]}: {e['reason']}")
    miss = sum(1 for d in data["decisions"] if d.get("result") == "missed"
               and pd.Timestamp(d["time"]) >= start)
    if miss:
        L.append(f"  entradas perdidas por o bot não estar a correr: {miss}")
    L.append("")
    h = A.health(cfg, tr, eq, end)
    L.append(f"-- Saúde do setup ({cfg.setup_id}): {LEVEL_TXT[h['level']]} --")
    for c in h["checks"]:
        L.append(f"  [{LEVEL_TXT[c['level']]}] {c['text']}")
    L.append("")
    u = st.get("universe") or {}
    L.append(f"Universo top-10 ({u.get('month', '-')}, calculado {str(u.get('computed_at', '-'))[:16]}"
             f"): {', '.join(u.get('symbols', []))}")
    if cfg.halt_file.exists():
        L.append("")
        L.append("ATENÇÃO: o bot está PARADO (ficheiro HALT). Leia logs/shield.jsonl e apague o "
                 "ficheiro HALT para retomar.")
    L.append("Lembrete: PAPER ONLY. Valores de mínimo nocional são ESTIMATE até à primeira "
             "leitura do exchangeInfo.")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Monthly paper-trading report")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--config", default=None)
    ap.add_argument("--until", default=None, help="end of the period (UTC), default: last log")
    a = ap.parse_args(argv)
    cfg = C.load(a.config)
    text = build(cfg, a.days, a.until)
    try:
        print(text)
    except UnicodeEncodeError:  # old Windows consoles
        sys.stdout.buffer.write(text.encode("utf-8", "replace") + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
