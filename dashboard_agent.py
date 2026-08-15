"""
Dashboard Agent
Renders the HTML fantasy dashboard from reasoning agent output
and writes it to the configured web root.
"""

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

OUTPUT_PATH = os.environ.get("DASHBOARD_OUTPUT_PATH", "")


def trend_icon(trend: str) -> str:
    icons = {"UP": "▲", "DOWN": "▼", "WATCH": "◆"}
    return icons.get(trend, "◆")


def trend_class(trend: str) -> str:
    classes = {"UP": "trend-up", "DOWN": "trend-down", "WATCH": "trend-watch"}
    return classes.get(trend, "trend-watch")


def confidence_badge(confidence: str) -> str:
    classes = {"HIGH": "badge-high", "MEDIUM": "badge-medium", "LOW": "badge-low"}
    return f'<span class="badge {classes.get(confidence, "badge-low")}">{confidence}</span>'


def flag_chips(flags: list) -> str:
    if not flags:
        return ""
    label_map = {
        "injury": "🩹 Injury",
        "trade": "🔄 Trade",
        "depth_chart": "📋 Depth Chart",
        "breakout": "🚀 Breakout",
        "bust_risk": "⚠️ Bust Risk",
        "target_share": "🎯 Target Share",
    }
    chips = [f'<span class="flag-chip">{label_map.get(f, f)}</span>' for f in flags]
    return "".join(chips)


def render_player_card(player: dict) -> str:
    reasoning = player.get("reasoning", {})
    trend = reasoning.get("trend", "WATCH")
    confidence = reasoning.get("confidence", "LOW")
    summary = reasoning.get("summary", "No data available.")
    recommendation = reasoning.get("recommendation", "Monitor.")
    dynasty_note = reasoning.get("dynasty_note", "")
    contract_note = reasoning.get("contract_note", "")
    roster_status_note = reasoning.get("roster_status_note", "")
    flags = reasoning.get("flags", [])

    name = player.get("full_name", "Unknown Player")
    position = player.get("position", "?")
    team = player.get("team", "FA")
    age = player.get("age", "?")
    injury_status = player.get("injury_status") or player.get("news_injury_status") or ""
    is_ir = player.get("is_ir", False)
    is_taxi = player.get("is_taxi", False)
    is_starter = player.get("is_starter", False)
    source_count = player.get("source_count", 0)
    designation = player.get("roster_designation", "")
    trade_value = player.get("trade_value", "")

    roster_tags = []
    if is_starter:
        roster_tags.append('<span class="roster-tag starter">STR</span>')
    if is_ir:
        roster_tags.append('<span class="roster-tag ir">IR</span>')
    if is_taxi:
        roster_tags.append('<span class="roster-tag taxi">TAXI</span>')

    injury_html = ""
    if injury_status:
        injury_html = f'<span class="injury-pill">{injury_status}</span>'

    designation_html = ""
    if designation:
        designation_html = f'<span class="designation-tag">{designation}</span>'

    sources_html = ""
    if source_count > 0:
        sources_html = f'<span class="source-count">{source_count} source{"s" if source_count != 1 else ""}</span>'

    trade_value_html = ""
    if trade_value:
        trade_value_html = f'<span class="trade-value-tag">💰 {trade_value}</span>'

    dynasty_html = ""
    if dynasty_note:
        dynasty_html = f'<div class="dynasty-note">📈 {dynasty_note}</div>'

    contract_html = ""
    if contract_note:
        contract_html = f'<div class="contract-note">📄 {contract_note}</div>'

    roster_status_html = ""
    if roster_status_note:
        roster_status_html = f'<div class="roster-status-note">🧩 {roster_status_note}</div>'

    return f"""
    <div class="player-card {trend_class(trend)}">
      <div class="card-header">
        <div class="player-meta">
          <span class="position-tag pos-{position.lower()}">{position}</span>
          {designation_html}
          <span class="player-name">{name}</span>
          <span class="team-tag">{team}</span>
          {"".join(roster_tags)}
          {injury_html}
        </div>
        <div class="card-right">
          {confidence_badge(confidence)}
          <span class="trend-badge {trend_class(trend)}">{trend_icon(trend)} {trend}</span>
        </div>
      </div>
      <div class="card-body">
        <p class="summary">{summary}</p>
        <div class="recommendation">💡 {recommendation}</div>
        {dynasty_html}
        {contract_html}
        {roster_status_html}
        <div class="card-footer">
          {flag_chips(flags)}
          {trade_value_html}
          {sources_html}
          <span class="age-tag">Age {age}</span>
        </div>
      </div>
    </div>"""


