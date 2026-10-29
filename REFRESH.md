# Iron Neck Revenue Projection: daily refresh

Live dashboard: https://ironneck-revenue-projection.netlify.app (Netlify site id `be4fcd15-2584-493e-bfba-c84821ac650d`)
Source of truth: GitHub repo `SamKuhn08/Business-Projections` (branch `main`): `model/` (model.py, data.py, refresh.py, targets_parse.py, store.json), `site/` (netlify.toml, public/index.html, public/data.json, public/chart.umd.js) and this file.
`model.py`, `data.py`, `refresh.py`, `targets_parse.py`, `store.json`, `index.html`, `chart.umd.js`, `netlify.toml`, and this file.
The claude.ai Project copy under `claude/revenue-projection/` is a backup only; do not read code or data from it.

Layout
- `model/store.json`  every month of inputs (Shopify, Klaviyo, finance sheet, Seller Board). The only data file.
- `model/data.py`     loads the store. `model/model.py` fits and projects. `model/refresh.py` merges new pulls, validates, reruns the model and writes `site/public/data.json`.
- `site/public/index.html` dashboard. `site/public/chart.umd.js` is Chart.js 4.4.1 (copy from npm, see step 5).

## Steps (run in order, no questions needed)

Let TODAY = today's date (America/Chicago). Let START = the first day of the month two months before TODAY's month (e.g. TODAY 2026-11-05 -> START 2026-09-01).

1. Get the repo: add_repo `SamKuhn08/Business-Projections` (access push), then `git clone --depth 1 https://github.com/SamKuhn08/Business-Projections work` and `mkdir -p work/raw`. Work in `work/model/`.
   START = the EARLIER of (first day of `store.json` `meta.partial_month`) and (first day of the month two months before TODAY). This guarantees every month since the last saved store gets re-pulled.

2. Shopify. Use the Shopify `graphql_query` tool with `shopifyqlQuery` (the `run-analytics-query` tool started failing on 2026-10-27 with "Structured content does not match the tool's output schema"; use it only if GraphQL is unavailable). Several queries fit in one call with aliases, e.g.
   `{ a: shopifyqlQuery(query: "FROM sales SHOW ... ") { tableData { rows } parseErrors } b: shopifyqlQuery(query: "...") { tableData { rows } parseErrors } }`
   Save each query's `tableData.rows` to the named file as `{"rows": [...]}` exactly as returned (rows are objects in column order; refresh.py accepts objects or lists, null = ""). If `parseErrors` is not empty, fix the query:
   - `raw/totals.json`   `FROM sales SHOW orders, gross_sales, discounts, net_sales, total_sales, average_order_value TIMESERIES month SINCE START UNTIL today`
   - `raw/sessions.json` `FROM sessions SHOW sessions, sessions_with_cart_additions, sessions_that_completed_checkout, conversion_rate TIMESERIES month SINCE START UNTIL today`
   - `raw/landing.json`  `FROM sessions SHOW sessions, sessions_that_completed_checkout GROUP BY landing_page_type TIMESERIES month SINCE START UNTIL today`
   - `raw/referrer.json` `FROM sessions SHOW sessions, sessions_that_completed_checkout GROUP BY referrer_source TIMESERIES month SINCE START UNTIL today`
   - `raw/product.json`  `FROM sales SHOW net_sales, quantity_ordered GROUP BY product_type TIMESERIES month SINCE START UNTIL today`
   - `raw/shop_daily.json` `FROM sales SHOW orders, net_sales TIMESERIES day SINCE -40d UNTIL today` (daily orders feed the promo-lift measurement; refresh.py keeps closed days only)

3. Klaviyo (query_metric_aggregates, interval month, timezone America/Chicago, range START to tomorrow 00:00):
   - email revenue: metric `RydRc9` (Shopify Placed Order), measurements `sum_value`, by `$attributed_channel`; take the `$email_channel` series.
   - signups: metric `YgDjTw` (Subscribed to Email Marketing), measurement `count`.
   - The response's first date bucket is the month BEFORE START with a near-zero value; drop it. Save
     `raw/klaviyo.json` = `{"months": ["YYYY-MM", ...], "email_rev": [...], "signups": [...]}` for START's month through TODAY's month.

