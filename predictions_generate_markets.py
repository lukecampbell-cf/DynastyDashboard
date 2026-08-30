"""Controlled operator bridge from Sleeper rosters to published markets."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote

import httpx

from common import write_json_atomic
from predictions_market_agent import generate_markets

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
LEAGUE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
USERNAME_RE = re.compile(r"^.{1,50}$")


class GenerationError(RuntimeError):
    pass


def _load_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GenerationError(f"Could not load {label} at {path}") from exc
    if not isinstance(value, dict):
        raise GenerationError(f"{label} must contain a JSON object")
    return value


def authorised_username(data_dir: Path, supplied: str) -> str:
    normalised = supplied.strip().lower()
    if not USERNAME_RE.fullmatch(supplied.strip()):
        raise GenerationError("An authorised Sleeper username is required")
    config = _load_object(data_dir / "authorised_users.json", "authorised_users.json")
    for item in config.get("authorised_users", []):
        if not isinstance(item, dict):
            continue
        configured = str(item.get("sleeper_username", "")).strip()
        if configured.lower() == normalised:
            if item.get("enabled") is not True:
                raise GenerationError("That Sleeper username is disabled")
            return configured
    raise GenerationError("That Sleeper username is not authorised")


class SleeperClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.environ.get("PREDICTIONS_SLEEPER_API_BASE") or "https://api.sleeper.app/v1").rstrip("/")

    def get(self, path: str) -> Any:
        try:
            response = httpx.get(f"{self.base_url}/{path.lstrip('/')}", timeout=15,
                                 headers={"Accept": "application/json", "User-Agent": "DynastyHQ-Predictions/1.0"})
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GenerationError("Sleeper returned an unexpected response") from exc


def resolve_inputs(username: str, season: str, requested: list[str] | None, client: SleeperClient) -> tuple[str, list[dict]]:
    user = client.get(f"user/{quote(username, safe='')}")
    if not isinstance(user, dict) or not user.get("user_id"):
        raise GenerationError("The authorised Sleeper username could not be resolved")
    if str(user.get("username", "")).strip().lower() != username.strip().lower():
        raise GenerationError("Sleeper returned a mismatched username")
    user_id = str(user["user_id"])
    leagues_raw = client.get(f"user/{quote(user_id, safe='')}/leagues/nfl/{season}")
    if not isinstance(leagues_raw, list):
        raise GenerationError("Sleeper league data was invalid")
    leagues = {str(item.get("league_id")): item for item in leagues_raw if isinstance(item, dict) and item.get("league_id")}
    if requested is None:
        selected = list(leagues.values())
    else:
        missing = [league_id for league_id in requested if league_id not in leagues]
        if missing:
            raise GenerationError("Requested league is not owned by the resolved user: " + ", ".join(missing))
        selected = [leagues[league_id] for league_id in requested]
    if not selected:
        raise GenerationError("No leagues were selected for generation")
    return user_id, selected


def resolve_php_binary(configured: str | None = None) -> str:
    """Find PHP CLI, including the versioned path used by Plesk hosts."""
    requested = configured or os.environ.get("PREDICTIONS_PHP_BINARY")
    if requested:
        resolved = shutil.which(requested)
        if resolved:
            return resolved
        path = Path(requested)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise GenerationError(f"Configured PHP CLI is not executable: {requested}")

    on_path = shutil.which("php")
    if on_path:
        return on_path

    plesk_versions = sorted(
        Path("/opt/plesk/php").glob("*/bin/php"),
        key=lambda path: tuple(int(part) if part.isdigit() else -1 for part in path.parts[-3].split(".")),
        reverse=True,
    )
    for candidate in plesk_versions:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise GenerationError(
        "PHP CLI was not found. Install/enable it, add it to PATH, or set "
        "PREDICTIONS_PHP_BINARY=/absolute/path/to/php"
    )


def build_snapshot(data_dir: Path, season: str, week: int, league: dict, roster: dict,
                   sleeper_players: dict, php_binary: str | None = None) -> dict:
    payload = {"data_directory": str(data_dir), "season": season, "week": week,
               "league": league, "roster": roster, "sleeper_players": sleeper_players}
    binary = resolve_php_binary(php_binary)
    try:
        process = subprocess.run([binary, str(ROOT / "scripts/predictions_build_snapshot.php")],
                                 input=json.dumps(payload), text=True, capture_output=True, check=False)
    except OSError as exc:
        raise GenerationError(f"Could not start PHP CLI at {binary}: {exc}") from exc
    if process.returncode != 0:
        raise GenerationError(process.stderr.strip() or "Phase 2 snapshot builder failed")
    try:
        snapshot = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise GenerationError("Phase 2 snapshot builder returned invalid JSON") from exc
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("players"), list):
        raise GenerationError("Phase 2 snapshot builder returned an invalid snapshot")
    return snapshot


@contextmanager
def generation_lock(path: Path, *, blocking: bool = False) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise GenerationError("Generation is already running for this league/week") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def publish_one(snapshot: dict, data_dir: Path, *, generator: Callable[..., dict] = generate_markets) -> tuple[dict, Path, str]:
    season, week, league_id = str(snapshot["season"]), int(snapshot["week"]), str(snapshot["league_id"])
    final_path = data_dir / "prediction_markets" / season / f"week_{week}" / f"{league_id}.json"
    lock_path = data_dir / ".prediction_locks" / season / f"week_{week}" / f"{league_id}.lock"
    snapshot_path = data_dir / "prediction_snapshots" / season / f"week_{week}" / f"{league_id}.json"
    cache_path = data_dir / "weekly_player_analysis.json"
    with generation_lock(lock_path):
        write_json_atomic(snapshot_path, snapshot)
        # Stage the market document separately. Only replace the authoritative
        # file after Phase 3 returns and the staged document validates.
        with tempfile.TemporaryDirectory(dir=data_dir, prefix=".prediction-market-") as staging, \
                generation_lock(data_dir / ".prediction_locks" / "weekly_player_analysis.lock", blocking=True):
            before = _load_object(cache_path, "weekly analysis cache") if cache_path.exists() else {}
            expected = [p for p in snapshot.get("players", []) if isinstance(p, dict)]
            reusable = 0
            for player in expected:
                entry = before.get(f"{season}:{week}:{player.get('player_id')}")
                if isinstance(entry, dict) and entry.get("input_hash") == player.get("input_hash"):
                    reusable += 1
            result = generator(snapshot, cache_path=cache_path, markets_dir=Path(staging))
            staged = Path(staging) / season / f"week_{week}" / f"{league_id}.json"
            document = _load_object(staged, "staged market")
            if (str(document.get("league_id")) != league_id or str(document.get("season")) != season
                    or int(document.get("week", 0)) != week or not isinstance(document.get("markets"), list)):
                raise GenerationError("Phase 3 produced an invalid market document")
            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, final_path)
            os.chmod(final_path, 0o644)
            status = "reused" if expected and reusable == len(expected) else ("generated" if result.get("markets") else "skipped")
            return result, final_path, status


def run(args: argparse.Namespace, *, client: SleeperClient | None = None,
        snapshot_builder: Callable[..., dict] = build_snapshot,
        publisher: Callable[..., tuple[dict, Path, str]] = publish_one) -> list[tuple[str, Path]]:
    data_dir = Path(os.environ.get("DASHBOARD_DATA_DIR", ROOT)).resolve()
    username = authorised_username(data_dir, args.username)  # Must precede every Sleeper request.
    client = client or SleeperClient()
    requested = None if args.all_leagues else args.league_id
    user_id, leagues = resolve_inputs(username, args.season, requested, client)
    # Load both canonical inputs before roster API calls/generation.
    _load_object(data_dir / "player_directory.json", "player_directory.json")
    _load_object(data_dir / "player_cache.json", "player_cache.json")
    sleeper_players = client.get("players/nfl")
    if not isinstance(sleeper_players, dict):
        raise GenerationError("Sleeper player data was invalid")
    results = []
    failures = []
    for league in leagues:
        league_id = str(league["league_id"])
        try:
            if not LEAGUE_ID_RE.fullmatch(league_id):
                raise GenerationError(f"Sleeper returned an invalid league ID: {league_id!r}")
            rosters = client.get(f"league/{quote(league_id, safe='')}/rosters")
            roster = next((item for item in rosters if isinstance(item, dict) and str(item.get("owner_id", "")) == user_id), None) if isinstance(rosters, list) else None
            if roster is None:
                raise GenerationError(f"No roster owned by the resolved user was found in league {league_id}")
            snapshot = snapshot_builder(data_dir, args.season, args.week, league, roster, sleeper_players)
            result, path, status = publisher(snapshot, data_dir)
            print(f"{league_id}: {status} {len(result.get('markets', []))} markets -> {path}")
            results.append((status, path))
        except GenerationError as exc:
            print(f"{league_id}: failed - {exc}")
            failures.append(f"{league_id}: {exc}")
    if failures:
        raise GenerationError(f"{len(failures)} league(s) failed: " + "; ".join(failures))
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare and publish weekly Dynasty HQ prediction markets")
    parser.add_argument("--username", required=True, help="Authorised Sleeper username (not a user ID)")
    parser.add_argument("--season", required=True)
    parser.add_argument("--week", required=True, type=int)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all-leagues", action="store_true")
    selection.add_argument("--league-id", action="append", help="League ID; repeat to select multiple leagues")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"\d{4}", args.season) or not 1 <= args.week <= 22:
        parser.error("season must be four digits and week must be between 1 and 22")
    if args.league_id and (len(set(args.league_id)) != len(args.league_id)
                           or any(not LEAGUE_ID_RE.fullmatch(item) for item in args.league_id)):
        parser.error("league IDs must be unique and contain only letters, digits, underscore or hyphen")
    return args


def main() -> int:
    try:
        run(parse_args())
        return 0
    except GenerationError as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
