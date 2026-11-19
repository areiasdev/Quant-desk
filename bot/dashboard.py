"""Local dashboard:  python -m bot.dashboard  [--watch] [--no-open] [--out FILE]

Builds ONE self-contained dashboard.html from logs/ and state.json (no server, no internet, no
CDN: CSS inline, charts as inline SVG drawn here in Python) and opens it in the default
browser. --watch rebuilds it every 5 minutes; the page reloads itself every 5 minutes.
All labels are in Portuguese. Reasons recorded by the bot (in English) are shown as details.
"""
from __future__ import annotations

import argparse
import html
import math
import re
import time
import webbrowser
from pathlib import Path

import numpy as np
import pandas as pd

from . import analytics as A
from . import config as C

REFRESH_S = 300
LEVEL_CSS = {A.GREEN: "ok", A.AMBER: "warn", A.RED: "bad"}
LEVEL_PT = {A.GREEN: "Verde", A.AMBER: "Amarelo", A.RED: "Vermelho"}
EXIT_PT = {"stop": "Stop atingido dentro da vela (preço do stop)",
           "stop_gap": "Vela abriu abaixo do stop (preço de abertura)",
           "target": "Alvo atingido",
           "signal": "Sinal de saída no fecho -> venda na abertura seguinte",
           "time": "Limite de tempo",
           "signal_late": "Sinal de saída, executado com atraso (bot desligado)",
           "time_late": "Limite de tempo, executado com atraso"}
SHIELD_PT = {"min_notional": "Abaixo do mínimo nocional", "max_positions": "Máximo de posições",
             "duplicate": "Posição repetida na mesma moeda", "leverage": "Alavancagem acima de 3x",
             "no_stop": "Ordem sem stop", "min_qty": "Abaixo da quantidade mínima",
             "halted": "Bot parado", "daily_loss": "Perda diária >= 3%",
             "consecutive_losses": "3 perdas seguidas", "stale_feed": "Dados desatualizados",
             "fetch_failures": "Falhas de leitura de dados", "mismatch": "Capital/posições não batem",
             "exception": "Erro inesperado no código"}
TYPE_PT = {"BLOCK": "Bloqueio", "HALT": "PARAGEM (HALT)", "RESET": "Retoma (HALT apagado)",
           "FETCH_FAILURE": "Falha de leitura"}
_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://")


def esc(x) -> str:
    """HTML-escape, and drop any URL scheme so the page never contains a link to the outside."""
    return html.escape(_URL.sub("", "" if x is None else str(x)))


def num(x, d=2, sign=False) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "-"
    s = f"{x:+,.{d}f}" if sign else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",")


def price(x) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "-"
    return f"{float(x):.6g}".replace(".", ",")


def pct(x, d=1, sign=False) -> str:
    return "-" if x is None or not math.isfinite(x) else num(100 * x, d, sign) + "%"


def ts(x) -> str:
    if not x:
        return "-"
    try:
        return pd.Timestamp(x).tz_convert("UTC").strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return esc(x)


