# Dynasty Dashboard

An automated pipeline that pulls your Sleeper fantasy football rosters, cross-references
player news from six sites, layers in FantasyPros consensus rankings and RosterAudit
dynasty trade value, runs it all through Claude for a trend/recommendation call per
player, and renders the result as a static HTML dashboard.

For install/deploy/cron instructions, see [SETUP.md](SETUP.md). This file covers what
the pipeline does and how the pieces fit together.

---

## Pipeline

```
orchestrator.py
  │
  ├─ 1. sleeper_agent.py   → your rosters, enriched with FantasyPros rank + RosterAudit trade value
  ├─ 2. contract_agent.py  → real NFL contract terms per player, scraped from Spotrac
  ├─ 3. news_agent.py      → injury/trade/depth-chart news, matched to your roster
  ├─ 4. reasoning_agent.py → Claude analysis: trend, confidence, recommendation
  └─ 5. dashboard_agent.py → renders + writes index.html
```

Each step's output feeds the next. `orchestrator.py` runs them in order and degrades
gracefully if contract lookup or news scraping fail (continues with the data it has)
but stops if Sleeper or Reasoning fail outright, since there's nothing to render
without them.

Player retrieval is deliberately split across steps 1 and 2, each with its own cache
lifetime, so the pipeline doesn't re-hit either upstream site more than it needs to:
step 1 refreshes a player's Sleeper bio details (name, team, age, draft year, etc.)
only when missing or over 2 weeks old; step 2 refreshes that same player's Spotrac
contract only when missing or over 4 weeks old. Contract terms move far less often
than rosters do, hence the longer window.

Run it with:
```bash
python orchestrator.py            # writes to the configured web root
python orchestrator.py --dry-run  # writes to /tmp instead, for previewing
python orchestrator.py --debug    # also dumps intermediate JSON to debug/
```

---

## 1. Sleeper Agent (`sleeper_agent.py`)

Fetches your leagues and rosters from the public Sleeper API (no auth required) for
the username in `SLEEPER_USERNAME` (`.env`). The season is resolved dynamically from
Sleeper's `/state/nfl` endpoint, so nothing needs to be hardcoded year to year.

For each league, it also:

- **Detects the league format** — `settings.type == 2` means dynasty; otherwise the
  `scoring_settings.rec` value picks PPR (1), half-PPR (0.5), or standard (0/absent).
- **Fetches FantasyPros consensus rankings** for that format (dynasty rankings are
  scoring-format-agnostic — FantasyPros only publishes one blended dynasty list — so
  every dynasty league shares a single fetch; redraft leagues get the matching
  PPR/half-PPR/standard cheat sheet). Cached locally 24h since these move often.
- **Matches each roster player to a FantasyPros player_id**, first via the persistent
  local cache (`player_cache.json`, see below), falling back to name matching only
  for players it hasn't seen before.
- **Derives a trade value tier** (e.g. "Mid 1st", "Early 3rd", "Waiver / Deep Stash").
  For dynasty leagues this comes from RosterAudit's market values (see
  `trade_value_agent.py` below) — a real, weekly-refreshed valuation rather than a
  hand-rolled bucket. Redraft leagues, and any player RosterAudit hasn't ranked yet,
  fall back to the old heuristic: bucketing the FantasyPros consensus rank into
  4-player slices (early/mid/late thirds of a round, sized for a 12-team startup
  draft) — a common industry convention, not a scraped/authoritative value.
- **Assigns a roster-relative depth designation** (e.g. `WR1`, `WR2`, `TE1`) via
  `assign_roster_designations()`. Players are grouped by position *within your own
  roster* (not league-wide) and ranked by `fp_rank`, lowest first; unranked players
  sort last but still get a designation. Only applied to `QB`/`RB`/`WR`/`TE`/`K`/`DEF`.

Each resolved player carries: `full_name`, `position`, `team`, `age`, `years_exp`,
`college`, `draft_year`, `injury_status`, `is_starter`/`is_taxi`/`is_ir`, plus the
FantasyPros fields `fp_player_id`, `fp_rank`, `fp_pos_rank`, `fp_tier`,
`fp_scoring_format`, `trade_value`, `roster_designation`, and `contract` (populated by
step 2, see below).