def league_slug(league: dict) -> str:
    """Stable per-league anchor id, used to link the nav bar to its <details> section."""
    return f"league-{league.get('league_id', 'unknown')}"


def render_league_section(league: dict, is_first: bool = False) -> str:
    name = league["league_name"]
    season = league["season"]
    summary = league.get("summary", "")
    stats = league.get("stats", {})
    slug = league_slug(league)

    trending_up = league.get("trending_up", [])
    trending_down = league.get("trending_down", [])
    watch_list = league.get("watch_list", [])

    up_cards = "\n".join(render_player_card(p) for p in trending_up)
    down_cards = "\n".join(render_player_card(p) for p in trending_down)
    watch_cards = "\n".join(render_player_card(p) for p in watch_list)

    return f"""
  <details class="league-details" id="{slug}"{" open" if is_first else ""}>
    <summary class="league-header">
      <div class="league-title-block">
        <span class="toggle-arrow">▸</span>
        <h2 class="league-name">{name}</h2>
        <span class="season-tag">{season}</span>
      </div>
      <div class="league-stats">
        <div class="stat-pill up">▲ {stats.get('trending_up', 0)} Up</div>
        <div class="stat-pill down">▼ {stats.get('trending_down', 0)} Down</div>
        <div class="stat-pill watch">◆ {stats.get('watch', 0)} Watch</div>
        <div class="stat-pill injury">🩹 {stats.get('injured', 0)} Injured</div>
      </div>
    </summary>

    <div class="league-body">
      {f'<div class="league-summary"><p>{summary}</p></div>' if summary else ''}

      <div class="trend-columns">
        <div class="trend-col col-up">
          <h3 class="col-header up">▲ Trending Up</h3>
          {up_cards if up_cards else '<p class="no-data">No players trending up</p>'}
        </div>
        <div class="trend-col col-down">
          <h3 class="col-header down">▼ Trending Down</h3>
          {down_cards if down_cards else '<p class="no-data">No players trending down</p>'}
        </div>
        <div class="trend-col col-watch">
          <h3 class="col-header watch">◆ Watch Carefully</h3>
          {watch_cards if watch_cards else '<p class="no-data">No players on watch</p>'}
        </div>
      </div>
    </div>
  </details>"""


def render_league_nav(leagues: list) -> str:
    """Quick-jump nav bar so a specific team can be reached without scrolling past the rest."""
    if not leagues:
        return ""
    links = "\n".join(
        f'<a href="#{league_slug(l)}" class="league-nav-link" data-target="{league_slug(l)}">{l["league_name"]}</a>'
        for l in leagues
    )
    return f"""
<nav class="league-nav">
  {links}
</nav>"""