# ------------------------------------------------------------------ chart
def equity_svg(cfg: C.Config, eq: pd.DataFrame) -> str:
    if len(eq) < 2:
        return ('<p class="muted">Ainda não há histórico de capital: o gráfico aparece depois das '
                'primeiras horas de funcionamento.</p>')
    s = eq.set_index("time")["equity_mark_usdt"].astype(float)
    if len(s) > 1500:
        s = s.iloc[np.linspace(0, len(s) - 1, 1500).astype(int)]
    v = np.r_[cfg.starting_equity_usdt, s.to_numpy()]
    t = np.r_[s.index[0].value, [x.value for x in s.index]].astype(float)
    peak = np.maximum.accumulate(v)
    W, Hh, L, R, T, B = 900, 300, 56, 12, 14, 34
    lo, hi = float(min(v.min(), peak.min())), float(max(v.max(), peak.max()))
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    pad = 0.05 * (hi - lo)
    lo, hi = lo - pad, hi + pad
    t0, t1 = t[0], t[-1] if t[-1] > t[0] else t[0] + 1

    def X(x):
        return L + (x - t0) / (t1 - t0) * (W - L - R)

    def Y(y):
        return T + (hi - y) / (hi - lo) * (Hh - T - B)

    line = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in zip(t, v))
    shade = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in zip(t, peak)) + " " + \
        " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in zip(t[::-1], v[::-1]))
    grid = []
    for k in range(5):
        y = lo + (hi - lo) * k / 4
        grid.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(y):.1f}" y2="{Y(y):.1f}" class="grid"/>'
                    f'<text x="{L - 6}" y="{Y(y) + 4:.1f}" text-anchor="end">{num(y, 0)}</text>')
    for k in range(4):
        x = t0 + (t1 - t0) * k / 3
        grid.append(f'<text x="{X(x):.1f}" y="{Hh - 12}" text-anchor="middle">'
                    f'{pd.Timestamp(int(x), tz="UTC"):%Y-%m-%d}</text>')
    base = Y(cfg.starting_equity_usdt)
    return (f'<svg viewBox="0 0 {W} {Hh}" role="img" aria-label="Curva de capital em papel com '
            f'drawdown sombreado" class="chart">{"".join(grid)}'
            f'<line x1="{L}" x2="{W - R}" y1="{base:.1f}" y2="{base:.1f}" class="base"/>'
            f'<polygon points="{shade}" class="dd"/>'
            f'<polyline points="{line}" class="eq"/></svg>'
            '<p class="legend"><span class="sw eqc"></span> capital marcado a mercado (USDT) '
            '<span class="sw ddc"></span> drawdown (distância ao máximo anterior) '
            '<span class="sw basec"></span> capital inicial</p>')


# ------------------------------------------------------------------ page
def _pnl_since(cfg, eq: pd.DataFrame, now_eq: float, since) -> float:
    if len(eq) == 0:
        return 0.0
    before = eq[eq["time"] < since]
    ref = float(before["equity_mark_usdt"].iloc[-1]) if len(before) else cfg.starting_equity_usdt
    return now_eq - ref


def status(cfg: C.Config, data: dict, now: pd.Timestamp) -> tuple[str, str, str]:
    """(css class, title, detail)."""
    if cfg.halt_file.exists():
        try:
            why = cfg.halt_file.read_text(encoding="utf-8").splitlines()[0]
        except Exception:
            why = "ficheiro HALT presente"
        return ("bad", "PARADO (HALT)", f"{why}. Leia os eventos do Shield abaixo e apague o "
                                       "ficheiro HALT para retomar.")
    if not data["equity"]:
        return ("warn", "Ainda não iniciado", "Não há registos: inicie o bot com run_bot.ps1.")
    last = pd.Timestamp(data["equity"][-1]["time"])
    st = data["state"]
    bars = [now]
    if now - last > pd.Timedelta(hours=2):
        return ("warn", "DESATUALIZADO", f"Último ciclo do bot em {ts(last)} (há mais de 2 horas): "
                                         "o PC esteve a dormir ou o bot está desligado.")
    if data["equity"][-1].get("status") == "fetch_failed":
        return ("warn", "A funcionar, com falha de dados", "A última leitura de dados falhou; o "
                                                           "bot volta a tentar em 5 minutos.")
    del bars
    return ("ok", "A funcionar", f"Último ciclo {ts(last)}; {len(st.get('positions', {}))} "
                                 "posição(ões) aberta(s).")