`draft_year` is read straight from Sleeper's own `metadata.rookie_year` field rather
than inferred from `years_exp` — `years_exp` counts completed seasons and drifts
depending on when in the season the pipeline runs, so it can't reliably tell a 2025
rookie from a 2026 one on its own. `rookie_year` is a fixed fact per player, so this
is what feeds the reasoning agent's accurate "Year Drafted" context.

### `player_cache.json` — the per-player bio/id cache

A flat JSON file in the project root, keyed by Sleeper `player_id`:

```json
{
  "4046": {
    "sleeper_player_id": "4046",
    "full_name": "Patrick Mahomes",
    "position": "QB",
    "team": "KC",
    "age": 30,
    "years_exp": 9,
    "college": "Texas Tech",
    "draft_year": 2017,
    "fp_player_id": 15291,
    "fp_page_url": "https://www.fantasypros.com/nfl/players/patrick-mahomes.php",
    "details_updated_at": "2026-08-06T10:00:02Z",
    "contract": { "...": "see contract_agent.py below" }
  }
}
```

On each run, every roster player is looked up here first. If the entry is missing, or
its `details_updated_at` is more than **2 weeks** old, the bio fields and FantasyPros
id are refreshed from the freshly-fetched Sleeper player DB (and a name-based match
against FantasyPros on an id cache miss) and the timestamp is bumped — otherwise the
cached values are reused as-is and nothing is re-derived or rewritten. Fields that
change more often than bio details — injury status, FantasyPros rank/tier, roster
flags — are never cached here; they're re-read live every run regardless of the bio
entry's freshness. This also means a bad automatic name match can be manually
corrected by editing this file directly, and the fix sticks.

This file is safe to delete — it will simply rebuild itself (with a burst of name
matching, and Spotrac contract re-lookups) on the next run.

### `trade_values.json` — weekly RosterAudit dynasty market values

