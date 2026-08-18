# Dynasty Dashboard — Setup Guide

## Prerequisites
- Python 3.11+
- Nginx serving your-domain.com with web root at /var/www/sites/your-domain.com/httpdocs/
- Your Anthropic API key (DASHBOARD_KEY)
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
# Set DASHBOARD_KEY=your-rotated-api-key
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
python orchestrator.py --dry-run
```

This writes to /tmp/dynasty_dashboard_preview.html — open it in a browser (scp it locally) to verify the output before going live.

---

## 6. Run for real

```bash
python orchestrator.py
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
0 */4 * * * cd /var/www/vhosts/your-domain.com/dashboard && /var/www/vhosts/your-domain.com/dashboard/venv/bin/python orchestrator.py >> /var/www/vhosts/your-domain.com/dashboard/cron.log 2>&1

# During season (Sep-Jan): hourly on match days (Thu, Sun, Mon)
# 0 * * * 0,1,4 cd /var/www/vhosts/your-domain.com/dashboard && /var/www/vhosts/your-domain.com/dashboard/venv/bin/python orchestrator.py >> cron.log 2>&1
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
├── orchestrator.py       ← run this
├── sleeper_agent.py      ← fetches your Sleeper rosters (bio details, 2-week cache)
├── contract_agent.py     ← Spotrac contract lookup per player (4-week cache)
├── news_agent.py         ← scrapes injury/trade news
├── reasoning_agent.py    ← Anthropic AI analysis
├── validation.py         ← runtime schema check on Claude's structured output
├── common.py             ← shared helpers: atomic JSON/HTML writes, staleness checks
├── dashboard_agent.py    ← renders and writes HTML
├── health_agent.py       ← writes health.json every run; `python health_agent.py` for a summary
├── trade_calculator.php  ← standalone trade fairness tool, reads the JSON caches directly
├── player_cache.json     ← per-player bio + contract cache (self-building, safe to delete)
├── trade_values.json     ← RosterAudit dynasty trade values, sf & 1qb refreshed independently (weekly)
├── player_directory.json ← every fantasy-relevant NFL player + trade value (weekly, powers trade_calculator.php)
├── player_analysis_cache.json ← per-player Claude reasoning cache (see README's Reasoning Agent section)
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
maintains — `player_directory.json` is built by `player_directory_agent.py`,
called automatically from `sleeper_agent.run()`, so there's nothing extra to
schedule.

Requires PHP (PHP-FPM behind nginx, or your host's equivalent) able to
execute `.php` files in whichever directory you deploy it to. This project
has otherwise only ever served static HTML, so if PHP isn't already wired up
on your VPS you'll need to install `php-fpm` and add an nginx block for it
(the exact steps depend on your distro/PHP version — see your OS's php-fpm
package docs).

**Simplest deployment:** drop `trade_calculator.php` in the project root,
next to `player_directory.json` and `trade_values.json` — it defaults to
reading them from its own directory. Add an nginx `location` block pointing at that
directory (same `alias` pattern as the dashboard block in step 8 above), e.g.:

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

## Troubleshooting

**"DASHBOARD_KEY not set"**
→ Ensure .env exists and contains DASHBOARD_KEY=sk-ant-...

**"Permission denied writing to /var/www/..."**
→ Re-run step 4 above, check chown matches your user

**"No leagues found"**
→ Sleeper API may return empty for future seasons before they open. Try --season 2024

**News scraping returns 0 items**
→ Sites may have changed structure. Check pipeline.log for specific errors. The reasoning agent will still run with Sleeper data alone.
→ Or run `python health_agent.py` — it'll name exactly which of the six news sources is failing and why, instead of one blanket "news step" status.

**Not sure if the pipeline is actually healthy**
→ Run `python health_agent.py` for a summary: last run status, per-step errors, per-news-source errors, and which caches have gone stale. `health.json` is rewritten every run (success or failure) so it never reflects a status older than your last cron tick.

**Contract lookups or ESPN news all come back empty**
→ Check PARSE_BOT_API is set in .env and hasn't hit its Parse Bot rate/credit limit (check pipeline.log for 401/429 responses from api.parse.bot).

**Dashboard renders but looks wrong**
→ Run --dry-run, scp the HTML to your local machine and inspect in browser dev tools