4. Finance sheet. Google Drive search `title contains 'Iron Neck Master'` and use the current year's master (2026: id `1_hma6ts0ZonGBGXRqQGMcE7eTqBFHdZlngbULTmo_1E`; in 2027 look for "2027 Iron Neck Master"). Read the monthly block at the top with get_file_metadata (snippetVerbosity DETAILED). Columns: Months, Net Shopify Rev, Net Amazon Revenue, Net Revenue, Cogs, Gross Profit, FB Spend, GG Spend, Amazon Spend, SMA Ads, SMA Other, Administration, Payroll, Other, Net Income, ..., Σ Shopify Goal, ..., Σ Amazon Goal.
   For every COMPLETED month (before TODAY's month) whose row is filled, write to `raw/finance.json`:
   `{"YYYY-MM": {"sheet_shop": NetShopifyRev, "amazon": NetAmazonRevenue, "meta": FBSpend, "google": GGSpend, "amz_ads": AmazonSpend, "fixed": SMAOther+Administration+Payroll+Other, "goal_shop": ΣShopifyGoal, "goal_amz": ΣAmazonGoal}}`
   For the current and future months write only `fixed`, `goal_shop`, `goal_amz` from the sheet's projection rows (those rows have Net Shopify Rev / Net Amazon Revenue but no goal columns: use those two values as goal_shop / goal_amz). Never write projected values into `amazon`, `meta`, `google`, `amz_ads` or `sheet_shop`.

4a. Month to date (same sheet, the daily rows below row 15 of "2026 Forcast Sheet"). For every day of TODAY's month BEFORE TODAY (today and later rows are grayed projections: ignore them), sum Net Shopify Rev, Net Amazon Revenue, FB Spend, GG Spend, AMZ Spend and write
   `raw/mtd.json` = `{"month": "YYYY-MM", "through": "YYYY-MM-DD", "days": N, "shop": .., "amazon": .., "meta": .., "google": .., "amz_ads": ..}`. On the 1st of a month (no closed days) skip this file. The model uses it to nowcast the month in progress.

4c. O&F Targets tab (official P&L; file id `1wSEMec5fEH4VL4yYSpfzqeimqNBphoObVAZlS19sTHo`, tab "Targets"). Only needed when a month has closed since the saved store (TODAY's month differs from the store's `meta.partial_month`) or Sam says the tab was updated. Export the tab as text/CSV to `work/targets_export.txt` and run
   `python3 targets_parse.py ../targets_export.txt ../raw LAST_CLOSED` (LAST_CLOSED = the month before TODAY, YYYY-MM). It writes `raw/targets.json` (actuals: Shopify, AMZ, D/D/O, COGS, ad spend, other SMA, payroll, G&A, other, net income) and `raw/plan.json` (the Est. plan columns). Targets AMZ becomes the stored Amazon figure. If the tab is not yet filled for the newly closed month, skip it; the finance sheet values stand in until it is.

4b. Seller Board fallback (Seller Board MCP, account_id `5aaaf62b-5d4a-11ee-85dc-e4434b601800`). For every COMPLETED month since START that the finance sheet has NOT filled yet, call `dashboard_period` with that month's UTC period (start = 00:00 on the 1st, end = 00:00 on the last day, unix seconds; one call can hold several months). If it returns status "preparing", call again with identical arguments. From `data.Info` take `Sales`, `ProductRefunds` (negative) and `Advertising` (negative) and add to `raw/finance.json` for that month:
   `"amazon": round((Sales + ProductRefunds) * store.meta.amazon_ratio)`, `"amz_ads": round(-Advertising)`, plus `"sb_sales"`, `"sb_refunds"`, `"sb_ads"` (raw, positive). When the sheet later fills the month, its value overwrites this.

4d. Amazon daily, finished sales and closed years (Seller Board `dashboard_period`, account above). Run `python3 sale_lift.py --todo TODAY` in `work/model/`: it prints the periods to pull.
   - `daily`: one period per day (up to 12 periods per call; re-call with identical arguments while status is "preparing"). Write `raw/amazon_daily.json` = `{"YYYY-MM-DD": [Sales, Orders, Units, -Advertising]}` (Advertising is negative in Seller Board; store it positive). Feeds the Nov/Dec daily view's October baseline and last-year line.
   - `sale_windows` (usually empty): for each, pull its `period` (it carries compare_to_start/compare_to_end) and append to `raw/amazon_sale_windows.json` a list item `{start, end, prior_start, prior_end, sales: Sales, prior_sales: compare Sales}` using the start/end/prior dates printed. Feeds the Sale lift card.
   - `years` (only in January): pull the year's period and write `raw/amazon_years.json` = `{"YYYY": {"sales": Sales, "extra_sale": []}}`. Feeds the Holiday sale's "average MSRP day of that year".

5. Run `python3 refresh.py ../raw TODAY`. It must print `OK: no validation problems`. If it prints PROBLEMS, fix the raw files (usually a missed month or a wrong column) and rerun. If still failing, stop: do not deploy, and report the problems.
   `site/public/chart.umd.js` is in the repo; if it is missing, `npm i chart.js@4.4.1` in a scratch folder and copy `node_modules/chart.js/dist/chart.umd.js`.

6. Deploy `site/` to Netlify: call the Netlify deploy-site tool with siteId `be4fcd15-2584-493e-bfba-c84821ac650d`, then run the `npx -y @netlify/mcp@latest --site-id ... --proxy-path ...` command it returns from inside `site/`. Confirm `https://ironneck-revenue-projection.netlify.app/data.json` shows `generated` = TODAY (WebFetch).

7. Commit and push from `work/`: `git add model/store.json model/shop_daily.json model/amazon_daily.json model/amazon_sale_windows.json model/amazon_years.json model/projection_log.json site/public/data.json site/public/history.json` (plus any code you had to fix), commit as `Daily refresh TODAY`, `git push origin HEAD:main`. If the push is rejected as non-fast-forward, `git pull --rebase origin main` and push again. Do not write to the claude.ai Project.

8. Report in two lines: projected current-year total revenue vs the same period last year (not vs plan), and anything that moved more than 5% vs yesterday's `data.json` (compare the first three projected months' `d2c` and `amazon`; fetch yesterday's values from the live data.json BEFORE deploying).

## Total business, brand search, D/D/O (added 2026-10-16)
- History for all channels and net income = O&F Targets tab (`tgt_*`), Jan 2023 to the last closed month. Plan = Targets "Est." columns (`goal_shop`, `goal_amz`, `goal_ddo`, `goal_ni`); this replaced the Iron Neck Master goals.
- D/D/O: projected flat at the Jan 2026+ average. Sam: pre-2026 D/D/O included Amazon resellers (six-figure months) that are gone, so never use earlier history.
- Net income projection = (Shopify + Amazon + D/D/O) x (1 − COGS% of the last 12 closed months) − (Meta + Google + Amazon ads) x ad_ratio − fixed (finance sheet fixed costs).
- Brand search: `gsc_brand_clicks` / `gsc_brand_impr` = Search Console query "iron neck" (web). 2024 to May 2025 from Sam's "2025 Branded Search on Google" sheet, Jun 2025 on from a GSC export. No connector: when Sam uploads a new GSC Performance export (Chart.csv, daily), sum it by month into `raw/brand.json` = `{"YYYY-MM": {"gsc_brand_clicks":..,"gsc_brand_impr":..,"gsc_days":..}}` (only full months, plus the current month with its day count). `amz_brand_searches` from the Amazon tab of that sheet (2024 Aug to 2025 Sep).
- Rogan (Jan to Feb 2025), measured vs a Jan to Feb 2024 baseline scaled by the rest-of-year change: about +15.5k Google brand clicks, +$377k Shopify, +$171k Amazon (~$35 per extra click). Masked from every fit.
- Google spend 2025 filled from the Master's "2025 Forecast" tab (GG Spend).

## Amazon history (Seller Board)
- Jan 2023 to Sep 2026 pulled from Seller Board `dashboard_period` (all marketplaces, USD) on 2026-10-16. Store fields `sb_sales`, `sb_refunds`, `sb_ads` hold the raw values.
- Since 2026-10-16 `amazon` = O&F Targets AMZ for every closed month (official books; Jan 2025 = 240,580, which resolves the Master's 200,488 vs Seller Board 246,995 mismatch). Seller Board raw values stay in `sb_*`; Step 4b only fills months that neither the Targets tab nor the finance sheet has yet.
- `amz_ads` = sheet Amazon Spend where filled, else Sellerboard Advertising. Sellerboard has no ad data before Jun 2023, so Jan to Jun 2023 `amz_ads` is left empty.
- Model: `project_amazon` in model.py. Own seasonal index with recency weight^3 (Amazon Nov/Oct ratio fell 2.6x → 2.0x → 1.25x over 2023 to 2025), level = last 3 de-seasonalised months, slope = trailing-12-month YoY, damped. Rolling backtest MAPE 28.9% (Theil-Sen alternative 33%).

## SMS (Attentive) and Meta history
- Attentive has no connector. SMS fields in the store (`sms_rev`, `sms_signups`, `popup_impr`, plus `sms_camp_rev`, `sms_flow_rev`, `popup_email_signups`) come from Attentive exports Sam uploads (campaigns, journeys, sign-up units). When new exports arrive, aggregate them by month (campaign revenue + journey revenue = `sms_rev`; sign-up units SMS Subscribes = `sms_signups`, Impressions = `popup_impr`) and write them into store.json, then commit and push the store to the repo. The daily refresh never overwrites these fields.
- Meta spend history (Oct 2023 to Dec 2025) came from the Meta Ads connector, account "Kit Ad Account" (1850888784987005). The finance sheet is still the source for months it covers.

## Adding new history (ad spend, Search Console)
Put monthly values into `store.json` under each month (`meta`, `google`). Google spend 2023 to 2025 is still missing. New fields for brand search are not wired into the model yet: add them to the store as `gsc_brand_clicks` / `amz_brand_searches` and update `model.py` when Sam sends them.

## Promos, Prime and the track record (added 2026-10-19)
- `model/promos.json` is the promo calendar (from Sam's 2024 to 2026 Promotions & Sales docs; 2023 inferred from order spikes; 2027 assumed = 2026 on the same holidays, Prime Day in June). When Sam shares a new year's promo doc, add its sales here (type, start, end, `pro` = $ off the 3.0 Pro, `amazon` = Prime dates). Nov-Dec sales stay in seasonality; every other sale is a movable event.
- Shopify lift per sale is measured automatically from `model/shop_daily.json` (events.py). Amazon only responds to Prime events: after each Prime Day / October Prime has run plus 5 weeks, measure it with Seller Board `dashboard_period` using 4 periods (28 days before, the Prime days, the 7 days after, days 8-35 after) and append to `model/amazon_events.json` as `{type, start, end, base_day, event_day, extra, lift_days}` where base_day = (before + days 8-35) sales / 56, extra = (Prime + 7 days after) - base_day x (Prime days + 7), lift_days = extra / base_day.
- Shopify carries a bias correction (model.shop_bias): the mean log error of its own last 54 realised 1-3 month forecasts.
- Fixed costs are projected from the last 3 closed months on the Targets tab. Nothing in the forecast reads the O&F plan or the finance sheet's forward rows (Sam, 2026-10-19: the model forecasts from history only; plan is a comparison option on the page).
- refresh.py runs `backtest.py` after the model and writes the track record into data.json (`track`), shown on the Method & accuracy tab: every month start since Jan 2025 refit on data known then, scored against Targets actuals and a same-month-last-year benchmark.

## Channels tab (added 2026-10-26; refresh MONTHLY, on the first daily run of each month)
Website (Shopify) orders split by traffic channel. Code in `channels/`, the page reads `site/public/channels.json` (aggregated by month and channel, no customer-level data). The per-order file `channels/orders.json` holds Shopify customer IDs, so it stays out of git (see .gitignore) and lives only in the working session.
1. Orders: page the Shopify Admin GraphQL `orders` query (`first: 250`, `sortKey: CREATED_AT`, `query: "created_at:>=YYYY-MM-DD"`), following `endCursor` until `hasNextPage` is false. Field selection (aliases matter, build.py reads them):
   `pageInfo { hasNextPage endCursor } nodes { c: createdAt a: currentSubtotalPriceSet { s: shopMoney { a: amount } } u: customer { i: id o: numberOfOrders e: defaultEmailAddress { m: marketingState } } j: customerJourneySummary { d: daysToConversion f: firstVisit { s: source u: utmParameters { s: source m: medium } } l: lastVisit { s: source u: utmParameters { s: source m: medium } } } }`
   Save each page as `channels/raw/orders_<n>.json`, run `python3 extract_orders.py` (merges into orders.json; re-pulled orders replace old copies). orders.json is never committed (customer ids), so a scheduled run always starts without it: pull from 2025-01-01 (about 70 pages of 250). Hand the paging to a subagent so the pages land in tool-result files instead of your context, then point `extract_orders.py` at them (it also reads `channels/raw/*.json`). Do this on the first daily run of each month only; on other days skip the Channels steps.
2. Sessions: ShopifyQL `FROM sessions SHOW sessions, sessions_that_completed_checkout GROUP BY month, referrer_source, utm_source, utm_medium SINCE <first of last month> UNTIL today`; replace those months in `sessions_raw.json` (`[month, referrer_source, utm_source, utm_medium, sessions, checkouts]`).
3. Blog: same query with `WHERE landing_page_type = 'Blog Article'` grouped by month, referrer_source; replace those months in `blog_sessions.json`.
4. `python3 build.py` (Meta and Google spend from `model/store.json`), commit code + `site/public/channels.json`, deploy.
Notes: Meta ads were not consistently UTM-tagged before March 2026 (those orders mostly land in Direct). Affiliate cost = 15% commission estimate (`AFF_COMMISSION`). About 15% of orders have no journey (tracking declined) and count only in "All website". Shopify journeys look back at most 30 days.

## Sale lift (Drivers tab, added 2026-10-27)
- `model/sale_lift.py` builds `data.json` → `sales` on every refresh: for each documented sale since 2024 that has finished (from `promos.json`), Shopify + Amazon revenue during the sale vs the same number of days right before it (evergreen), and the multiplier sale / evergreen. Descriptive only, not used by the forecast.
- Shopify comes from `shop_daily.json` automatically. Amazon comes from `model/amazon_sale_windows.json` (Seller Board `dashboard_period`, one period per sale with `compare_to_start/compare_to_end` = the prior window; Amazon uses the Prime dates when `promos.json` has `amazon` dates). After a new sale ends (next: BFCM 2026, Holiday 2026), append its row `{start, end, prior_start, prior_end, sales, prior_sales}`; until then the refresh reports "a finished sale has no Amazon window".
- Excluded: Father's Day 2026 (gift with purchase, Sam 2026-10-27) and 2023 (no promo doc). A sale is flagged when at least a quarter of its prior days were another sale (Holiday after BFCM, Father's Day 2025 after Memorial Day, Prime Day 2024 after July 4th); flagged sales are shown but left out of totals by default.
- Holiday sale (Sam 2026-10-27): it starts right after BFCM, so its evergreen = that year's average MSRP day (all days of the calendar year not on a documented sale, Shopify + Amazon) x the holiday sale's days. Shown in gold, counted in totals. Needs the full year closed: after Dec 31 2026, add `"2026": {"sales": <Seller Board Sales for 2026>, "extra_sale": []}` to `model/amazon_years.json` along with the Holiday 2026 window in `amazon_sale_windows.json`.

## Nov / Dec day by day (added 2026-10-27)
- `model/daily.py` runs inside refresh.py after the model: for the coming Nov + Dec it builds a daily forecast (Shopify net sales, Shopify orders, Amazon) and writes `data.json` → `daily`. Click the Nov or Dec bar on the Projection tab to open it.
- How: full-price (MSRP) days = this October's average full-price day (no sale, not within 7 days after one). Sale days borrow each past season's multiplier of its own October base (2023-2025, weighted by recency ** 6): early access by days-before-Thanksgiving (3-day average), Thanksgiving to Cyber Tuesday by exact offset from Thanksgiving, Holiday by calendar date (level x date shape). Dates come from `promos.json` (2026: BFCM Nov 2 - Dec 2, Holiday Dec 3 - 31 per the updated doc; Veterans Day Nov 9-15 is not a revenue event per Sam).
- The monthly model still sets the season size (Nov + Dec total per channel); the daily calendar only decides how it splits between Nov and Dec, by scaling the lift above full price (full-price days stay at October's level). refresh.py overwrites the Nov/Dec `orders`, `d2c` and `amazon` in `fut` with that split (originals kept in `daily.model_months`). Once Nov starts, the split is no longer applied.
- Backtest of the daily pattern alone (forecast from Nov 1, Shopify): 2024 Nov -4% / Dec -25%, 2025 Nov +18% / Dec +57%. It gets the Nov vs Dec split closer than the monthly model did for 2025 (51% vs 47% in Nov, actual 58%), which is why it is used for the split, not the size.
- Data: `shop_daily.json` (Shopify) and `model/amazon_daily.json` (Seller Board daily Sales, Oct-Dec 2023-2025 + Oct 2026). Amazon had $0 sales Nov 20-26 2025 (listing down): excluded from the pattern (`AMZ_OUTAGE`), shown as-is in the last-year line. Ad spend per day: Meta/Google follow Shopify's daily multiplier ** 0.5, Amazon ads follow Amazon's (no daily Meta history in the connector); month totals unchanged.

## Projection history tab (added 2026-10-29)
- Every refresh appends that day's base-case projection (no levers) for every projected month to `model/projection_log.json` (one snapshot per day; a rerun the same day replaces it) and rebuilds `site/public/history.json`. Commit both. Oct 16-29 2026 were backfilled from git history.
- The tab also shows reconstructed snapshots: today's method refit on the data closed before the 1st of each month of the current year (`projection_log.reconstructed`, uses backtest.forecast_from with `model.FULL_HORIZON`). They do not include the Nov/Dec calendar split. Actuals for closed months come from the Targets tab (backtest.actual).
