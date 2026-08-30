# Dynasty Dashboard — Setup Guide

## Prerequisites
- Python 3.11+
- Nginx serving your-domain.com with web root at /var/www/sites/your-domain.com/httpdocs/
- An Anthropic or OpenAI API key for league analysis
- A Parse Bot API key (PARSE_BOT_API) — a paid, metered third-party API (see
  [parse.bot](https://parse.bot) for current pricing) that powers the Contract Agent's
  Spotrac lookup, three of the News Agent's six sources (ESPN, FantasyPros news,
  FantasyPros injuries), and the Trade Value Agent's RosterAudit rankings. Optional and
  non-fatal everywhere it's used: missing key, an outage, or a rate/credit limit hit
  makes that one call site degrade on its own rather than stop the run — Contract Agent
  falls back to no verified contract data (Claude hedges from general knowledge
  instead), News Agent just runs with fewer of its six sources, and Trade Value Agent
  falls back to its last successfully cached file. A Parse Bot outage or pricing change
  never stops the pipeline from producing a dashboard, only from enriching it as fully.

---

## 1. Upload files to your VPS

From your local machine:
```bash
scp -r ./dashboard/ user@your-domain.com:~/dynasty-dashboard/
```

Or clone/create the directory directly on the VPS.

---

## 2. Install dependencies

```bash
cd ~/dynasty-dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 3. Configure environment

```bash
cp .env.example .env
nano .env
# Set AI_PROVIDER=openai (default) or AI_PROVIDER=anthropic
# Set OPENAI_API_KEY=... or ANTHROPIC_API_KEY=...
# Set PARSE_BOT_API=your-parse-bot-api-key
# Set SLEEPER_USERNAME=your-sleeper-username
# Set DASHBOARD_OUTPUT_PATH=/var/www/vhosts/your-domain.com/httpdocs/dashboard/index.html
chmod 600 .env   # restrict read access
```

---

## 4. Create the dashboard directory

```bash
sudo mkdir -p /var/www/vhosts/your-domain.com/httpdocs/dashboard
sudo chown $USER:www-data /var/www/vhosts/your-domain.com/httpdocs/dashboard
sudo chmod 775 /var/www/vhosts/your-domain.com/httpdocs/dashboard
```

---

## 5. Test with a dry run

```bash
source venv/bin/activate
python -m dynasty_dashboard --dry-run
```

This writes to /tmp/dynasty_dashboard_preview.html — open it in a browser (scp it locally) to verify the output before going live.

---

## 6. Run for real

```bash
python -m dynasty_dashboard
```

Dashboard will be live at: https://your-domain.com/dashboard/

---

## 7. Schedule with cron (recommended: every 4 hours during season)

```bash
crontab -e
```

Add:
```
# Dynasty Dashboard — refresh every 4 hours
0 */4 * * * cd /var/www/vhosts/your-domain.com/dashboard && /var/www/vhosts/your-domain.com/dashboard/venv/bin/python -m dynasty_dashboard >> /var/www/vhosts/your-domain.com/dashboard/cron.log 2>&1

# During season (Sep-Jan): hourly on match days (Thu, Sun, Mon)
# 0 * * * 0,1,4 cd /var/www/vhosts/your-domain.com/dashboard && /var/www/vhosts/your-domain.com/dashboard/venv/bin/python -m dynasty_dashboard >> cron.log 2>&1
```

Replace `your-domain.com` and paths above with your actual VPS domain and username.

---

## 8. Nginx location block (if /dashboard/ is not auto-served)

Add to your your-domain.com server block in /etc/nginx/sites-available/your-domain.com:

```nginx
location /dashboard/ {
    alias /var/www/vhosts/your-domain.com/httpdocs/dashboard/;
    index index.html;
    try_files $uri $uri/ =404;
}
```

Then: sudo nginx -t && sudo systemctl reload nginx

---

## File structure

```
dynasty-dashboard/
├── dynasty_dashboard/    ← importable Python application package
│   ├── orchestrator.py   ← pipeline entry point (`python -m dynasty_dashboard`)
│   ├── *_agent.py        ← pipeline and Predictions agents
│   ├── common.py         ← shared atomic-write and cache helpers
│   ├── paths.py          ← explicit package/project path boundary
│   └── schemas.py        ← shared TypedDict contracts
├── tests/                ← Python unit and repository-layout tests
├── scripts/              ← operator adapters and PHP regression scripts
├── web/
│   ├── trade_calculator.php
│   ├── trade_calculator_lib.php
│   └── predictions/      ← deployable Predictions PHP application
├── player_cache.json     ← per-player bio + contract cache (self-building, safe to delete)
├── trade_values.json     ← RosterAudit dynasty trade values, sf & 1qb refreshed independently (weekly)
├── player_directory.json ← every fantasy-relevant NFL player + trade value (weekly, powers trade_calculator.php)
├── player_store.json      ← canonical player facts used by league reasoning
├── league_snapshots/      ← compact per-league membership/status views
├── league_analysis_cache.json ← successful batched league analyses and fingerprints
├── health.json            ← last run status, per-step/per-source errors, stale-cache flags
├── requirements.txt
├── .env                  ← your API key (never commit)
├── .env.example          ← template
├── README.md             ← architecture overview
├── pipeline.log          ← run log
└── debug/                ← intermediate JSON (--debug flag)
```

See [README.md](README.md) for how the pipeline and each agent works.

---

## Trade Calculator (`trade_calculator.php`)

A standalone PHP page — not part of the Python pipeline — that reads
`player_directory.json` (every fantasy-relevant NFL player + trade value)
and `trade_values.json` (for its pick tier chart) directly to let you build a
two-sided trade and get a fairness verdict. It doesn't call any API or write
to either file; it's a read-only consumer of caches the pipeline already
maintains — `player_directory.json` is built by `dynasty_dashboard/player_directory_agent.py`,
called automatically from the Sleeper agent, so there's nothing extra to
schedule.

Requires PHP (PHP-FPM behind nginx, or your host's equivalent) able to
execute `.php` files in whichever directory you deploy it to. This project
has otherwise only ever served static HTML, so if PHP isn't already wired up
on your VPS you'll need to install `php-fpm` and add an nginx block for it
(the exact steps depend on your distro/PHP version — see your OS's php-fpm
package docs).

**Deploy both PHP files together from `web/`.** `trade_calculator.php` does
`require __DIR__ . '/trade_calculator_lib.php'`, so the two must land in the
same directory. Copying only `trade_calculator.php` gives you a PHP fatal
(500 or blank page). Copying only the new `trade_calculator.php` over a
deployment that predates the lib file does the same. Re-copy the pair
whenever either one changes. The dashboard's "Explore Trade" deep link
(`trade_calculator.php?player=<id>`) is resolved server-side in the lib, so a
stale `trade_calculator.php` ignores the `?player=` id and loads an empty
Side A.

**Simplest deployment:** copy `web/trade_calculator.php` and
`web/trade_calculator_lib.php` into the public dashboard directory. Set
`DASHBOARD_DATA_DIR` to the private/project data directory containing
`player_directory.json` and `trade_values.json`. Add an nginx `location` block
pointing at that directory (same `alias` pattern as the dashboard block in
step 8 above), e.g.:

```nginx
location /trade-calculator.php {
    alias /var/www/vhosts/your-domain.com/dashboard/trade_calculator.php;
    fastcgi_pass unix:/var/run/php/php-fpm.sock;   # match your php-fpm socket
    fastcgi_index trade_calculator.php;
    include fastcgi_params;
    fastcgi_param SCRIPT_FILENAME $request_filename;
}
```

**Deploying it elsewhere** (e.g. inside the web root alongside `index.html`,
away from the JSON caches): set the `DASHBOARD_DATA_DIR` environment
variable to the project root's absolute path (an nginx `fastcgi_param
DASHBOARD_DATA_DIR /path/to/dynasty-dashboard;` works), and the script will
read the caches from there instead.

---

## Fantasy Predictions deployment

Predictions is optional and isolated from the generated dashboard. The public
web root contains `index.html`, the trade calculator, and the contents of
`web/predictions/`. SQLite, markets, snapshots, and score files stay private:

```text
/var/www/vhosts/your-domain.com/
├── httpdocs/dashboard/
│   ├── index.html
│   ├── trade_calculator.php
│   ├── trade_calculator_lib.php
│   └── predictions/
└── private/
    ├── dynasty-dashboard-data/
    │   ├── authorised_users.json
    │   ├── player_cache.json
    │   ├── player_directory.json
    │   ├── prediction_markets/
    │   └── prediction_snapshots/
    ├── predictions/predictions.sqlite
    └── settlement/actual_scores_YYYY_WW.json
```

Create private locations with minimal pipeline/PHP-FPM access (replace the
user and group for your host):

```bash
sudo install -d -m 0750 -o "$USER" -g www-data /var/www/vhosts/your-domain.com/private/dynasty-dashboard-data
sudo install -d -m 0770 -o "$USER" -g www-data /var/www/vhosts/your-domain.com/private/predictions
sudo install -d -m 0700 -o "$USER" -g "$USER" /var/www/vhosts/your-domain.com/private/settlement
```

Set these for market generation and pass the first two to PHP-FPM (for nginx,
`fastcgi_param` inside the Predictions PHP location is sufficient):

```dotenv
DASHBOARD_DATA_DIR=/var/www/vhosts/your-domain.com/private/dynasty-dashboard-data
PREDICTIONS_DB_PATH=/var/www/vhosts/your-domain.com/private/predictions/predictions.sqlite
# PREDICTIONS_PHP_BINARY=/opt/plesk/php/8.3/bin/php
```

If PHP-FPM does not receive `PREDICTIONS_DB_PATH`, copy
`web/predictions/includes/local_config.example.php` to `local_config.php` and
set the same private SQLite path there. The file is gitignored. Never place
score JSON, SQLite (including `-wal`/`-shm`), snapshots, or `local_config.php`
under the public directory. Application-created private JSON and SQLite files
are mode `0600`; directory permissions provide the shared access boundary.

Deploy without nesting the source directory twice:

```bash
rsync -a --delete web/predictions/ /var/www/vhosts/your-domain.com/httpdocs/dashboard/predictions/
```

Run the complete offline suite before deployment:

```bash
python -m unittest discover -p "test_*.py" -v
php scripts/test_predictions_phase1.php
php scripts/test_predictions_phase2.php
php scripts/test_predictions_phase4.php
php scripts/test_predictions_phase5.php
php scripts/test_predictions_phase6.php
```

Verify `/dashboard/`, `/dashboard/predictions/`, and navigation in both
directions. A deliberately invalid Predictions database path in staging must
affect only `/dashboard/predictions/`; static `/dashboard/index.html` must
continue to load.

---

## Troubleshooting

**"API key environment variable not set"**
→ Select `AI_PROVIDER=openai` with `OPENAI_API_KEY`, or
`AI_PROVIDER=anthropic` with `ANTHROPIC_API_KEY`. Existing `DASHBOARD_KEY` remains an
Anthropic-only compatibility fallback.

**"Permission denied writing to /var/www/..."**
→ Re-run step 4 above, check chown matches your user

**"No leagues found"**
→ Sleeper API may return empty for future seasons before they open. Try --season 2024

**News scraping returns 0 items**
→ Sites may have changed structure. Check pipeline.log for specific errors. The reasoning agent will still run with Sleeper data alone.
→ Or run `python -m dynasty_dashboard.health_agent` — it'll name exactly which of the six news sources is failing and why, instead of one blanket "news step" status.

**Not sure if the pipeline is actually healthy**
→ Run `python -m dynasty_dashboard.health_agent` for a summary: last run status, per-step errors, per-news-source errors, and which caches have gone stale. `health.json` is rewritten every run (success or failure) so it never reflects a status older than your last cron tick.

**Contract lookups or ESPN news all come back empty**
→ Check PARSE_BOT_API is set in .env and hasn't hit its Parse Bot rate/credit limit (check pipeline.log for 401/429 responses from api.parse.bot).

**"Explore Trade" link opens the calculator with Side A empty**
→ The deployed `trade_calculator.php` predates the deep-link feature. View source on
`trade_calculator.php?player=<id>`: if there's no `const PRESELECT = ...` line next to
`const PLAYERS`, the server is running an old copy. Re-copy both
`trade_calculator.php` and `trade_calculator_lib.php` (see the trade calculator
section above).
→ If `PRESELECT` is there and the page shows a note saying the player isn't in the
directory, the id genuinely isn't in `player_directory.json`. Rebuild it with
`python -m dynasty_dashboard.player_directory_agent` (running it directly always forces a rebuild).

**Dashboard renders but looks wrong**
→ Run --dry-run, scp the HTML to your local machine and inspect in browser dev tools
