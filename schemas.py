"""
Schemas
TypedDict definitions for the record shapes that flow between the pipeline's
stages (sleeper_agent -> news_agent -> reasoning_agent -> dashboard_agent).
These were previously untyped `dict`s described only in prose docstrings —
formalizing them here lets a type checker (mypy) catch a key renamed or
typoed in one module but not updated in another, which is easy to miss when
a shape crosses five independently-edited files. Purely static: TypedDict
has no runtime effect and these classes are never instantiated — every
existing dict literal already satisfies its corresponding TypedDict as-is.
"""

from typing import Optional, TypedDict


# ── Contract (contract_agent.py) ─────────────────────────────────────────

class ContractInfo(TypedDict, total=False):
    """A resolve_contract() result, also stored at player_cache[pid]["contract"].
    Only "found" and "contract_updated_at" are guaranteed present — every
    other key is populated only on a successful ("(CURRENT)" deal found) lookup."""
    found: bool
    contract_updated_at: str
    spotrac_id: str
    spotrac_url: str
    contract_span: Optional[str]
    contract_type: Optional[str]
    current_year: Optional[str]
    final_year: Optional[str]
    current_year_cap_hit: str
    current_year_cap_pct: str


# ── Player cache (sleeper_agent.py's player_cache.json entries) ─────────

class PlayerCacheEntry(TypedDict, total=False):
    """player_cache[pid] — bio/id crosswalk (sleeper_agent.py) plus an
    optional contract sub-object (contract_agent.py, refreshed separately)."""
    sleeper_player_id: str
    full_name: str
    position: str
    team: str
    age: Optional[int]
    years_exp: Optional[int]
    college: Optional[str]
    draft_year: Optional[int]
    fp_player_id: Optional[int]
    fp_page_url: Optional[str]
    details_updated_at: str
    contract: ContractInfo


# ── Roster player (sleeper_agent.py's run() output) ──────────────────────

class _ResolvedPlayerRequired(TypedDict):
    player_id: str
    full_name: str
    position: str
    team: str
    age: Optional[int]
    years_exp: Optional[int]
    college: Optional[str]
    draft_year: Optional[int]
    injury_status: Optional[str]
    injury_body_part: Optional[str]
    status: str
    is_starter: bool
    is_taxi: bool
    is_ir: bool
    fp_player_id: Optional[int]
    fp_rank: Optional[int]
    fp_pos_rank: Optional[str]
    fp_tier: Optional[int]
    fp_scoring_format: str
    trade_value: Optional[str]
    contract: Optional[ContractInfo]


class ResolvedPlayer(_ResolvedPlayerRequired, total=False):
    """One roster player, as returned by sleeper_agent.run() (before news
    or reasoning enrichment). roster_designation is added afterward, in
    place, by assign_roster_designations() — only fantasy positions get one."""
    roster_designation: str


class ContractLookupPlayer(TypedDict):
    """The minimal shape contract_agent.run() actually reads — player_id,
    full_name, position, nothing else. Every real caller passes a full
    ResolvedPlayer (a structural superset, so it satisfies this too); this
    exists so the type matches what the function's own docstring promises
    ("each dict needs at least player_id, full_name, position") rather than
    over-constraining it to the full roster-player shape."""
    player_id: str
    full_name: str
    position: str


# ── League (sleeper_agent.py's run() output) ─────────────────────────────

class LeagueRecord(TypedDict):
    league_id: str
    league_name: str
    season: str
    roster_id: Optional[int]
    record: dict
    players: list[ResolvedPlayer]
    total_players: int
    settings: dict
    scoring_settings: dict
    ranking_format: str


class SleeperOutput(TypedDict):
    """sleeper_agent.run()'s top-level return value.

    trade_values_degraded is trade_value_agent.run()'s own degraded_formats
    passed through — the list of value formats ("sf"/"1qb") that fell back
    to cached (or empty) data this run instead of a fresh RosterAudit fetch.
    Surfaced here (rather than orchestrator.py calling trade_value_agent
    directly) because the fetch itself happens inside sleeper_agent.run(),
    which is what actually calls trade_value_agent.run(); this lets
    orchestrator.py report a distinct "trade values" pipeline stage without
    restructuring which module owns that call."""
    username: str
    season: Optional[str]
    user: Optional[dict]
    leagues: list[LeagueRecord]
    trade_values_degraded: list[str]


# ── News (news_agent.py) ──────────────────────────────────────────────────

class NewsItem(TypedDict, total=False):
    """One scraped news item — the exact key set varies slightly by source
    (see news_agent.py's individual scrape_*() functions), so every field
    but the always-present ones is optional here."""
    source: str
    player_name: Optional[str]
    team: Optional[str]
    headline: Optional[str]
    body: Optional[str]
    analysis: Optional[str]
    url: Optional[str]
    scraped_at: str
    injury_status: str
    injury_type: Optional[str]
    published_at: Optional[str]


class NewsByPlayerEntry(TypedDict):
    player_name: str
    sources: list[str]
    items: list[NewsItem]
    source_count: int
    has_injury_flag: bool
    injury_status: Optional[str]


class EnrichedPlayer(ResolvedPlayer, total=False):
    """A ResolvedPlayer after news_agent.match_to_roster() — adds the
    news-derived fields every roster player gets, matched to news or not."""
    news_items: list[NewsItem]
    source_count: int
    has_injury_flag: bool
    news_injury_status: Optional[str]
    has_news: bool
    season: str  # stamped on by reasoning_agent.run()'s Pass 1


class SourceStatus(TypedDict):
    ok: bool
    error: Optional[str]
    items: int


