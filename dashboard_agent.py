"""
Dashboard Agent
Renders the HTML fantasy dashboard from reasoning agent output
and writes it to the configured web root.
"""

import logging
import os
import shutil
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Optional

from common import write_text_atomic
from schemas import AnalysedPlayer, LeagueResult, ReasoningOutput

log = logging.getLogger(__name__)

OUTPUT_PATH = os.environ.get("DASHBOARD_OUTPUT_PATH", "")
DASHBOARD_CSS_PATH = Path(__file__).resolve().parent / "dashboard.css"


def load_dashboard_css() -> str:
    """Read the dashboard stylesheet, inlined verbatim into the rendered
    HTML's <style> block by render_html() — the output stays a single
    self-contained file, this just keeps the CSS out of the Python f-string."""
    return DASHBOARD_CSS_PATH.read_text(encoding="utf-8")


def esc(value) -> str:
    """
    HTML-escape a value before splicing it into the template. Applied
    uniformly to every field that isn't a hardcoded literal — most of this
    data (news-derived Claude output, Sleeper league/player names, RosterAudit
    tier labels) ultimately traces back to a third party, so nothing here is
    trusted enough to interpolate raw. quote=True (the default) also escapes
    `"`/`'`, which matters for values placed inside a double-quoted HTML
    attribute (e.g. class="pos-{position}"), not just text content.
    """
    if value is None:
        return ""
    return escape(str(value), quote=True)


def trend_icon(trend: str) -> str:
    icons = {"UP": "▲", "DOWN": "▼", "WATCH": "◆"}
    return icons.get(trend, "◆")


def trend_class(trend: str) -> str:
    classes = {"UP": "trend-up", "DOWN": "trend-down", "WATCH": "trend-watch"}
    return classes.get(trend, "trend-watch")


def confidence_badge(confidence: str) -> str:
    classes = {"HIGH": "badge-high", "MEDIUM": "badge-medium", "LOW": "badge-low"}
    return f'<span class="badge {classes.get(confidence, "badge-low")}">{esc(confidence)}</span>'


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
    chips = [f'<span class="flag-chip">{esc(label_map.get(f, f))}</span>' for f in flags]
    return "".join(chips)


def safe_url(url) -> Optional[str]:
    """
    Only http(s) URLs are allowed through as a clickable href. HTML-escaping
    (esc()) neutralizes tag/attribute breakout, but it does NOT neutralize a
    dangerous URI *scheme* — `javascript:alert(1)` escapes to itself (no
    `<`/`>`/`"`/`'` involved) and the browser still executes it on click.
    Scheme-allowlisting is a separate, necessary check for any URL that
    ultimately comes from a third party (every url here is scraped news).
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if url.lower().startswith(("http://", "https://")):
        return url
    return None


SOURCE_LABELS = {
    "rotowire": "Rotowire",
    "fantasypros": "FantasyPros",
    "fantasypros_injuries": "FantasyPros Injuries",
    "espn": "ESPN",
    "nfl_com": "NFL.com",
    "cbssports": "CBS Sports",
}


def source_attribution_html(news_items: list) -> str:
    """
    One chip per distinct source that fed this player's reasoning, linking
    to that source's article when a URL is available (fantasypros_injuries
    items have none — it's a structured designation, not an article) — the
    point of this line is letting you jump straight to the actual news that
    drove Claude's summary, not just seeing a bare count of how many
    sources mentioned the player.
    """
    if not news_items:
        return ""
    seen = {}
    for item in news_items:
        src = item.get("source")
        if src and src not in seen:
            seen[src] = item

    chips = []
    for src, item in seen.items():
        label = esc(SOURCE_LABELS.get(src, src))
        headline = item.get("headline") or ""
        title_attr = f' title="{esc(headline)}"' if headline else ""
        url = safe_url(item.get("url"))
        if url:
            chips.append(
                f'<a class="source-chip" href="{esc(url)}" target="_blank" '
                f'rel="noopener noreferrer"{title_attr}>{label}</a>'
            )
        else:
            chips.append(f'<span class="source-chip no-link"{title_attr}>{label}</span>')

    return f'<div class="source-attribution">📎 <span class="source-attribution-label">Sources:</span> {"".join(chips)}</div>'


def render_player_card(player: AnalysedPlayer) -> str:
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
    news_items = player.get("news_items", [])
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
        injury_html = f'<span class="injury-pill">{esc(injury_status)}</span>'

    designation_html = ""
    if designation:
        designation_html = f'<span class="designation-tag">{esc(designation)}</span>'

    sources_html = ""
    if source_count > 0:
        sources_html = f'<span class="source-count">{esc(source_count)} source{"s" if source_count != 1 else ""}</span>'

    trade_value_html = ""
    if trade_value:
        trade_value_html = f'<span class="trade-value-tag">💰 {esc(trade_value)}</span>'

    dynasty_html = ""
    if dynasty_note:
        dynasty_html = f'<div class="dynasty-note">📈 {esc(dynasty_note)}</div>'

    contract_html = ""
    if contract_note:
        contract_html = f'<div class="contract-note">📄 {esc(contract_note)}</div>'

    roster_status_html = ""
    if roster_status_note:
        roster_status_html = f'<div class="roster-status-note">🧩 {esc(roster_status_note)}</div>'

    source_attribution = source_attribution_html(news_items)

    position_esc = esc(position)

    return f"""
    <div class="player-card {trend_class(trend)}">
      <div class="card-header">
        <div class="player-meta">
          <span class="position-tag pos-{position_esc.lower()}">{position_esc}</span>
          {designation_html}
          <span class="player-name">{esc(name)}</span>
          <span class="team-tag">{esc(team)}</span>
          {"".join(roster_tags)}
          {injury_html}
        </div>
        <div class="card-right">
          {confidence_badge(confidence)}
          <span class="trend-badge {trend_class(trend)}">{trend_icon(trend)} {esc(trend)}</span>
        </div>
      </div>
      <div class="card-body">
        <p class="summary">{esc(summary)}</p>
        <div class="recommendation">💡 {esc(recommendation)}</div>
        {dynasty_html}
        {contract_html}
        {roster_status_html}
        {source_attribution}
        <div class="card-footer">
          {flag_chips(flags)}
          {trade_value_html}
          {sources_html}
          <span class="age-tag">Age {esc(age)}</span>
        </div>
      </div>
    </div>"""


def league_slug(league: LeagueResult) -> str:
    """Stable per-league anchor id, used to link the nav bar to its <details> section."""
    return f"league-{esc(league.get('league_id', 'unknown'))}"


def render_league_section(league: LeagueResult, is_first: bool = False) -> str:
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
        <h2 class="league-name">{esc(name)}</h2>
        <span class="season-tag">{esc(season)}</span>
      </div>
      <div class="league-stats">
        <div class="stat-pill up">▲ {esc(stats.get('trending_up', 0))} Up</div>
        <div class="stat-pill down">▼ {esc(stats.get('trending_down', 0))} Down</div>
        <div class="stat-pill watch">◆ {esc(stats.get('watch', 0))} Watch</div>
        <div class="stat-pill injury">🩹 {esc(stats.get('injured', 0))} Injured</div>
      </div>
    </summary>

    <div class="league-body">
      {f'<div class="league-summary"><p>{esc(summary)}</p></div>' if summary else ''}

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


def render_league_nav(leagues: list[LeagueResult]) -> str:
    """Quick-jump nav bar so a specific team can be reached without scrolling past the rest."""
    if not leagues:
        return ""
    links = "\n".join(
        f'<a href="#{league_slug(l)}" class="league-nav-link" data-target="{league_slug(l)}">{esc(l["league_name"])}</a>'
        for l in leagues
    )
    return f"""
