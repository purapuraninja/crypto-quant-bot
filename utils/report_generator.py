"""
utils/report_generator.py — Generate bot_performance.html dari pnl_store.json.

Usage:
  python utils/report_generator.py
  python utils/report_generator.py report.html
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from utils.eval_bot import calculate_metrics


def _load_store() -> Dict:
    path = _ROOT / "pnl_store.json"
    if not path.exists():
        return {"open_positions": {}, "closed_trades": [], "stats": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _symbol_breakdown(closed: List[Dict]) -> List[Dict]:
    sym: Dict[str, Dict] = {}
    for t in closed:
        s = t.get("symbol", "?")
        p = t.get("pnl_usdt", 0.0)
        if s not in sym:
            sym[s] = {"symbol": s, "trades": 0, "pnl": 0.0, "wins": 0, "losses": 0}
        sym[s]["trades"] += 1
        sym[s]["pnl"] += p
        if p > 0: sym[s]["wins"] += 1
        elif p < 0: sym[s]["losses"] += 1
    rows = list(sym.values())
    for r in rows: r["pnl"] = round(r["pnl"], 4)
    rows.sort(key=lambda x: x["pnl"], reverse=True)
    return rows


def _exit_breakdown(closed: List[Dict]) -> List[Dict]:
    rm: Dict[str, int] = {}
    for t in closed:
        r = t.get("reason", t.get("close_reason", "unknown"))
        rm[r] = rm.get(r, 0) + 1
    return sorted([{"reason": k, "count": v} for k, v in rm.items()],
                  key=lambda x: x["count"], reverse=True)


def _equity_curve(closed: List[Dict]) -> List[Dict]:
    """Cumulative PnL over time for chart."""
    points, cumulative = [], 0.0
    for t in closed:
        cumulative += t.get("pnl_usdt", 0.0)
        dt = t.get("close_time", "")[:16]
        points.append({"t": dt, "v": round(cumulative, 4)})
    return points


def _daily_pnl(closed: List[Dict]) -> Dict[str, float]:
    """PnL grouped by date."""
    daily: Dict[str, float] = {}
    for t in closed:
        d = t.get("close_time", "")[:10]
        if d:
            daily[d] = round(daily.get(d, 0.0) + t.get("pnl_usdt", 0.0), 4)
    return dict(sorted(daily.items()))


def generate_html(store: Dict, out_path: Path) -> None:
    closed    = store.get("closed_trades", [])
    open_pos  = store.get("open_positions", {})
    m         = calculate_metrics(store)
    now_str   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total     = m["total_trades"]

    # Date range
    times = [t.get("close_time", "") for t in closed if t.get("close_time")]
    date_range = ""
    if times:
        try:
            dts = [datetime.strptime(d, "%Y-%m-%d %H:%M:%S") for d in times]
            date_range = f"{min(dts).strftime('%d %b')} &ndash; {max(dts).strftime('%d %b %Y')}"
        except Exception:
            date_range = f"{times[0][:10]} &ndash; {times[-1][:10]}"

    best  = max(closed, key=lambda t: t.get("pnl_usdt", 0)) if closed else {}
    worst = min(closed, key=lambda t: t.get("pnl_usdt", 0)) if closed else {}

    sym_rows  = _symbol_breakdown(closed)
    exit_rows = _exit_breakdown(closed)
    equity    = _equity_curve(closed)
    daily     = _daily_pnl(closed)
    recent    = list(reversed(closed[-15:])) if closed else []

    # Chart data (JSON)
    eq_labels = json.dumps([p["t"] for p in equity])
    eq_values = json.dumps([p["v"] for p in equity])
    dl_labels = json.dumps(list(daily.keys()))
    dl_values = json.dumps(list(daily.values()))
    dl_colors = json.dumps(["#3fb950" if v >= 0 else "#f85149" for v in daily.values()])

    sym_chart_labels = json.dumps([r["symbol"] for r in sym_rows])
    sym_chart_values = json.dumps([r["pnl"] for r in sym_rows])
    sym_chart_colors = json.dumps(["#3fb950" if r["pnl"] >= 0 else "#f85149" for r in sym_rows])

    # Helpers
    def pc(v): return "pos" if v > 0 else ("neg" if v < 0 else "neu")
    def pf(v): return f"+${v:.4f}" if v > 0 else f"-${abs(v):.4f}"
    def side_cls(a): return "long" if a == "LONG" else "short"

    # ── Open positions table ──────────────────────────────────
    open_html = ""
    for sym, pos in open_pos.items():
        act  = pos.get("action", "?")
        entry = pos.get("entry_price", 0)
        sl   = pos.get("stop_loss", 0)
        lev  = pos.get("leverage", 1)
        dca  = pos.get("dca_count", 0)
        ot   = pos.get("open_time", "")[:16]
        sl_txt = f"{sl:.6f}" if sl > 0 else "NO SL"
        sl_cls = "warn-txt" if sl == 0 else "neu"
        dca_txt = f"DCA x{dca}" if dca > 0 else "&mdash;"
        open_html += f"""
            <tr>
              <td class="sym">{sym}</td>
              <td><span class="badge {side_cls(act)}">{act}</span></td>
              <td>{entry:.6f}</td>
              <td class="{sl_cls}">{sl_txt}</td>
              <td>{lev}x</td>
              <td class="{'accent' if dca>0 else 'muted'}">{dca_txt}</td>
              <td class="muted">{ot}</td>
            </tr>"""
    if not open_html:
        open_html = '<tr><td colspan="7" class="empty">No open positions</td></tr>'

    # ── Symbol breakdown table ────────────────────────────────
    sym_html = ""
    for r in sym_rows:
        p = r["pnl"]
        wr = round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0
        sym_html += f"""
            <tr>
              <td class="sym">{r['symbol']}</td>
              <td>{r['trades']}</td>
              <td class="{'pos' if p>=0 else 'neg'}">{pf(p)}</td>
              <td>{r['wins']}W / {r['losses']}L</td>
              <td>
                <div class="mini-bar-wrap">
                  <div class="mini-bar {'pos-bar' if wr>=50 else 'neg-bar'}" style="width:{wr}%"></div>
                </div>
                {wr:.0f}%
              </td>
            </tr>"""

    # ── Exit reason table ─────────────────────────────────────
    exit_html = ""
    for r in exit_rows:
        pct = round(r["count"] / total * 100, 1) if total else 0
        reason = r["reason"]
        cls = "pos" if ("profit" in reason or "tp" in reason.lower()) else \
              "neg" if ("stop" in reason.lower() or "sl" in reason.lower()) else "muted"
        exit_html += f"""
            <tr>
              <td class="{cls}">{reason}</td>
              <td>{r['count']}</td>
              <td>
                <div class="mini-bar-wrap">
                  <div class="mini-bar {'pos-bar' if cls=='pos' else 'neg-bar'}" style="width:{pct}%"></div>
                </div>
                {pct}%
              </td>
            </tr>"""

    # ── Recent trades table ───────────────────────────────────
    recent_html = ""
    for t in recent:
        sym  = t.get("symbol", "?")
        act  = t.get("action", "?")
        pnlv = t.get("pnl_usdt", 0.0)
        roi  = t.get("roi_pct", 0.0)
        rsn  = t.get("reason", t.get("close_reason", "?"))
        ct   = t.get("close_time", "")[:16]
        lev  = t.get("leverage", "?")
        recent_html += f"""
            <tr>
              <td class="muted">{ct}</td>
              <td class="sym">{sym}</td>
              <td><span class="badge {side_cls(act)}">{act}</span></td>
              <td class="{pc(pnlv)}">{pf(pnlv)}</td>
              <td class="{pc(roi)}">{'+' if roi>0 else ''}{roi:.2f}%</td>
              <td class="muted">{lev}x</td>
              <td class="muted">{rsn}</td>
            </tr>"""
    if not recent_html:
        recent_html = '<tr><td colspan="7" class="empty">No closed trades yet</td></tr>'

    # ── Metric helpers ────────────────────────────────────────
    pf_val = m["profit_factor"]
    pf_cls = "pos" if pf_val >= 1.5 else ("warn-txt" if pf_val >= 1.0 else "neg")
    wr     = m["win_rate_pct"]
    wr_cls = "pos" if wr >= 60 else ("warn-txt" if wr >= 50 else "neg")
    total_pnl = m["total_pnl_usdt"]

    best_sym  = best.get("symbol", "&mdash;")
    best_pnl  = best.get("pnl_usdt", 0)
    best_roi  = best.get("roi_pct", 0)
    best_act  = best.get("action", "")
    worst_sym = worst.get("symbol", "&mdash;")
    worst_pnl = worst.get("pnl_usdt", 0)
    worst_roi = worst.get("roi_pct", 0)
    worst_act = worst.get("action", "")

    open_count = len(open_pos)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>Bot Performance</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {{
  --bg:       #090c10;
  --surface:  #0d1117;
  --card:     #161b22;
  --border:   #21262d;
  --border2:  #30363d;
  --text:     #e6edf3;
  --muted:    #7d8590;
  --pos:      #3fb950;
  --neg:      #f85149;
  --warn:     #d29922;
  --accent:   #58a6ff;
  --long:     #3fb950;
  --short:    #f85149;
  --radius:   10px;
  --font:     'Segoe UI', system-ui, sans-serif;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--bg); color: var(--text); font-family: var(--font); font-size: 14px; line-height: 1.5; }}

/* ── Layout ── */
.shell {{ display: grid; grid-template-rows: auto 1fr; min-height: 100vh; }}
.topbar {{ background: var(--surface); border-bottom: 1px solid var(--border); padding: 0 28px; display: flex; align-items: center; gap: 20px; height: 56px; position: sticky; top: 0; z-index: 100; }}
.topbar .logo {{ font-size: 15px; font-weight: 700; color: var(--accent); letter-spacing: .5px; }}
.topbar .logo span {{ color: var(--muted); font-weight: 400; }}
.topbar-right {{ margin-left: auto; display: flex; align-items: center; gap: 16px; font-size: 12px; color: var(--muted); }}
.pulse {{ width: 8px; height: 8px; border-radius: 50%; background: var(--pos); animation: pulse 2s infinite; display: inline-block; margin-right: 4px; }}
@keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:.3; }} }}

.main {{ padding: 24px 28px; max-width: 1440px; margin: 0 auto; }}

/* ── Section ── */
.section-title {{ font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px; color: var(--muted); margin: 28px 0 12px; border-left: 3px solid var(--accent); padding-left: 10px; }}

/* ── KPI Grid ── */
.kpi-grid {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }}
.kpi {{ background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; position: relative; overflow: hidden; }}
.kpi::before {{ content:''; position:absolute; top:0; left:0; right:0; height:2px; }}
.kpi.pos-kpi::before {{ background: var(--pos); }}
.kpi.neg-kpi::before {{ background: var(--neg); }}
.kpi.warn-kpi::before {{ background: var(--warn); }}
.kpi.blue-kpi::before {{ background: var(--accent); }}
.kpi .kpi-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 6px; }}
.kpi .kpi-val {{ font-size: 26px; font-weight: 700; line-height: 1; }}
.kpi .kpi-sub {{ font-size: 11px; color: var(--muted); margin-top: 6px; }}
.kpi .kpi-bar {{ height: 3px; border-radius: 2px; background: var(--border); margin-top: 10px; overflow: hidden; }}
.kpi .kpi-bar-fill {{ height: 100%; border-radius: 2px; transition: width .4s; }}

/* ── Chart row ── */
.chart-row {{ display: grid; grid-template-columns: 2fr 1fr; gap: 14px; }}
.chart-row-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }}
.chart-card {{ background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px; }}
.chart-card .chart-title {{ font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 14px; }}
.chart-wrap {{ position: relative; }}

/* ── Tables ── */
.table-card {{ background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ background: var(--surface); color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 1px; padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); font-weight: 600; }}
td {{ padding: 9px 14px; border-bottom: 1px solid var(--border); font-size: 13px; }}
tbody tr:last-child td {{ border-bottom: none; }}
tbody tr:hover {{ background: rgba(255,255,255,.025); }}
.empty {{ text-align: center; color: var(--muted); padding: 28px; }}

/* ── Color classes ── */
.pos {{ color: var(--pos); }}
.neg {{ color: var(--neg); }}
.neu {{ color: var(--muted); }}
.accent {{ color: var(--accent); }}
.muted {{ color: var(--muted); }}
.warn-txt {{ color: var(--warn); }}
.sym {{ color: var(--accent); font-weight: 600; font-size: 12px; }}

/* ── Badges ── */
.badge {{ display: inline-block; font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 4px; letter-spacing: .5px; }}
.badge.long {{ background: rgba(63,185,80,.15); color: var(--pos); }}
.badge.short {{ background: rgba(248,81,73,.15); color: var(--neg); }}

/* ── Mini bar ── */
.mini-bar-wrap {{ display: inline-block; width: 60px; height: 4px; background: var(--border); border-radius: 2px; vertical-align: middle; margin-right: 6px; overflow: hidden; }}
.mini-bar {{ height: 100%; border-radius: 2px; }}
.pos-bar {{ background: var(--pos); }}
.neg-bar {{ background: var(--neg); }}

/* ── Open positions grid ── */
.pos-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }}
.pos-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; }}
.pos-card .ps {{ font-size: 13px; font-weight: 700; color: var(--accent); }}
.pos-card .pd {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}
.pos-card .pe {{ font-size: 12px; margin-top: 8px; }}

/* ── Responsive ── */
@media(max-width:1100px) {{ .kpi-grid {{ grid-template-columns: repeat(3,1fr); }} }}
@media(max-width:750px) {{ .kpi-grid {{ grid-template-columns: repeat(2,1fr); }} .chart-row,.chart-row-3 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="shell">

<!-- Topbar -->
<header class="topbar">
  <div class="logo">AI QUANT BOT <span>/ Performance Dashboard</span></div>
  <div class="topbar-right">
    <span><span class="pulse"></span>Live</span>
    <span>{now_str}</span>
    <span style="color:var(--accent)">{total} trades | {date_range}</span>
    <span style="font-size:11px;color:var(--border2)">Auto-refresh 5m</span>
  </div>
</header>

<main class="main">

<!-- ── KPI Row ─────────────────────────────────────── -->
<div class="section-title">Core Metrics</div>
<div class="kpi-grid">

  <div class="kpi {'pos-kpi' if total_pnl>=0 else 'neg-kpi'}">
    <div class="kpi-label">Total Realized PnL</div>
    <div class="kpi-val {pc(total_pnl)}">{pf(total_pnl)}</div>
    <div class="kpi-sub">from {total} closed trades</div>
  </div>

  <div class="kpi {'pos-kpi' if wr>=60 else ('warn-kpi' if wr>=50 else 'neg-kpi')}">
    <div class="kpi-label">Win Rate</div>
    <div class="kpi-val {wr_cls}">{wr:.1f}%</div>
    <div class="kpi-sub">{m['win_count']}W &nbsp;/&nbsp; {m['loss_count']}L</div>
    <div class="kpi-bar"><div class="kpi-bar-fill {'pos-bar' if wr>=50 else 'neg-bar'}" style="width:{wr}%"></div></div>
  </div>

  <div class="kpi {'pos-kpi' if pf_val>=1.5 else ('warn-kpi' if pf_val>=1.0 else 'neg-kpi')}">
    <div class="kpi-label">Profit Factor</div>
    <div class="kpi-val {pf_cls}">{pf_val:.2f}</div>
    <div class="kpi-sub">Target &ge; 1.5</div>
    <div class="kpi-bar"><div class="kpi-bar-fill {'pos-bar' if pf_val>=1.5 else 'neg-bar'}" style="width:{min(pf_val/2*100,100):.0f}%"></div></div>
  </div>

  <div class="kpi blue-kpi">
    <div class="kpi-label">Avg ROI / Trade</div>
    <div class="kpi-val {pc(m['avg_roi_pct'])}">{m['avg_roi_pct']:+.2f}%</div>
    <div class="kpi-sub">incl. leverage</div>
  </div>

  <div class="kpi pos-kpi">
    <div class="kpi-label">Best Trade</div>
    <div class="kpi-val pos">{pf(best_pnl)}</div>
    <div class="kpi-sub">{best_sym} {best_act} &nbsp;{best_roi:+.1f}%</div>
  </div>

  <div class="kpi neg-kpi">
    <div class="kpi-label">Worst Trade</div>
    <div class="kpi-val neg">{pf(worst_pnl)}</div>
    <div class="kpi-sub">{worst_sym} {worst_act} &nbsp;{worst_roi:+.1f}%</div>
  </div>

</div>

<!-- ── Charts Row 1 ────────────────────────────────── -->
<div class="section-title">Equity & Daily PnL</div>
<div class="chart-row">
  <div class="chart-card">
    <div class="chart-title">Equity Curve (Cumulative PnL)</div>
    <div class="chart-wrap" style="height:220px">
      <canvas id="equityChart"></canvas>
    </div>
  </div>
  <div class="chart-card">
    <div class="chart-title">Daily PnL</div>
    <div class="chart-wrap" style="height:220px">
      <canvas id="dailyChart"></canvas>
    </div>
  </div>
</div>

<!-- ── Charts Row 2 ────────────────────────────────── -->
<div class="section-title">Distribution</div>
<div class="chart-row-3">
  <div class="chart-card">
    <div class="chart-title">Win / Loss</div>
    <div class="chart-wrap" style="height:180px">
      <canvas id="donutChart"></canvas>
    </div>
  </div>
  <div class="chart-card">
    <div class="chart-title">PnL per Symbol</div>
    <div class="chart-wrap" style="height:180px">
      <canvas id="symChart"></canvas>
    </div>
  </div>
  <div class="chart-card">
    <div class="chart-title">Exit Reason Breakdown</div>
    <div class="chart-wrap" style="height:180px">
      <canvas id="exitChart"></canvas>
    </div>
  </div>
</div>

<!-- ── Open Positions ──────────────────────────────── -->
<div class="section-title">Open Positions ({open_count})</div>
<div class="table-card">
  <table>
    <thead>
      <tr>
        <th>Symbol</th><th>Side</th><th>Entry</th>
        <th>Stop Loss</th><th>Leverage</th><th>DCA</th><th>Opened</th>
      </tr>
    </thead>
    <tbody>{open_html}</tbody>
  </table>
</div>

<!-- ── Charts Row 3 ────────────────────────────────── -->
<div class="section-title">Symbol Breakdown</div>
<div class="table-card">
  <table>
    <thead>
      <tr><th>Symbol</th><th>Trades</th><th>Net PnL</th><th>W / L</th><th>Win Rate</th></tr>
    </thead>
    <tbody>{sym_html}</tbody>
  </table>
</div>

<!-- ── Exit Reasons ────────────────────────────────── -->
<div class="section-title">Exit Reason Analysis</div>
<div class="table-card">
  <table>
    <thead>
      <tr><th>Reason</th><th>Count</th><th>Share</th></tr>
    </thead>
    <tbody>{exit_html}</tbody>
  </table>
</div>

<!-- ── Recent Trades ───────────────────────────────── -->
<div class="section-title">Recent Trades (last 15)</div>
<div class="table-card">
  <table>
    <thead>
      <tr><th>Time</th><th>Symbol</th><th>Side</th><th>PnL</th><th>ROI</th><th>Lev</th><th>Exit Reason</th></tr>
    </thead>
    <tbody>{recent_html}</tbody>
  </table>
</div>

<div style="height:40px"></div>

</main>
</div>

<script>
Chart.defaults.color = '#7d8590';
Chart.defaults.borderColor = '#21262d';
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";
Chart.defaults.font.size = 11;

const eqLabels = {eq_labels};
const eqValues = {eq_values};
const dlLabels = {dl_labels};
const dlValues = {dl_values};
const dlColors = {dl_colors};
const symLabels = {sym_chart_labels};
const symValues = {sym_chart_values};
const symColors = {sym_chart_colors};

// Equity Curve
new Chart(document.getElementById('equityChart'), {{
  type: 'line',
  data: {{
    labels: eqLabels,
    datasets: [{{
      data: eqValues,
      borderColor: eqValues.length && eqValues[eqValues.length-1] >= 0 ? '#3fb950' : '#f85149',
      backgroundColor: eqValues.length && eqValues[eqValues.length-1] >= 0
        ? 'rgba(63,185,80,.08)' : 'rgba(248,81,73,.08)',
      borderWidth: 2,
      fill: true,
      tension: 0.3,
      pointRadius: eqValues.length > 30 ? 0 : 3,
      pointHoverRadius: 5,
      pointBackgroundColor: '#58a6ff',
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{
      callbacks: {{ label: ctx => ' $' + ctx.parsed.y.toFixed(4) }}
    }} }},
    scales: {{
      x: {{ ticks: {{ maxTicksLimit: 8, maxRotation: 0 }}, grid: {{ color: '#1c2128' }} }},
      y: {{ ticks: {{ callback: v => '$' + v }}, grid: {{ color: '#1c2128' }},
           afterDataLimits(scale) {{ const pad = Math.abs(scale.max - scale.min) * 0.1; scale.max += pad; scale.min -= pad; }} }}
    }}
  }}
}});

// Daily PnL Bar
new Chart(document.getElementById('dailyChart'), {{
  type: 'bar',
  data: {{
    labels: dlLabels,
    datasets: [{{
      data: dlValues,
      backgroundColor: dlColors,
      borderRadius: 4,
      borderSkipped: false,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{
      callbacks: {{ label: ctx => ' $' + ctx.parsed.y.toFixed(4) }}
    }} }},
    scales: {{
      x: {{ ticks: {{ maxTicksLimit: 7, maxRotation: 0 }}, grid: {{ display: false }} }},
      y: {{ ticks: {{ callback: v => '$' + v }}, grid: {{ color: '#1c2128' }} }}
    }}
  }}
}});

// Win/Loss Donut
new Chart(document.getElementById('donutChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['Win', 'Loss', 'Breakeven'],
    datasets: [{{
      data: [{m['win_count']}, {m['loss_count']}, {total - m['win_count'] - m['loss_count']}],
      backgroundColor: ['#3fb950', '#f85149', '#30363d'],
      borderColor: '#161b22',
      borderWidth: 3,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    cutout: '68%',
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ padding: 12, boxWidth: 10 }} }},
      tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.label}}: ${{ctx.parsed}}` }} }}
    }}
  }}
}});

// Symbol PnL
new Chart(document.getElementById('symChart'), {{
  type: 'bar',
  data: {{
    labels: symLabels,
    datasets: [{{
      data: symValues,
      backgroundColor: symColors,
      borderRadius: 4,
      borderSkipped: false,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    indexAxis: 'y',
    plugins: {{ legend: {{ display: false }}, tooltip: {{
      callbacks: {{ label: ctx => ' $' + ctx.parsed.x.toFixed(4) }}
    }} }},
    scales: {{
      x: {{ ticks: {{ callback: v => '$' + v }}, grid: {{ color: '#1c2128' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 10 }} }} }}
    }}
  }}
}});

// Exit Reason Donut
const exitLabels = {json.dumps([r['reason'] for r in exit_rows])};
const exitCounts = {json.dumps([r['count'] for r in exit_rows])};
const exitColors = exitLabels.map(l =>
  (l.includes('profit')||l.toLowerCase().includes('tp')) ? '#3fb950' :
  (l.toLowerCase().includes('stop')||l.toLowerCase().includes('sl')) ? '#f85149' :
  '#58a6ff'
);
new Chart(document.getElementById('exitChart'), {{
  type: 'doughnut',
  data: {{
    labels: exitLabels,
    datasets: [{{
      data: exitCounts,
      backgroundColor: exitColors,
      borderColor: '#161b22',
      borderWidth: 3,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    cutout: '60%',
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ padding: 8, boxWidth: 10, font: {{ size: 10 }} }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")
    print(f"Dashboard generated: {out_path}  ({total} trades, {open_count} open)")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "bot_performance.html"
    store = _load_store()
    generate_html(store, Path(out))


if __name__ == "__main__":
    main()