def render_html(reasoning_data: dict) -> str:
    """Render the full HTML dashboard."""
    username = reasoning_data.get("username", os.environ.get("SLEEPER_USERNAME", "your_username"))
    season = reasoning_data.get("season", "2025")
    leagues = reasoning_data.get("leagues", [])
    updated_at = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    league_sections = "\n".join(
        render_league_section(l, is_first=(i == 0)) for i, l in enumerate(leagues)
    )
    league_nav = render_league_nav(leagues)

    global_up = reasoning_data.get("global_trends", {}).get("trending_up", [])
    global_down = reasoning_data.get("global_trends", {}).get("trending_down", [])

    total_leagues = len(leagues)
    total_players = sum(l.get("stats", {}).get("total", 0) for l in leagues)
    total_injured = sum(l.get("stats", {}).get("injured", 0) for l in leagues)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dynasty Dashboard — {username}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --navy:    #0a0e1a;
      --navy-2:  #111827;
      --navy-3:  #1c2333;
      --navy-4:  #252f42;
      --bears-orange: #e05c00;
      --bears-dark:   #c04a00;
      --up:      #22c55e;
      --up-dim:  #166534;
      --down:    #ef4444;
      --down-dim:#7f1d1d;
      --watch:   #f59e0b;
      --watch-dim:#78350f;
      --text:    #e2e8f0;
      --muted:   #94a3b8;
      --border:  #2d3748;
      --card-bg: #161d2e;
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background: var(--navy);
      color: var(--text);
      font-family: 'Inter', sans-serif;
      font-size: 14px;
      line-height: 1.5;
      min-height: 100vh;
    }}

    /* ── HEADER ── */
    .site-header {{
      background: var(--navy-2);
      border-bottom: 3px solid var(--bears-orange);
      padding: 0 24px;
    }}

    .header-inner {{
      max-width: 1400px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 0;
      gap: 16px;
      flex-wrap: wrap;
    }}

    .header-brand {{
      display: flex;
      align-items: baseline;
      gap: 12px;
    }}

    .brand-title {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 28px;
      font-weight: 800;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: #fff;
    }}

    .brand-title span {{
      color: var(--bears-orange);
    }}

    .brand-sub {{
      font-size: 12px;
      color: var(--muted);
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}

    .header-meta {{
      display: flex;
      align-items: center;
      gap: 20px;
      flex-wrap: wrap;
    }}

    .meta-pill {{
      background: var(--navy-3);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 12px;
      color: var(--muted);
    }}

    .meta-pill strong {{
      color: var(--text);
      font-weight: 600;
    }}

    .updated-tag {{
      font-size: 11px;
      color: var(--muted);
    }}

    /* ── MAIN ── */
    .main {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 32px 24px;
    }}

    /* ── LEAGUE NAV ── */
    .league-nav {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 16px 24px 0;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}

    .league-nav-link {{
      background: var(--navy-3);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 6px 14px;
      font-size: 12px;
      font-weight: 600;
      color: var(--muted);
      text-decoration: none;
      transition: border-color 0.15s, color 0.15s;
    }}

    .league-nav-link:hover {{
      border-color: var(--bears-orange);
      color: #fff;
    }}

    /* ── LEAGUE SECTION ── */
    .league-details {{
      margin-bottom: 24px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--navy-2);
      overflow: hidden;
    }}

    .league-details[open] {{
      margin-bottom: 48px;
    }}

    .league-details summary {{
      list-style: none;
      cursor: pointer;
    }}
    .league-details summary::-webkit-details-marker {{
      display: none;
    }}

    .league-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
      padding: 16px 20px;
    }}

    .league-details[open] .league-header {{
      border-bottom: 1px solid var(--border);
    }}

    .toggle-arrow {{
      display: inline-block;
      font-size: 12px;
      color: var(--bears-orange);
      transition: transform 0.15s;
    }}

    .league-details[open] .toggle-arrow {{
      transform: rotate(90deg);
    }}

    .league-body {{
      padding: 20px;
    }}

    .league-title-block {{
      display: flex;
      align-items: baseline;
      gap: 10px;
    }}

    .league-name {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 22px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #fff;
    }}

    .season-tag {{
      font-size: 12px;
      background: var(--bears-orange);
      color: #fff;
      border-radius: 4px;
      padding: 2px 8px;
      font-weight: 700;
      font-family: 'Barlow Condensed', sans-serif;
    }}

    .league-stats {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}

    .stat-pill {{
      border-radius: 20px;
      padding: 4px 12px;
      font-size: 12px;
      font-weight: 600;
      border: 1px solid;
    }}
    .stat-pill.up    {{ background: rgba(34,197,94,0.12); color: var(--up); border-color: var(--up-dim); }}
    .stat-pill.down  {{ background: rgba(239,68,68,0.12); color: var(--down); border-color: var(--down-dim); }}
    .stat-pill.watch {{ background: rgba(245,158,11,0.12); color: var(--watch); border-color: var(--watch-dim); }}
    .stat-pill.injury{{ background: rgba(239,68,68,0.08); color: #fca5a5; border-color: #450a0a; }}

    .league-summary {{
      background: var(--navy-3);
      border-left: 3px solid var(--bears-orange);
      border-radius: 0 8px 8px 0;
      padding: 12px 16px;
      margin-bottom: 20px;
      color: var(--text);
      font-size: 13px;
      line-height: 1.6;
    }}

    /* ── TREND COLUMNS ── */
    .trend-columns {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 20px;
    }}

    @media (max-width: 900px) {{
      .trend-columns {{ grid-template-columns: 1fr; }}
    }}

    .col-header {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 8px 0;
      margin-bottom: 12px;
      border-bottom: 2px solid;
    }}

    .col-header.up   {{ color: var(--up);    border-color: var(--up); }}
    .col-header.down {{ color: var(--down);  border-color: var(--down); }}
    .col-header.watch{{ color: var(--watch); border-color: var(--watch); }}

    .no-data {{
      color: var(--muted);
      font-size: 13px;
      font-style: italic;
      padding: 12px 0;
    }}

    /* ── PLAYER CARD ── */
    .player-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      margin-bottom: 12px;
      overflow: hidden;
      transition: border-color 0.15s;
    }}

    .player-card:hover {{ border-color: var(--bears-orange); }}

    .player-card.trend-up   {{ border-left: 3px solid var(--up); }}
    .player-card.trend-down {{ border-left: 3px solid var(--down); }}
    .player-card.trend-watch{{ border-left: 3px solid var(--watch); }}

    .card-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 14px;
      background: var(--navy-3);
      gap: 8px;
      flex-wrap: wrap;
    }}

    .player-meta {{
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }}

    .player-name {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 16px;
      font-weight: 700;
      letter-spacing: 0.02em;
      color: #fff;
    }}

    .position-tag {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 11px;
      font-weight: 700;
      border-radius: 4px;
      padding: 2px 6px;
      letter-spacing: 0.05em;
    }}

    .pos-qb  {{ background: #7c3aed; color: #fff; }}
    .pos-rb  {{ background: #0891b2; color: #fff; }}
    .pos-wr  {{ background: #059669; color: #fff; }}
    .pos-te  {{ background: #d97706; color: #fff; }}
    .pos-k   {{ background: #6b7280; color: #fff; }}
    .pos-def {{ background: #374151; color: #fff; }}
    .pos-unk {{ background: #374151; color: #fff; }}

    .designation-tag {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.04em;
      color: var(--muted);
      background: var(--navy-4);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 1px 6px;
    }}

    .team-tag {{
      font-size: 11px;
      color: var(--muted);
      font-weight: 600;
    }}

    .roster-tag {{
      font-size: 10px;
      border-radius: 3px;
      padding: 1px 5px;
      font-weight: 700;
      font-family: 'Barlow Condensed', sans-serif;
    }}

    .roster-tag.starter {{ background: var(--bears-orange); color: #fff; }}
    .roster-tag.ir      {{ background: var(--down-dim); color: #fca5a5; }}
    .roster-tag.taxi    {{ background: var(--navy-4); color: var(--muted); border: 1px solid var(--border); }}

    .injury-pill {{
      font-size: 10px;
      background: rgba(239,68,68,0.15);
      color: #fca5a5;
      border: 1px solid var(--down-dim);
      border-radius: 10px;
      padding: 1px 7px;
      font-weight: 600;
    }}

    .card-right {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}

    .badge {{
      font-size: 10px;
      font-weight: 700;
      font-family: 'Barlow Condensed', sans-serif;
      border-radius: 3px;
      padding: 2px 6px;
      letter-spacing: 0.05em;
    }}

    .badge-high   {{ background: rgba(34,197,94,0.2); color: var(--up); }}
    .badge-medium {{ background: rgba(245,158,11,0.2); color: var(--watch); }}
    .badge-low    {{ background: rgba(100,116,139,0.2); color: var(--muted); }}

    .trend-badge {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.05em;
      border-radius: 4px;
      padding: 3px 8px;
    }}

    .trend-up   {{ background: rgba(34,197,94,0.15); color: var(--up); }}
    .trend-down {{ background: rgba(239,68,68,0.15); color: var(--down); }}
    .trend-watch{{ background: rgba(245,158,11,0.15); color: var(--watch); }}

    .card-body {{
      padding: 12px 14px;
    }}

    .summary {{
      font-size: 13px;
      color: var(--text);
      margin-bottom: 8px;
      line-height: 1.5;
    }}

    .recommendation {{
      font-size: 12px;
      color: var(--muted);
      background: var(--navy-4);
      border-radius: 6px;
      padding: 6px 10px;
      margin-bottom: 8px;
    }}

    .dynasty-note {{
      font-size: 11px;
      color: #93c5fd;
      margin-bottom: 8px;
      font-style: italic;
    }}

    .contract-note {{
      font-size: 11px;
      color: #d8b4fe;
      margin-bottom: 8px;
      font-style: italic;
    }}

    .roster-status-note {{
      font-size: 11px;
      color: #fcd34d;
      margin-bottom: 8px;
      font-style: italic;
    }}

    .card-footer {{
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }}

    .flag-chip {{
      font-size: 10px;
      background: var(--navy-4);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 2px 8px;
      color: var(--muted);
    }}

    .trade-value-tag {{
      font-size: 10px;
      background: rgba(224,92,0,0.12);
      color: var(--bears-orange);
      border: 1px solid var(--bears-dark);
      border-radius: 10px;
      padding: 2px 8px;
      font-weight: 600;
    }}

    .source-count {{
      font-size: 10px;
      color: var(--muted);
      margin-left: auto;
    }}

    .age-tag {{
      font-size: 10px;
      color: var(--muted);
    }}

    /* ── FOOTER ── */
    .site-footer {{
      text-align: center;
      padding: 32px 24px;
      color: var(--muted);
      font-size: 12px;
      border-top: 1px solid var(--border);
      margin-top: 24px;
    }}

    .footer-orange {{ color: var(--bears-orange); font-weight: 600; }}
  </style>
</head>
<body>

<header class="site-header">
  <div class="header-inner">
    <div class="header-brand">
      <div>
        <div class="brand-title">Dynasty <span>HQ</span></div>
        <div class="brand-sub">@{username} · NFL {season}</div>
      </div>
    </div>
    <div class="header-meta">
      <div class="meta-pill"><strong>{total_leagues}</strong> Leagues</div>
      <div class="meta-pill"><strong>{total_players}</strong> Players tracked</div>
      <div class="meta-pill"><strong>{total_injured}</strong> Injury flags</div>
      <div class="updated-tag">Updated {updated_at}</div>
    </div>
  </div>
</header>

{league_nav}

<main class="main">
  {league_sections if league_sections else '<p style="color:var(--muted);text-align:center;padding:60px 0;">No league data available. Run the pipeline to populate.</p>'}
</main>

<footer class="site-footer">
  <span class="footer-orange">Dynasty HQ</span> · Powered by Sleeper API + Anthropic Claude · {updated_at}
</footer>

<script>
  document.querySelectorAll('.league-nav-link').forEach(function (link) {{
    link.addEventListener('click', function (e) {{
      e.preventDefault();
      var target = document.getElementById(link.dataset.target);
      if (!target) return;
      target.open = true;
      target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    }});
  }});
</script>

</body>
</html>"""


def write_dashboard(html: str, output_path: str = OUTPUT_PATH) -> bool:
    """Write the rendered HTML to the output path, with backup of previous version."""
    try:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Backup previous version
        if path.exists():
            backup = path.with_suffix(".prev.html")
            shutil.copy2(path, backup)
            log.info(f"Previous dashboard backed up to {backup}")

        path.write_text(html, encoding="utf-8")
        log.info(f"Dashboard written to {output_path}")
        return True

    except PermissionError:
        log.error(f"Permission denied writing to {output_path}. Check file permissions.")
        return False
    except Exception as e:
        log.error(f"Failed to write dashboard: {e}")
        return False


def run(reasoning_data: dict, output_path: str = OUTPUT_PATH) -> bool:
    """
    Main entry point for the Dashboard agent.
    Renders HTML and writes to web root.
    """
    log.info("Dashboard agent starting...")
    html = render_html(reasoning_data)
    success = write_dashboard(html, output_path)
    if success:
        log.info("Dashboard agent complete — site updated.")
    else:
        log.error("Dashboard agent failed to write output.")
    return success


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [DASHBOARD] %(message)s")
    # Test with empty data
    mock_data = {
        "username": os.environ.get("SLEEPER_USERNAME", "your_username"),
        "season": "2025",
        "leagues": [],
        "global_trends": {"trending_up": [], "trending_down": [], "watch_list": []},
    }
    html = render_html(mock_data)
    test_path = "/tmp/dashboard_test.html"
    Path(test_path).write_text(html)
    print(f"Test dashboard written to {test_path}")
