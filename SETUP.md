# Dynasty Dashboard — Setup Guide

## Prerequisites
- Python 3.11+
- Nginx serving lukesplace.net with web root at /var/www/sites/lukesplace.net/httpdocs/
- Your rotated Anthropic API key (DASHBOARD_KEY)
- A Parse Bot API key (PARSE_BOT_API) — powers the Contract Agent's Spotrac lookup and
  the News Agent's ESPN feed. Non-fatal if missing: those two steps just return no
  data and the pipeline continues.

---

## 1. Upload files to your VPS

From your local machine:
```bash
scp -r ./dashboard/ user@lukesplace.net:~/dynasty-dashboard/
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
chmod 600 .env   # restrict read access
```

---

## 4. Create the dashboard directory

```bash
sudo mkdir -p /var/www/vhosts/lukesplace.net/httpdocs/dashboard
sudo chown $USER:www-data /var/www/vhosts/lukesplace.net/httpdocs/dashboard
sudo chmod 775 /var/www/vhosts/lukesplace.net/httpdocs/dashboard
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

Dashboard will be live at: https://lukesplace.net/dashboard/

---

## 7. Schedule with cron (recommended: every 4 hours during season)

```bash
crontab -e
```

Add:
```
# Dynasty Dashboard — refresh every 4 hours
0 */4 * * * cd /var/www/vhosts/lukesplace.net/dashboard && /var/www/vhosts/lukesplace.net/dashboard/venv/bin/python orchestrator.py >> /var/www/vhosts/lukesplace.net/dashboard/cron.log 2>&1

# During season (Sep-Jan): hourly on match days (Thu, Sun, Mon)
# 0 * * * 0,1,4 cd /var/www/vhosts/lukesplace.net/dashboard && /var/www/vhosts/lukesplace.net/dashboard/venv/bin/python orchestrator.py >> cron.log 2>&1
```

Replace YOUR_USER with your actual VPS username.

---

## 8. Nginx location block (if /dashboard/ is not auto-served)

Add to your lukesplace.net server block in /etc/nginx/sites-available/lukesplace.net:

```nginx
location /dashboard/ {
    alias /var/www/vhosts/lukesplace.net/httpdocs/dashboard/;
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
├── dashboard_agent.py    ← renders and writes HTML
├── player_cache.json     ← per-player bio + contract cache (self-building, safe to delete)
├── requirements.txt
├── .env                  ← your API key (never commit)
├── .env.example          ← template
├── README.md             ← architecture overview
├── pipeline.log          ← run log
└── debug/                ← intermediate JSON (--debug flag)
```

See [README.md](README.md) for how the pipeline and each agent works.

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

**Contract lookups or ESPN news all come back empty**
→ Check PARSE_BOT_API is set in .env and hasn't hit its Parse Bot rate/credit limit (check pipeline.log for 401/429 responses from api.parse.bot).

**Dashboard renders but looks wrong**
→ Run --dry-run, scp the HTML to your local machine and inspect in browser dev tools
