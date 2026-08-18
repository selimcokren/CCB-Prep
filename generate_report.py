"""
CCB FuSi Assessment HTML Report Generator
------------------------------------------
Reads a ccb_assessments.json file produced by the CCB Safety Manager agent
and generates a self-contained, offline-capable HTML report.

Usage:
    python generate_report.py --input ccb_assessments.json --output ccb_report.html
"""

import json
import argparse
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# HTML template (fully inline CSS, no external dependencies)
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: Arial, Helvetica, sans-serif; font-size: 14px;
       background: #f0f2f5; color: #1a1a1a; }

/* ---- Header ---- */
header { background: #005691; color: #fff; padding: 22px 40px; }
header h1 { font-size: 1.45em; font-weight: bold; }
header .meta { margin-top: 5px; font-size: 0.88em; opacity: 0.82; }
header .stats { display: flex; gap: 20px; margin-top: 12px; }
header .stat-box { background: rgba(255,255,255,0.15); border-radius: 5px;
                   padding: 6px 16px; text-align: center; }
header .stat-box .num { font-size: 1.5em; font-weight: bold; }
header .stat-box .lbl { font-size: 0.78em; opacity: 0.85; }

/* ---- Layout ---- */
.container { max-width: 1200px; margin: 0 auto; padding: 28px 40px; }

/* ---- Section headings ---- */
h2.section-title { font-size: 1.15em; color: #005691; border-bottom: 2px solid #005691;
                   padding-bottom: 4px; margin: 28px 0 16px; }

/* ---- Summary table ---- */
.summary-table { width: 100%; border-collapse: collapse; background: #fff;
                 border-radius: 6px; overflow: hidden;
                 box-shadow: 0 1px 4px rgba(0,0,0,0.1); margin-bottom: 36px; }
.summary-table th { background: #005691; color: #fff; padding: 11px 14px;
                    text-align: left; font-size: 0.92em; white-space: nowrap; }
.summary-table td { padding: 10px 14px; border-bottom: 1px solid #e8e8e8;
                    vertical-align: middle; }
.summary-table tr:last-child td { border-bottom: none; }
.summary-table tr:hover td { background: #f0f7ff; }
.summary-table .rec-cell { font-size: 0.88em; color: #444; max-width: 340px; }

/* ---- Verdict badges ---- */
.badge { display: inline-block; padding: 3px 10px; border-radius: 3px;
         font-weight: bold; font-size: 0.82em; white-space: nowrap; }
.badge-approve     { background: #d4edda; color: #155724; }
.badge-conditional { background: #fff3cd; color: #856404; }
.badge-reject      { background: #f8d7da; color: #721c24; }

/* ---- Detail cards ---- */
.wi-card { background: #fff; border-radius: 6px;
           box-shadow: 0 1px 4px rgba(0,0,0,0.1);
           margin-bottom: 22px; overflow: hidden; }
.wi-card-header { padding: 15px 22px; border-left: 5px solid #005691; }
.wi-card-header.approve     { border-left-color: #28a745; background: #f8fff9; }
.wi-card-header.conditional { border-left-color: #ffc107; background: #fffdf0; }
.wi-card-header.reject      { border-left-color: #dc3545; background: #fff8f8; }
.wi-card-header h3 { font-size: 1.05em; color: #1a1a1a; }
.wi-card-header .header-meta { margin-top: 6px; display: flex;
                                align-items: center; gap: 12px; }
.wi-card-header .wi-status { font-size: 0.85em; color: #666; }

.wi-card-body { padding: 0 22px 22px; }

/* ---- Collapsible sections ---- */
details { margin-top: 14px; }
details summary { cursor: pointer; user-select: none;
                  list-style: none; display: flex; align-items: center; gap: 6px; }
details summary::-webkit-details-marker { display: none; }
details summary::before { content: "▶"; font-size: 0.75em; color: #005691;
                           transition: transform 0.2s; display: inline-block; }
details[open] summary::before { transform: rotate(90deg); }
details summary h4 { font-size: 0.97em; color: #005691; border-bottom: 1px solid #dce8f0;
                     padding-bottom: 3px; flex: 1; font-weight: 600; margin-top: 0; }

.summary-text { margin-top: 12px; line-height: 1.55; color: #333; }

ul.bullets { padding-left: 20px; margin-top: 8px; }
ul.bullets li { margin-bottom: 4px; line-height: 1.45; }

/* ---- Detail tables (open points, actions) ---- */
table.detail { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 0.9em; }
table.detail th { background: #eef2f7; padding: 8px 12px; text-align: left;
                  font-size: 0.88em; color: #444; border-bottom: 2px solid #d0dce8; }
table.detail td { padding: 8px 12px; border-bottom: 1px solid #eee; vertical-align: top; }
table.detail tr:last-child td { border-bottom: none; }

.sev-critical { color: #c0392b; font-weight: bold; }
.sev-major    { color: #d35400; font-weight: bold; }
.sev-minor    { color: #7f8c8d; }

/* ---- Recommendation box ---- */
.recommendation-box { background: #eef4fa; border-left: 4px solid #005691;
                      padding: 10px 16px; border-radius: 0 5px 5px 0;
                      margin-top: 8px; line-height: 1.5; font-size: 0.93em; }

/* ---- Footer ---- */
footer { text-align: center; padding: 20px; font-size: 0.82em; color: #999;
         border-top: 1px solid #ddd; margin-top: 16px; }
"""

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CCB FuSi Assessment Report</title>
  <style>{css}</style>
</head>
<body>

<header>
  <h1>CCB Functional Safety Assessment Report</h1>
  <div class="meta">Generated: {date} &nbsp;|&nbsp; Product: Brake Systems ECU (ESP / iBooster)</div>
  <div class="stats">
    <div class="stat-box"><div class="num">{total}</div><div class="lbl">Total Items</div></div>
    <div class="stat-box" style="background:rgba(40,167,69,0.35)">
      <div class="num">{n_approve}</div><div class="lbl">Approved</div></div>
    <div class="stat-box" style="background:rgba(255,193,7,0.35)">
      <div class="num">{n_conditional}</div><div class="lbl">Conditional</div></div>
    <div class="stat-box" style="background:rgba(220,53,69,0.35)">
      <div class="num">{n_reject}</div><div class="lbl">Rejected</div></div>
  </div>
</header>

<div class="container">

  <h2 class="section-title">Summary</h2>
  <table class="summary-table">
    <thead>
      <tr>
        <th>WI #</th>
        <th>Title</th>
        <th>Status</th>
        <th>Verdict</th>
        <th style="text-align:center">Open Points</th>
        <th>Recommendation</th>
      </tr>
    </thead>
    <tbody>
      {summary_rows}
    </tbody>
  </table>

  <h2 class="section-title">Detailed Assessments</h2>
  {detail_cards}

</div>

<footer>
  CCB FuSi Assessment &nbsp;|&nbsp; Automotive Brake Systems SW &nbsp;|&nbsp; {date}
</footer>

</body>
</html>
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verdict_class(verdict: str) -> str:
    v = (verdict or "").upper()
    if "REJECT" in v:
        return "reject"
    if "CONDITIONAL" in v:
        return "conditional"
    return "approve"


def _badge(verdict: str) -> str:
    cls = _verdict_class(verdict)
    return f'<span class="badge badge-{cls}">{verdict}</span>'


def _sev_class(sev: str) -> str:
    s = (sev or "").upper()
    if "CRITICAL" in s:
        return "sev-critical"
    if "MAJOR" in s:
        return "sev-major"
    return "sev-minor"


def _escape(text: str) -> str:
    """Minimal HTML escaping for user-supplied text."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_summary_row(item: dict) -> str:
    vid = _verdict_class(item.get("verdict", ""))
    n_op = len(item.get("open_points") or [])
    op_color = " style=\"color:#c0392b;font-weight:bold\"" if n_op > 0 else ""
    rec = _escape(item.get("recommendation", ""))
    return f"""      <tr>
        <td><strong>#{_escape(str(item.get('id', '?')))}</strong></td>
        <td>{_escape(item.get('title', ''))}</td>
        <td><span style="font-size:0.85em;color:#555">{_escape(item.get('status', ''))}</span></td>
        <td>{_badge(item.get('verdict', '?'))}</td>
        <td style="text-align:center"{op_color}>{n_op}</td>
        <td class="rec-cell">{rec}</td>
      </tr>"""


def _render_open_points_table(open_points: list) -> str:
    if not open_points:
        return '<p style="margin-top:8px;color:#666;font-style:italic">None identified.</p>'
    rows = ""
    for op in open_points:
        sc = _sev_class(op.get("severity", ""))
        rows += f"""        <tr>
          <td style="width:32px">{op.get('num', '')}</td>
          <td>{_escape(op.get('issue', ''))}</td>
          <td class="{sc}" style="white-space:nowrap">{_escape(op.get('severity', ''))}</td>
          <td>{_escape(op.get('action', ''))}</td>
        </tr>\n"""
    return f"""    <table class="detail">
      <thead><tr><th>#</th><th>Issue</th><th>Severity</th><th>Required Action</th></tr></thead>
      <tbody>
{rows}      </tbody>
    </table>"""


def _render_actions_table(actions: list) -> str:
    if not actions:
        return '<p style="margin-top:8px;color:#666;font-style:italic">No actions required.</p>'
    rows = ""
    for ra in actions:
        rows += f"""        <tr>
          <td style="width:32px">{ra.get('num', '')}</td>
          <td>{_escape(ra.get('action', ''))}</td>
          <td style="white-space:nowrap">{_escape(ra.get('owner', 'Developer'))}</td>
        </tr>\n"""
    return f"""    <table class="detail">
      <thead><tr><th>#</th><th>Action</th><th>Owner</th></tr></thead>
      <tbody>
{rows}      </tbody>
    </table>"""


def _render_detail_card(item: dict) -> str:
    vid_cls = _verdict_class(item.get("verdict", ""))
    wi_id = _escape(str(item.get("id", "?")))
    title = _escape(item.get("title", ""))
    status = _escape(item.get("status", ""))

    def_bullets = "".join(
        f"      <li>{_escape(d)}</li>\n" for d in (item.get("defensible") or [])
    ) or "      <li><em>No items noted.</em></li>\n"

    summary_text = _escape(item.get("summary", ""))
    recommendation = _escape(item.get("recommendation", ""))
    op_table = _render_open_points_table(item.get("open_points") or [])
    ra_table = _render_actions_table(item.get("required_actions") or [])

    n_op = len(item.get("open_points") or [])
    open_attr = " open" if n_op > 0 else ""

    return f"""  <div class="wi-card" id="wi-{wi_id}">
    <div class="wi-card-header {vid_cls}">
      <h3>WI #{wi_id} &mdash; {title}</h3>
      <div class="header-meta">
        {_badge(item.get('verdict', '?'))}
        <span class="wi-status">{status}</span>
      </div>
    </div>
    <div class="wi-card-body">

      <p class="summary-text">{summary_text}</p>

      <details open>
        <summary><h4>What is defensible</h4></summary>
        <ul class="bullets">
{def_bullets}        </ul>
      </details>

      <details{open_attr}>
        <summary><h4>Open Points / Red Flags</h4></summary>
        {op_table}
      </details>

      <details>
        <summary><h4>Required Actions Before Approval</h4></summary>
        {ra_table}
      </details>

      <div style="margin-top:16px">
        <strong style="font-size:0.93em;color:#005691">Recommendation to CCB</strong>
        <div class="recommendation-box">{recommendation}</div>
      </div>

    </div>
  </div>
"""


def render_html(data: dict) -> str:
    items = data.get("items") or []
    date = data.get("generated") or datetime.today().strftime("%Y-%m-%d")

    n_approve = sum(1 for i in items if _verdict_class(i.get("verdict", "")) == "approve")
    n_cond    = sum(1 for i in items if _verdict_class(i.get("verdict", "")) == "conditional")
    n_rej     = sum(1 for i in items if _verdict_class(i.get("verdict", "")) == "reject")

    summary_rows = "\n".join(_render_summary_row(i) for i in items)
    detail_cards = "\n".join(_render_detail_card(i) for i in items)

    return _HTML_TEMPLATE.format(
        css=_CSS,
        date=date,
        total=len(items),
        n_approve=n_approve,
        n_conditional=n_cond,
        n_reject=n_rej,
        summary_rows=summary_rows,
        detail_cards=detail_cards,
    )

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate a CCB FuSi HTML report from a JSON assessments file."
    )
    parser.add_argument("--input",  required=True, metavar="FILE",
                        help="Path to ccb_assessments.json produced by the CCB Safety Manager agent")
    parser.add_argument("--output", metavar="FILE",
                        help="Output HTML file path (default: ccb_report_<timestamp>.html next to input)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌ Input file not found: {input_path}")
        sys.exit(1)

    try:
        with open(input_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in {input_path}: {e}")
        sys.exit(1)

    if not isinstance(data.get("items"), list):
        print("❌ JSON must have a top-level 'items' array.")
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        ts = datetime.now().strftime("%d.%m.%Y_%H.%M")
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"ccb_report_{ts}.html"

    html_content = render_html(data)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    n = len(data.get("items") or [])
    print(f"✅ Report generated: {output_path}  ({n} work item(s))")


if __name__ == "__main__":
    main()