<nav class="league-nav">
  {links}
</nav>"""


# Cap on cards shown per column in the cross-league trends panel — a manager
# in many leagues could otherwise see dozens of cards (deduped by name, but
# still one per distinct player) before ever reaching their first league
# section below. Each league's own trending_up/trending_down still shows
# everyone; this is just the headline cross-league digest.
GLOBAL_TRENDS_MAX = 6


def render_global_trends_section(global_up: list[AnalysedPlayer], global_down: list[AnalysedPlayer]) -> str:
    """
    Cross-league "trending across all your leagues" panel, from
    reasoning_agent.py's global_trends — the same player dicts (and cards)
    used in each league's own trending columns, deduped by full_name and
    already sorted by confidence before being merged. Omitted entirely when
    there's nothing to show (e.g. no league data yet).
    """
    if not global_up and not global_down:
        return ""

    up_cards = "\n".join(render_player_card(p) for p in global_up[:GLOBAL_TRENDS_MAX])
    down_cards = "\n".join(render_player_card(p) for p in global_down[:GLOBAL_TRENDS_MAX])

    return f"""
  <section class="global-trends">
    <h2 class="global-trends-title">📊 Trending Across All Leagues</h2>
    <div class="global-trends-columns">
      <div class="trend-col col-up">
        <h3 class="col-header up">▲ Trending Up</h3>
        {up_cards if up_cards else '<p class="no-data">No players trending up</p>'}
      </div>
      <div class="trend-col col-down">
        <h3 class="col-header down">▼ Trending Down</h3>
        {down_cards if down_cards else '<p class="no-data">No players trending down</p>'}
      </div>
    </div>
  </section>"""


def render_html(reasoning_data: ReasoningOutput) -> str:
    """Render the full HTML dashboard."""
    username = esc(reasoning_data.get("username", os.environ.get("SLEEPER_USERNAME", "your_username")))
    season = esc(reasoning_data.get("season", "2025"))
    leagues = reasoning_data.get("leagues", [])
    updated_at = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    league_sections = "\n".join(
        render_league_section(l, is_first=(i == 0)) for i, l in enumerate(leagues)
    )
    league_nav = render_league_nav(leagues)

    global_up = reasoning_data.get("global_trends", {}).get("trending_up", [])
    global_down = reasoning_data.get("global_trends", {}).get("trending_down", [])
    global_trends_section = render_global_trends_section(global_up, global_down)

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
  {load_dashboard_css()}
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
      <a class="calc-link" href="trade_calculator.php">Trade Calculator &rarr;</a>
      <div class="updated-tag">Updated {updated_at}</div>
    </div>
  </div>
</header>

{league_nav}

<main class="main">
  {global_trends_section}
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

        write_text_atomic(path, html)
        log.info(f"Dashboard written to {output_path}")
        return True

    except PermissionError:
        log.error(f"Permission denied writing to {output_path}. Check file permissions.")
        return False
    except Exception as e:
        log.error(f"Failed to write dashboard: {e}")
        return False


def run(reasoning_data: ReasoningOutput, output_path: str = OUTPUT_PATH) -> bool:
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
    mock_data: ReasoningOutput = {
        "username": os.environ.get("SLEEPER_USERNAME", "your_username"),
        "season": "2025",
        "leagues": [],
        "global_trends": {"trending_up": [], "trending_down": [], "watch_list": []},
    }
    html = render_html(mock_data)
    test_path = "/tmp/dashboard_test.html"
    Path(test_path).write_text(html)
    print(f"Test dashboard written to {test_path}")