class _NewsOutputRequired(TypedDict):
    total_items: int
    unique_players: int
    news_by_player: dict[str, NewsByPlayerEntry]
    all_items: list[NewsItem]
    source_status: dict[str, SourceStatus]
    scraped_at: str


class NewsOutput(_NewsOutputRequired, total=False):
    """news_agent.run()'s top-level return value. enriched_roster is only
    present when roster_players was passed in."""
    enriched_roster: list[EnrichedPlayer]


# ── Reasoning (reasoning_agent.py) ─────────────────────────────────────────

class ReasoningResult(TypedDict):
    """The Anthropic API's structured per-player assessment — see
    reasoning_agent.SYSTEM_PROMPT for the authoritative schema this
    formalizes; keep the two in sync if the prompt's schema changes."""
    trend: str          # "UP" | "DOWN" | "WATCH" | "NO_ACTION" (deterministic only)
    confidence: str      # "HIGH" | "MEDIUM" | "LOW"
    summary: str
    fantasy_impact: str  # "SHORT" | "MEDIUM" | "LONG" | "NONE"
    recommendation: str
    dynasty_note: Optional[str]
    contract_note: Optional[str]
    roster_status_note: Optional[str]
    flags: list[str]


class AnalysisCacheEntry(TypedDict):
    """One entry in reasoning_agent's player_analysis_cache.json, keyed by
    Sleeper player_id. "signal" is compute_signal_fingerprint()'s output —
    deliberately untyped here (dict) since it's an opaque comparison key,
    not a shape anything reads field-by-field.

    generated_at and last_reused_at are deliberately separate: generated_at
    is set only when the LLM genuinely produced this analysis, and is what
    QUIET_REUSE_MAX_AGE is measured against. last_reused_at is bumped every
    time a quiet reuse serves this entry without a fresh call — if reuse
    also bumped generated_at, an entry that keeps quietly reusing could
    never age past QUIET_REUSE_MAX_AGE, defeating the forced-refresh."""
    full_name: str
    reasoning: ReasoningResult
    generated_at: str
    last_reused_at: str
    signal: dict


class SummaryCacheEntry(TypedDict):
    """One entry in reasoning_agent's league_summary_cache.json, keyed by league_id."""
    summary: str
    fingerprint: dict


class AnalysedPlayer(EnrichedPlayer, total=False):
    """An EnrichedPlayer after reasoning_agent.run() attaches its verdict —
    dashboard_agent.render_player_card()'s actual input shape.

    change_status is signal_evidence.classify_change_status()'s output —
    "material_change" | "noteworthy_unchanged" | "stable" | "no_signal" —
    answering "what's actually new since the previous analysis" from a pure
    fingerprint diff, with no additional LLM call involved."""
    reasoning: ReasoningResult
    generated_at: Optional[str]
    change_status: str


class LeagueStats(TypedDict):
    total: int
    trending_up: int
    trending_down: int
    watch: int
    no_action: int
    injured: int


class LeagueResult(TypedDict):
    league_id: str
    league_name: str
    season: str
    summary: str
    players: list[AnalysedPlayer]
    trending_up: list[AnalysedPlayer]
    trending_down: list[AnalysedPlayer]
    watch_list: list[AnalysedPlayer]
    no_action: list[AnalysedPlayer]
    stats: LeagueStats


class GlobalTrends(TypedDict):
    trending_up: list[AnalysedPlayer]
    trending_down: list[AnalysedPlayer]
    watch_list: list[AnalysedPlayer]


class ReasoningOutput(TypedDict):
    """reasoning_agent.run()'s top-level return value — dashboard_agent.py's
    entire input.

    change_summary is a deduped-by-player (a player rostered in multiple
    leagues counts once) tally of AnalysedPlayer.change_status across every
    league — {"material_change": N, "noteworthy_unchanged": N, "stable": N,
    "no_signal": N} — the counts dashboard_agent.py's header banner is
    built from."""
    username: Optional[str]
    season: Optional[str]
    leagues: list[LeagueResult]
    global_trends: GlobalTrends
    change_summary: dict[str, int]


# ── Health (orchestrator.py writes, health_agent.py reads/writes) ────────

class CacheStatus(TypedDict):
    file: str
    exists: bool
    stale: bool
    age_hours: Optional[float]


class _StepStatusRequired(TypedDict):
    ok: bool
    error: Optional[str]


class StepStatus(_StepStatusRequired, total=False):
    """One pipeline step's outcome, written by orchestrator.py and read by
    health_agent.py. "sources" is only present for the news step — see
    NewsOutput's source_status for the per-source detail it carries."""
    sources: dict[str, SourceStatus]


class HealthRecord(TypedDict):
    """health_agent.record_run()'s return value / health.json's contents."""
    last_run_at: str
    last_run_success: bool
    last_success_at: Optional[str]
    steps: dict[str, StepStatus]
    stale_caches: list[CacheStatus]
    overall_ok: bool


# ── Pipeline result (orchestrator.py) ─────────────────────────────────────

class StageResult(TypedDict, total=False):
    """
    One pipeline stage's outcome, for orchestrator.run_pipeline()'s
    returned PipelineResult. Finer-grained than StepStatus above (which
    health.json persists as-is, unchanged by this): success/degraded/failed
    rather than just ok/error, so a caller can tell "ran, but fell back to
    stale cached data" (degraded=True) apart from a full failure without
    parsing log lines. Purely in-process/return-value — health.json's own
    on-disk schema is intentionally left alone.
    """
    name: str
    success: bool
    degraded: bool
    message: Optional[str]


class PipelineResult(TypedDict):
    """orchestrator.run_pipeline()'s return value."""
    success: bool
    stages: list[StageResult]
