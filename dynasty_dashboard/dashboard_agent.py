"""
Dashboard Agent
Renders the HTML fantasy dashboard from reasoning agent output
and writes it to the configured web root.
"""

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .common import write_text_atomic
from . import dashboard_components as _components
from .paths import PROJECT_ROOT
from .schemas import ReasoningOutput

log = logging.getLogger(__name__)

OUTPUT_PATH = os.environ.get("DASHBOARD_OUTPUT_PATH", "")
DASHBOARD_CSS_PATH = PROJECT_ROOT / "dashboard.css"

# Compatibility facade: callers can keep importing component helpers from
# dashboard_agent while their implementations live in the focused module.
esc = _components.esc
safe_url = _components.safe_url
trend_icon = _components.trend_icon
trend_class = _components.trend_class
confidence_badge = _components.confidence_badge
flag_chips = _components.flag_chips
source_attribution_html = _components.source_attribution_html
relative_time = _components.relative_time
evidence_note_html = _components.evidence_note_html
render_player_card = _components.render_player_card
league_slug = _components.league_slug
render_league_section = _components.render_league_section
render_league_nav = _components.render_league_nav
render_global_trends_section = _components.render_global_trends_section
render_change_summary_banner = _components.render_change_summary_banner
provider_display_name = _components.provider_display_name


def load_dashboard_css() -> str:
    """Read the dashboard stylesheet, inlined verbatim into the rendered
    HTML's <style> block by render_html() — the output stays a single
    self-contained file, this just keeps the CSS out of the Python f-string."""
    return DASHBOARD_CSS_PATH.read_text(encoding="utf-8")


def render_html(reasoning_data: ReasoningOutput) -> str:
    """Render the full HTML dashboard."""
    username = esc(reasoning_data.get("username", os.environ.get("SLEEPER_USERNAME", "your_username")))
    season = esc(reasoning_data.get("season", "2025"))
    leagues = reasoning_data.get("leagues", [])
    updated_at = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    ai_provider = provider_display_name()

    league_sections = "\n".join(
        render_league_section(l, is_first=(i == 0)) for i, l in enumerate(leagues)
    )
    league_nav = render_league_nav(leagues)

    global_up = reasoning_data.get("global_trends", {}).get("trending_up", [])
    global_down = reasoning_data.get("global_trends", {}).get("trending_down", [])
    global_trends_section = render_global_trends_section(global_up, global_down)
    change_summary_banner = render_change_summary_banner(reasoning_data.get("change_summary", {}))

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
      <a class="calc-link" href="predictions/">Predictions &rarr;</a>
      <a class="calc-link" href="trade_calculator.php">Trade Calculator &rarr;</a>
      <div class="updated-tag">Updated {updated_at}</div>
    </div>
  </div>
</header>

{league_nav}

<main class="main">
  {change_summary_banner}
  {global_trends_section}
  {league_sections if league_sections else '<p style="color:var(--muted);text-align:center;padding:60px 0;">No league data available. Run the pipeline to populate.</p>'}
</main>

<footer class="site-footer">
  <span class="footer-orange">Dynasty HQ</span> · Powered by Sleeper API + ParseBot + {ai_provider} + FantasyPros + RosterAudit · {updated_at}
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
        "change_summary": {},
    }
    html = render_html(mock_data)
    test_path = "/tmp/dashboard_test.html"
    Path(test_path).write_text(html)
    print(f"Test dashboard written to {test_path}")
