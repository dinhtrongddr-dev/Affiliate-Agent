# Trend Intelligence

Package: `services/agent_core/trend_intel/`. Page: **🔥 Trend** (`/trends`).

## Flow

1. **Collect** (`collectors.py`, worker task `fetch_trends`, every 6 hours or via the button)
   - Google Trends daily RSS, geo=VN: public feed, no key.
   - Google News RSS search, hl=vi gl=VN: article counts in the last 24 hours, the last 7 days and the 7 days before. Each run probes up to 15 topics and seed keywords. The feed returns at most ~100 items, so the counts are lower bounds and a capped feed is not used for growth.
   - YouTube Data API v3 (`AA_YOUTUBE_API_KEY`): 7-day Vietnamese videos per keyword with real view, like and comment counts. Each probe costs 101 units; a daily cap is set by `AA_YOUTUBE_DAILY_UNITS`.
   - Trends MCP REST (`AA_TRENDSMCP_API_KEY`): the `TikTok Trending Hashtags` board for country VN. Monthly cap: `AA_TRENDSMCP_MONTHLY_BUDGET`.
   - TikTok hashtags pasted from TikTok Creative Center.
   - Not used: TikTok's internal Creative Center endpoints, X/Instagram/Shopee scraping, and Meta Ad Library (commercial ads are only available for the EU).
2. **Safety** (`safety.py`): rule filter for lottery and gambling, tragedies and disasters, crime, politics, sensitive health topics and adult content. The AI adds its own `sensitive` verdict. A blocked topic never gets a product or an opportunity score, and is never sent to the AI.
3. **Analyse and score** (`analyzer.py`, `scoring.py`)
   - Growth: latest vs previous observation of a source, or the news 7-day window. Acceleration: change of growth. Spike: z-score of log values against the 3 previous observations. Lifecycle: new / rising / peaking / saturated / stable / declining.
   - Trend score: weighted average of growth, engagement, acceleration, cross-platform, VN relevance, freshness and data confidence (`AA_TREND_WEIGHTS`, JSON).
   - Opportunity score = trend score × (0.3 + 0.7 × affiliate potential / 100). The factor is 0.65 while the affiliate potential is still unknown.
   - Status: `OBSERVED` (time series), `WINDOW` (news window only) or `PROVISIONAL`.
4. **AI intent** (`matcher.analyse_intents`, ChatGPT2API, 15 topics per call, cached 7 days): commercial or not, purchase intent 0–100, product categories, Vietnamese product keywords, video angle and audience. Topics below `AA_TREND_MIN_PURCHASE_INTENT` are marked `NOT_COMMERCIAL`.
5. **Product match** (`matcher.match_products`): products from **Kho sản phẩm**.
   - Matching: keyword coverage plus a fuzzy token-set match.
   - Ranking: product score = 55% relevance + 25% commission + 20% sales momentum (Win Score when available).
   - Products without an affiliate or TikTok link are excluded. Rows imported more than 7 days ago are flagged.
   - For the top 5 topics the AI re-ranks the shortlist.
   - Importing a new CSV re-runs matching.
6. **Make video**: the "🎬 Làm video" button on a matched product creates a trend job. The job carries the affiliate link, the trend angle and the product keywords, and goes through the existing pipeline.

## Tables (additive)

`trend_topics`, `trend_observations`, `trend_products`, `trend_source_usage`. The legacy `trends` table is kept for hand-entered format references.

## Limits

Scores rank topics; they are not view or revenue forecasts. Shopee price, stock and commission are only as fresh as the latest CSV import, because the account has no Open API access.