def build(cfg: C.Config, now: pd.Timestamp | None = None) -> str:
    data = A.load_all(cfg)
    now = now or pd.Timestamp.now(tz="UTC")
    ref_now = A.end_time(data) if data["equity"] else now
    tr = A.closed_trades(data["trades"])
    eq = A.equity_frame(data["equity"])
    st = data["state"]
    fx = cfg.eur_usd_estimate
    now_eq = float(eq["equity_mark_usdt"].iloc[-1]) if len(eq) else cfg.starting_equity_usdt
    s_all = A.summary(tr)
    dd = A.max_drawdown(np.r_[cfg.starting_equity_usdt, eq["equity_mark_usdt"].astype(float)]) \
        if len(eq) else 0.0
    cur_streak, _ = A.streaks(tr["pnl"].to_numpy()) if len(tr) else (0, 0)
    cur_streak = int(st.get("shield", {}).get("consecutive_losses", cur_streak))
    lim = cfg.shield.consecutive_losses_halt
    day0 = ref_now.floor("D")
    pnl_today = now_eq - st["shield"]["day_start_equity"] if st.get("shield", {}).get(
        "day_start_equity") and st["shield"].get("day") == str(day0.date()) else \
        _pnl_since(cfg, eq, now_eq, day0)
    pnl7 = _pnl_since(cfg, eq, now_eq, ref_now - pd.Timedelta(days=7))
    pnl30 = _pnl_since(cfg, eq, now_eq, ref_now - pd.Timedelta(days=30))
    pnl_tot = now_eq - cfg.starting_equity_usdt
    css, title, detail = status(cfg, data, now)
    fresh = data["equity"][-1]["time"] if data["equity"] else None

    def card(label, main, sub="", cls=""):
        return (f'<div class="card {cls}"><div class="lab">{esc(label)}</div>'
                f'<div class="val">{main}</div><div class="sub">{sub}</div></div>')

    def money(x):
        cls = "pos" if x > 1e-12 else "neg" if x < -1e-12 else ""
        return (f'<span class="{cls}">{num(x / fx, 2, True)} EUR</span>',
                f"{num(x, 2, True)} USDT")

    cards = [card("Capital em papel", f"{num(now_eq / fx)} EUR",
                  f"{num(now_eq)} USDT (câmbio ESTIMATE {num(fx, 2)})")]
    for lab, x in (("Resultado hoje (UTC)", pnl_today), ("Últimos 7 dias", pnl7),
                   ("Últimos 30 dias", pnl30), ("Desde o início", pnl_tot)):
        a, b = money(x)
        cards.append(card(lab, a, b))
    cards.append(card("Posições abertas", str(len(st.get("positions", {}))),
                      f"limite {cfg.shield.max_open_positions}"))
    cards.append(card("Trades ganhadores", pct(s_all["win_rate"]),
                      f"{s_all['trades']} trades fechados (backtest "
                      f"{pct(cfg.backtest_reference.get('oos_win_rate', math.nan))})"))
    cards.append(card("Drawdown máximo", pct(dd), "desde o início, marcado a mercado",
                      "warn" if dd > 0.15 else ""))
    cards.append(card("Perdas seguidas", f"{cur_streak} / {lim}",
                      "o Shield para o bot ao chegar ao limite",
                      "bad" if cur_streak >= lim else "warn" if cur_streak == lim - 1 else ""))

    # open positions
    pos = list(st.get("positions", {}).values())
    if pos:
        rows = "".join(
            f"<tr><td>{esc(p['symbol'])}</td><td>{'Compra' if p['side'] == 1 else 'Venda'}</td>"
            f"<td>{price(p['entry'])}</td><td>{price(p['stop'])}</td>"
            f"<td>{num(p['qty'], 6)}<br><span class='muted'>{num(p['notional'])} USDT</span></td>"
            f"<td class=\"{'pos' if p.get('unrealized', 0) > 0 else 'neg'}\">"
            f"{num(p.get('unrealized', 0.0), 2, True)} USDT</td>"
            f"<td>{num(p.get('funding_usdt', p.get('funding', 0) * p['notional']), 4, True)}"
            f" USDT</td><td>{ts(pd.Timestamp(p['entry_time'], unit='ns', tz='UTC'))}</td></tr>"
            for p in pos)
        pos_html = ("<table><thead><tr><th>Moeda</th><th>Lado</th><th>Entrada</th><th>Stop</th>"
                    "<th>Tamanho</th><th>P&amp;L não realizado</th><th>Funding acumulado</th>"
                    f"<th>Aberta em</th></tr></thead><tbody>{rows}</tbody></table>")
    else:
        pos_html = '<p class="muted">Nenhuma posição aberta.</p>'

    # last 50 trades
    opens = {r["id"]: r for r in data["trades"] if r.get("event") == "open"}
    last = tr.iloc[::-1].head(50)
    if len(last):
        rows = []
        for _, r in last.iterrows():
            o = opens.get(r["id"], {})
            rows.append(
                f"<tr><td>{ts(r['entry_time'])}<br>{ts(r['exit_time'])}</td>"
                f"<td>{esc(r['symbol'])}</td><td>{price(r['entry'])}<br>{price(r['exit'])}</td>"
                f"<td class=\"{'pos' if r['pnl'] > 0 else 'neg'}\">{num(r['R'], 2, True)}R<br>"
                f"{num(r['pnl'], 2, True)} USDT</td>"
                f"<td>{esc(_entry_pt(cfg, o))}</td>"
                f"<td>{esc(EXIT_PT.get(r['reason'], r['reason']))}</td></tr>")
        trades_html = ("<table><thead><tr><th>Entrada / saída</th><th>Moeda</th>"
                       "<th>Preço entrada / saída</th><th>Resultado</th><th>Motivo da entrada</th>"
                       f"<th>Motivo da saída</th></tr></thead><tbody>{''.join(rows)}</tbody></table>")
    else:
        trades_html = '<p class="muted">Ainda não há trades fechados.</p>'

    # shield
    ev = [e for e in data["shield"] if e["type"] in ("BLOCK", "HALT", "RESET", "FETCH_FAILURE")]
    if ev:
        rows = "".join(
            f"<tr class=\"{'badrow' if e['type'] == 'HALT' else ''}\"><td>{ts(e['time'])}</td>"
            f"<td>{esc(TYPE_PT.get(e['type'], e['type']))}</td>"
            f"<td>{esc(SHIELD_PT.get(e.get('rule', ''), ''))}"
            f"{'<br>' if e.get('rule') in SHIELD_PT else ''}<span class='muted'>"
            f"{esc(e.get('symbol', ''))} {esc(e.get('reason', ''))}</span></td></tr>"
            for e in ev[::-1][:50])
        cnt = A.shield_counts(data["shield"])
        summ = ", ".join(f"{esc(TYPE_PT.get(k.split(' ')[0], k))}"
                         f"{' - ' + esc(SHIELD_PT.get(k.split(' ', 1)[1], k.split(' ', 1)[1])) if ' ' in k else ''}"
                         f": {v}" for k, v in sorted(cnt.items()))
        shield_html = (f"<p>Total: {summ}</p><table><thead><tr><th>Quando</th><th>Tipo</th>"
                       f"<th>Motivo</th></tr></thead><tbody>{rows}</tbody></table>")
    else:
        shield_html = '<p class="muted">Nenhum bloqueio nem paragem registados.</p>'

    # health
    h = A.health(cfg, tr, eq, ref_now)
    ref = cfg.backtest_reference
    e_txt = f"{num(s_all['exp_R'], 3, True)}R" if s_all["trades"] else "-"
    exp_line = (f"Expectativa em papel: <b>{e_txt}</b> por trade em "
                f"{s_all['trades']} trades"
                + (f" (intervalo 95% {num(s_all['exp_R_lo'], 2, True)} a "
                   f"{num(s_all['exp_R_hi'], 2, True)}R)" if s_all["trades"] >= 2 else "")
                + f". Backtest (fora da amostra): <b>{num(ref.get('oos_exp_R', math.nan), 3, True)}"
                  f"R</b>; 2.º ano só {num(ref.get('oos_year2_exp_R', math.nan), 3, True)}R.")
    checks = "".join(f'<li class="{LEVEL_CSS[c["level"]]}"><span class="dot"></span>'
                     f'<b>{LEVEL_PT[c["level"]]}</b> - {esc(c["text"])}</li>' for c in h["checks"])
    health_html = (f'<div class="health {LEVEL_CSS[h["level"]]}"><div class="big">'
                   f'Estado geral: {LEVEL_PT[h["level"]]}</div><p>{exp_line}</p>'
                   f'<ul class="checks">{checks}</ul><p class="muted">Regras do cartão S16: '
                   'enfraquecimento = expectativa a 12 meses abaixo de +0,05R, mais de 15 perdas '
                   'seguidas, ou drawdown acima de 28%; parar = drawdown acima de 30% ou '
                   'expectativa dos últimos 100 trades abaixo de zero.</p></div>')

    u = st.get("universe") or {}
    uni_html = (f"<p>Mês {esc(u.get('month', '-'))}, atualizado em {ts(u.get('computed_at'))}"
                "</p><ol class='uni'>" + "".join(f"<li>{esc(x)}</li>" for x in
                                                 u.get("symbols", [])) + "</ol>") if u else \
        '<p class="muted">O universo ainda não foi calculado (é calculado no início de cada mês '
    if not u:
        uni_html += "e no primeiro arranque).</p>"

    est = "" if st.get("filters_fetched_at") else (
        "<div class='note'>Mínimos nocionais são ESTIMATE (BTC 100, ETH 20, outros 5 USDT) até à "
        "primeira leitura do exchangeInfo da Binance.</div>")
    from .strategy import is_approved
    obs_html = "" if is_approved(cfg.setup_id) else (
        f"<div class='paper obs'>SÓ OBSERVAÇÃO - o setup {esc(cfg.setup_id)} NÃO foi aprovado "
        "pelo LAB. Serve para aprender, nunca para dinheiro real.</div>")
    warn = ("o S16 enfraqueceu no 2.º ano fora da amostra (+0,05R por trade)"
            if cfg.setup_id == "S16" else
            f"o {esc(cfg.setup_id)} NÃO passou nos testes do LAB")
    return f"""<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{REFRESH_S}">
<title>Bot em papel - painel</title>
<style>{CSS}</style>
</head>
<body>
<header class="banner {css}">
  <div class="paper">PAPER ONLY - SÓ PAPEL, SEM DINHEIRO REAL</div>
  {obs_html}
  <div class="st"><b>{esc(title)}</b> - {esc(detail)}</div>
  <div class="fresh">Dados de {ts(fresh)} · painel gerado {ts(now)} · atualiza a cada 5 min
   · setup {esc(cfg.setup_id)}</div>
</header>
<main>
{est}
<section class="cards">{''.join(cards)}</section>
<section><h2>Curva de capital</h2><div class="tw">{equity_svg(cfg, eq)}</div></section>
<section><h2>Saúde do setup</h2>{health_html}</section>
<section><h2>Posições abertas</h2><div class="tw">{pos_html}</div></section>
<section><h2>Últimos 50 trades</h2><div class="tw">{trades_html}</div></section>
<section><h2>Eventos do Shield</h2><div class="tw">{shield_html}</div></section>
<section><h2>Universo top-10</h2>{uni_html}</section>
<footer class="muted">Gerado a partir de logs/ e state.json. Este painel não envia nada a lado
 nenhum e não precisa de internet. Avisos: só papel; {warn}; mínimos nocionais são estimativas até à primeira leitura ao vivo.</footer>
</main>
</body>
</html>
"""


