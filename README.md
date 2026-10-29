# KB Securities Open API connection

Only the connection part was kept. Everything else (screens, backtests, pages) was removed on 2026-10-26; a backup zip exists.

- `kb_api.py`  token handling and `call(api, body)` (standard library only)
- `test_connection.py` / `.bat`  connection test (server date)
- `.env.txt`  fill in KB_APP_KEY / KB_APP_SECRET (never commit or share this file)

Known facts from earlier work (not re-verified in this reset):
- POST /api/v1/<api>, body wrapped as dataHeader (ipAddr, macAddr) and dataBody.
- Daily chart IVS11560: info_ccd 1 = raw price, 2 = adjusted (KOSPI/KOSDAQ only); mkt_clsf 0 KOSPI / 1 KOSDAQ; inq_cnt is a 4-character string; broken dates can appear in rows.
- Trading-value ranking IVU10210, quote IVU10140, trade restriction SIQM4900, server date SZQM0771, investor net buying IVU10430 (unit and sign unverified).

## Screener (screener.py)
Built from the screening prompt. `run_screen.bat` runs it and opens `result\screen_result.html`; `result\screen_result.csv` has all rows.
- Options: `py screener.py --cap-min 500` (market cap minimum in eok won, 500-3000), `--rank-count 400`, `--show 10`.
- Conditions 1-7 are applied in order and the cumulative counts are shown. Score items and the summary of the rules are printed in the page.
- Not confirmable through the API (written in the page as unconfirmed): unfaithful-disclosure, investment-attention (hwangi) and abnormal-surge stocks. Put them in `exclusions.csv` if you want them excluded.
- Preferred shares / SPAC / ETF are filtered by code, name and type text (heuristic).
- Trading value is the KRX daily-candle value; the ranking API value (includes the alternative exchange) is used only as a first cut.
- Tested only against a local mock server, not against the real KB server yet.

## GitHub Pages (docs/index.html + docs/screen.json)
`run_screen.bat` writes `docs/index.html` and `docs/screen.json` (plus `result/screen_result.csv`, local only).
The page loads `screen.json` when served over http(s); opened as a local file it uses the copy embedded in the HTML.
1. Create a GitHub repository, then in this folder: `git init`, `git add .`, `git commit -m init`, `git remote add origin <url>`, `git push -u origin main`. `.env.txt`, `result/`, `data/` and the token cache are in `.gitignore` - check `git status` before the first commit that `.env.txt` is not listed.
2. GitHub: Settings > Pages > Source "Deploy from a branch", Branch `main`, folder `/docs`.
3. Each time: `run_screen.bat`, then `publish.bat` (git add docs, commit, push). The page updates in about a minute.
Notes: Pages sites are public even from a private repo on most plans, so anyone with the URL can read the results (the page has a noindex tag). Check the KB Securities Open API terms before publishing its data.