Fetched by `trade_value_agent.py`, called from within `sleeper_agent.run()` (it isn't
its own orchestrator step, the same way the FantasyPros fetch above isn't). Pulls
RosterAudit's `get_dynasty_rankings` endpoint on the ["rosteraudit.com API"](https://parse.bot/marketplace/0df80132-239a-4553-97df-7b36fed4d070/rosteraudit-com-api)
listing on Parse Bot (same `PARSE_BOT_API` key as the other Parse Bot calls), paginating
through every player and future draft pick in the ranking, for both Superflex and 1QB
value formats.

RosterAudit's ranking usefully blends real players and future draft picks into one
value-ordered list (e.g. "2027 Early 1st", "2027 Mid 1st", ...), so rather than
inventing our own rank→tier bucketing, a player's trade value tier is just the label of
the closest pick their numeric value sits above — a market-calibrated "Mid 1st" instead
of an arbitrary one. Saved to `trade_values.json` in the project root (a separate file
from `player_cache.json`, since this is market data refreshed on its own weekly cadence
rather than a per-player bio cache):

```json
{
  "fetched_at": "2026-08-09T12:45:56Z",
  "source": "rosteraudit.com dynasty rankings (via Parse Bot)",
  "formats": {
    "sf": {
      "players": {
        "9509": { "name": "Bijan Robinson", "position": "RB", "team": "ATL", "value": 10000, "rank_overall": 1, "tier": "1" }
      },
      "tier_chart": [
        { "label": "Elite", "min_value": 6300 },
        { "label": "Early 1st", "min_value": 5122 },
        { "label": "Mid 1st", "min_value": 2832 }
      ]
    },
    "1qb": { "...": "same shape, valued for 1-QB startups" }
  }
}
```

Refreshed at most **once a week** — checked via the file's mtime, the same pattern
`fetch_fantasypros_rankings()` uses for its 24h cache, just a longer window since
dynasty trade value doesn't move nearly as fast as weekly rankings do. A failed refresh
falls back to the existing stale file rather than blanking out trade values for a week;
if no file exists yet either, every dynasty player just falls back to the FantasyPros
rank heuristic until the next successful fetch.

Which value format (`sf` vs `1qb`) applies is decided per league in
`determine_value_format()`, from that league's `roster_positions` — a `SUPER_FLEX` slot
or a second dedicated `QB` slot means Superflex, since RosterAudit's pick and player
values swing significantly between the two (a 1QB startup values QB picks far lower).

### `player_directory.json` — weekly full player + trade value directory

Built by `player_directory_agent.py`, called from within `sleeper_agent.run()` right
after `all_players` (Sleeper's full player DB, already fetched for step 1) and
`rosteraudit_data` (trade values, both formats, already fetched above) are available.

Exists for `trade_calculator.php`: `player_cache.json` only has the ~180 players
actually on your rosters, and `trade_values.json`'s own player lists only cover
RosterAudit's top ~390 ranked players per format — neither is a complete pool to search
when building a hypothetical trade. This file filters Sleeper's ~12,000-player database
down to fantasy-relevant positions (`QB`/`RB`/`WR`/`TE`/`K`/`DEF`) on an active NFL
roster (~1,050 players) and gives each one a trade value for both formats, in three
tiers of confidence:

1. **RosterAudit's real value**, when the player is one of its ~390 ranked players per
   format (`"source": "rosteraudit"`).
2. Otherwise, an **estimate calibrated against RosterAudit's own value curve**: for
   every player priced by both RosterAudit and FantasyPros, we know
   `(fp_rank, ra_value)`; an unpriced player's value is linearly interpolated between
   the two calibration points bracketing their own FantasyPros dynasty consensus rank
   (`sleeper_agent.fetch_fantasypros_rankings("dynasty")`, matched by normalised name).
   This keeps the estimate on the same numeric scale as real RosterAudit values instead
   of an arbitrary made-up one, but it's still a derived guess — tagged
   `"source": "fantasypros_estimate"` so callers can tell it apart from real market
   data (`trade_calculator.php` shows it as "· est." in the UI).
3. `value: 0, tier: "Deep Stash", "source": "unranked"` for anyone neither system has
   any signal on at all.

Refreshed at most **once a week**, matching `trade_values.json`'s own cadence — the
only piece of this file that actually changes meaningfully week to week. A failed or
empty build falls back to the existing stale file rather than blanking out the trade
calculator's player pool.

---

## 2. Contract Agent (`contract_agent.py`)

Looks up each roster player's real-world NFL contract on Spotrac and writes the result
into the `contract` key of that player's entry in `player_cache.json`, alongside the
bio data from step 1. Spotrac has no official API of its own, so this calls the
[spotrac.com API](https://parse.bot/marketplace/7994a459-31cd-4d12-a28b-5c053246f105/spotrac-com-api)
on [Parse Bot](https://parse.bot) — a hosted wrapper that does the scraping and hands
back structured JSON — authenticated with a Parse Bot API key (`PARSE_BOT_API` in
`.env`).

For each player (skipping `DEF` — team defenses have no personal contract):

- **Searches** Parse Bot's `search_players` endpoint, which returns candidates across
  every sport Spotrac covers, not just NFL.
- **Disambiguates** by normalised name; if more than one candidate shares a name (e.g.
  an unrelated MLB player), the Sleeper position must also map onto the Spotrac
  position label to confirm the match. Spotrac's search doesn't always label
  well-known players with a position (even the real Patrick Mahomes comes back
  unlabelled), so an unlabelled same-name candidate still gets tried — but only
  accepted once its contract data itself is confirmed as an NFL contract, not
  another capped sport's. If nothing can be confirmed, the lookup is left unresolved
  rather than guessed.
- **Fetches the player's contract** via `get_player_contract` and pulls the handful of
  facts Spotrac's own "(CURRENT)" deal table exposes cleanly: the deal's year span and
  type (extension/rookie/renegotiation/etc.), and the current league year's cap hit
  and cap percentage. The API returns full year-by-year tables (cap hit, cash, dead
  cap, earnings history) rather than a single clean summary sentence, so total value
  and guaranteed-money figures aren't fabricated from that data — only what's directly
  labelled is reported.

Cached **4 weeks** per player — much longer than the Sleeper bio cache, since contract
terms change far less often than rosters or rankings do. A miss (no confident Spotrac
match, e.g. a very deep stash) is cached too, so it isn't re-queried every run — it's
simply retried after the same 4-week window in case the player gets a Spotrac page
later.

This contract data feeds `reasoning_agent.py`'s `contract_note`: when a verified match
exists, Claude is instructed to state those actual terms rather than its own
best-effort guess; when it doesn't, the prompt falls back to the previous
general-knowledge hedge.

---

## 3. News Agent (`news_agent.py`)

Pulls player news from six sources and cross-references them by name:

| Source | What it covers |
|---|---|
| Rotowire | Player news updates with analyst commentary |
| FantasyPros news | Player transaction/news feed, via the Parse Bot API |
| FantasyPros injuries | Structured weekly injury designations (PUP/IR/Q/D/O), via the Parse Bot API |
| ESPN NFL | General league news, via the Parse Bot API |
| NFL.com | General league news |
| CBS Sports NFL | General league news |

Rotowire extracts a clean player name from a dedicated `.news-update__player-link`
element (a prior, over-broad `[class*='player']` fallback selector used to match the
whole name+headline wrapper instead, e.g. "Josh DownsNursing groin injury" — fixed).

ESPN's own site returns an empty 202 "bot challenge" response to scraping, so its feed
goes through the [espn.com scraper](https://parse.bot/scrapers/1682503b-990a-4f2a-b44a-c95c30c1d08f)
on Parse Bot instead (`get_news`, authenticated with `PARSE_BOT_API`, shared by every
Parse Bot call in this file) — this also replaced what used to be *two* separate ESPN
HTML scrapers (a general news feed and a dedicated injuries table), since Parse Bot's
single `get_news` call already tags every article with the players and teams it's
about via its `categories` list. An article naming several players (e.g. a
training-camp roundup) is emitted as one news item per player.

FantasyPros' own `/nfl/news/` page had also drifted from the HTML selectors here (they
were silently returning zero items), so its news feed now goes through the
[fantasypros.com scraper](https://parse.bot/scrapers/6f2ca995-75cd-4151-ae78-ea03c18f8012)'s
`get_player_news` endpoint too. Its headlines read like transaction-wire copy ("Jared
Bartlett waived by Jaguars") with no delimiter, so player names are pulled with a
regex matching the leading capitalised-name run before the lowercase verb, rather than
the `:` split the general-news sources use.

That same FantasyPros scraper also exposes `get_injuries` — a structured, per-player
injury/practice-designation table the old scraper never had an equivalent for. It's
keyed by NFL week and season year rather than scraped live, so `get_current_nfl_week()`
first calls the [nfl.com API](https://parse.bot/marketplace/f5dc4749-3bbe-416c-be21-c1b189dd6f00/nfl-com-api)'s
`get_current_week` (also via Parse Bot) to resolve those. FantasyPros' own off-season
default (`week="draft"`) returns only a handful of stale "retired" entries even
mid-preseason, so outside the regular season this requests week 1 instead — PUP/IR
designations set during camp carry forward into it, which is what's actually current.

The two remaining general-news sources (NFL.com, CBS Sports) still parse headlines
that aren't consistently player-scoped, so name extraction there stays best-effort
(split on `:`) and often produces no name, or occasionally a non-player fragment (a
show/column name, a partial clause) rather than a real player — useful for
injury/trade signal, not reliable for exact per-player matching. Unlike the sources
above, this hasn't been fixed, since there's no single clean delimiter to key off for
either site.

`cross_reference()` groups all items by normalised player name and flags injury-related
keywords. `match_to_roster()` (called by the orchestrator before the reasoning step)
attaches each player's matched news items, source count, and injury flag onto their
Sleeper roster entry.

---

## 4. Reasoning Agent (`reasoning_agent.py`)

For each roster player (enriched with news from step 3 and contract data from step 2),
calls the Claude API (`claude-sonnet-4-6`, key from `DASHBOARD_KEY` in `.env`) with
their injury status, roster context, verified draft year, verified contract terms (when
available), and recent news, and gets back structured JSON: `trend` (UP/DOWN/WATCH),
`confidence`, a one-line `summary`, `fantasy_impact` window, a `recommendation`, a
`dynasty_note`, a `contract_note`, a `roster_status_note`, and relevant `flags`. It also
generates a short executive summary per league. Players are bucketed into `trending_up` /
`trending_down` / `watch_list`, both per-league and merged into `global_trends` across all
leagues.

`contract_note` is grounded in `contract_agent.py`'s Spotrac lookup when a verified match
exists for that player — Claude is instructed to state those actual terms rather than
guess. When no verified match exists, it falls back to Claude's best-effort
characterization from general knowledge (e.g. "Rookie deal, year 2 of 4") and is
instructed to hedge rather than fabricate precise numbers. `draft_year` (from Sleeper's
`metadata.rookie_year`, see step 1) is passed the same way — Claude is told to treat it
as ground truth rather than infer a player's draft class itself, which is what keeps
"drafted 2025" vs "drafted 2026" distinctions accurate. `roster_status_note` remains a
best-effort characterization from general knowledge — not a lookup against a live depth
chart feed — so treat it as directional, not authoritative.

---

## 5. Dashboard Agent (`dashboard_agent.py`)

Renders the reasoning output into a static `index.html` — one card per player showing
trend icon, confidence badge, roster designation (WR1/WR2/etc.), summary, recommendation,
dynasty/contract/roster-status notes, trade value, and flag chips — and writes it to the
web root (or `/tmp` for `--dry-run`).

Each league renders as a collapsible `<details>` section (only the first league is open
by default) with a quick-jump nav bar above the leagues that expands and scrolls to the
target league on click — keeps the page navigable as a single static "app" without
needing a build step or JS framework.

---

## Trade Calculator (`trade_calculator.php`)

A standalone tool, separate from the pipeline above — it isn't run by `orchestrator.py`
and doesn't call any API. It reads `player_directory.json` (every fantasy-relevant NFL
player + trade value, see step 1's player-directory section above) and `trade_values.json`
(for its pick tier chart) directly, server-side, on each page load, and lets you build a
two-sided trade (any player, plus future picks) to get a fairness verdict, computed
client-side in vanilla JS from the embedded value data. Pick values come from the same
RosterAudit tier chart `trade_value_agent.py` already builds — a pick you add is priced
at its tier's `min_value` for whichever format (Superflex / 1QB) is selected.

Verdict is `min(sideA, sideB) / max(sideA, sideB)`, bucketed into Yes / Close Yes / Close
No / No / Outrageously Unbalanced (thresholds are constants at the top of the PHP file).
A trade below the "No" threshold surfaces a `mailto:` link to email a summary to the
Bullshit Trade Association — it only opens the visitor's own mail client with a
pre-filled draft; nothing sends automatically.

Requires PHP on the host — see [SETUP.md](SETUP.md#trade-calculator-trade_calculatorphp)
for deployment.

---

## Caching summary

| File | Contents | Expiry |
|---|---|---|
| `/tmp/sleeper_players_cache.json` | Full Sleeper NFL player DB (~5MB) | 24h |
| `/tmp/fantasypros_rankings_{format}.json` | FantasyPros ranks for one format | 24h |
| `player_cache.json` (project root) | Per-player bio/id details (name, team, age, draft year, FantasyPros id) | 2 weeks per player |
| `player_cache.json` → `.<id>.contract` | Spotrac contract terms per player | 4 weeks per player |
| `trade_values.json` (project root) | RosterAudit dynasty trade values + pick tier chart, sf & 1qb | 1 week |
| `player_directory.json` (project root) | Every fantasy-relevant NFL player + trade value (sf & 1qb), for trade_calculator.php | 1 week |
| `league_summary_cache.json` (project root) | Per-league Haiku executive summary, keyed by league_id | regenerated only when trend/injury counts change |
| `pipeline.log` | Orchestrator run log | append-only |
| `debug/*.json` | Intermediate stage output, only with `--debug` | per run |

---

## Requirements

See `requirements.txt`: `httpx`, `beautifulsoup4`, `lxml`, `anthropic`, `python-dotenv`.