def _entry_pt(cfg, o: dict) -> str:
    if cfg.setup_id == "S16":
        return ("Fecho 4h acima da SMA50 > SMA200 e funding liquidado negativo (1.º sinal da "
                "subida)")
    return f"Sinal {cfg.setup_id}" if o else "-"


CSS = """
:root{--bg:#f6f7f9;--fg:#1d2330;--mut:#667085;--card:#fff;--line:#e3e6eb;--ok:#127a3a;
--okb:#e6f4ea;--warn:#9a6700;--warnb:#fff5d6;--bad:#b42318;--badb:#fde8e7;--eq:#1f5fbf;
--dd:rgba(180,35,24,.22)}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--fg:#e8eaee;--mut:#9aa3b2;--card:#1b2029;
--line:#2c3340;--ok:#5fd08a;--okb:#16301f;--warn:#f0c04a;--warnb:#33290f;--bad:#ff7a6e;
--badb:#3a1714;--eq:#7fb0ff;--dd:rgba(255,122,110,.25)}}
.obs{color:var(--bad);font-weight:700}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:12px 16px 40px}
.banner{padding:12px 16px;border-bottom:3px solid var(--line)}
.banner.ok{background:var(--okb);border-color:var(--ok)}.banner.warn{background:var(--warnb);
border-color:var(--warn)}.banner.bad{background:var(--badb);border-color:var(--bad)}
.paper{font-weight:800;letter-spacing:.04em;font-size:17px}.st{margin-top:4px}
.fresh{color:var(--mut);font-size:13px;margin-top:4px}
h2{font-size:18px;margin:26px 0 10px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;
margin-top:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.card.warn{border-color:var(--warn)}.card.bad{border-color:var(--bad);background:var(--badb)}
.lab{color:var(--mut);font-size:13px}.val{font-size:22px;font-weight:700;margin:2px 0}
.sub{color:var(--mut);font-size:12px}
.pos{color:var(--ok)}.neg{color:var(--bad)}.muted{color:var(--mut)}
.chart{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
.chart text{fill:var(--mut);font-size:11px}.chart .grid{stroke:var(--line)}
.chart .base{stroke:var(--mut);stroke-dasharray:4 4}.chart .eq{fill:none;stroke:var(--eq);
stroke-width:2}.chart .dd{fill:var(--dd);stroke:none}
.legend{font-size:12px;color:var(--mut)}.sw{display:inline-block;width:12px;height:12px;
vertical-align:-2px;margin:0 4px 0 10px;border-radius:2px}.eqc{background:var(--eq)}
.ddc{background:var(--dd)}.basec{border-top:2px dashed var(--mut);height:0}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;background:var(--card);font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--mut);font-weight:600;white-space:nowrap}tr.badrow td{background:var(--badb)}
.health{background:var(--card);border:1px solid var(--line);border-left:6px solid var(--mut);
border-radius:10px;padding:10px 14px}.health.ok{border-left-color:var(--ok)}
.health.warn{border-left-color:var(--warn)}.health.bad{border-left-color:var(--bad)}
.big{font-size:18px;font-weight:700}.checks{list-style:none;padding:0}
.checks li{margin:6px 0}.dot{display:inline-block;width:11px;height:11px;border-radius:50%;
margin-right:6px;background:var(--mut)}.checks .ok .dot{background:var(--ok)}
.checks .warn .dot{background:var(--warn)}.checks .bad .dot{background:var(--bad)}
.uni{columns:2;padding-left:22px}.note{background:var(--warnb);border:1px solid var(--warn);
border-radius:8px;padding:8px 12px;margin-top:12px;font-size:14px}
footer{margin-top:30px;font-size:12px}
@media (max-width:520px){.val{font-size:19px}.chart{min-width:620px}main{padding:10px 16px 30px}.uni{columns:1}}
"""


def write(cfg: C.Config, out: Path | None = None, now=None) -> Path:
    out = Path(out) if out else Path(cfg.logs_dir).parent / "dashboard.html"
    text = build(cfg, now)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the local paper-trading dashboard")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--watch", action="store_true", help="rebuild every 5 minutes")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    a = ap.parse_args(argv)
    cfg = C.load(a.config)
    p = write(cfg, a.out)
    print(f"painel: {p}")
    if not a.no_open:
        webbrowser.open(p.resolve().as_uri())
    while a.watch:
        time.sleep(REFRESH_S)
        p = write(cfg, a.out)
        print(f"painel atualizado: {time.strftime('%H:%M')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
