# Shopee Product Intelligence

## Purpose

Extend the existing product catalog with timestamped product observations, commission evidence, trend velocity, transparent Win Score ranking, and media-rights checks. This module reuses the current SQLite database and the existing video-job pipeline.

## Approved data workflow

The optional [AddLiveTag connection](addlive-data-api.md) adds an internal data-reference workflow at `/intelligence`. It keeps source/cache provenance and does not replace media permissions or confirm owner-specific earnings. CSV/manual import below remains available.

1. Sign in to the Shopee Affiliate Dashboard using the account owner's KOC/KOL session.
2. Open **Hoa hồng Sản phẩm** and use Shopee's own **Lấy link hàng loạt** workflow for products selected by the account owner.
3. Import the CSV downloaded from Shopee into **Kho sản phẩm** at /catalog.
4. For fields not present in the CSV (e.g. separate Xtra rate, rating count, views, likes, media permissions), open a product's **Dữ liệu trend / Xtra / quyền media** section and save a snapshot.
5. Re-import or update the same products after a meaningful interval (ideally 24 hours or longer) to calculate sales velocity.

The module does not log into Shopee to crawl pages, call undocumented endpoints, bypass CAPTCHA, or scrape product/video content. Shopee's current Service Terms and Affiliate terms restrict automated collection without prior written permission. Use official exports, account-owner-entered values, seller-provided assets, or data sources explicitly licensed for commercial use.

## Data model

- shopee_products: current product details, displayed commission rate, optional base/Xtra split, affiliate URL, media references, rights status, and source.
- shopee_product_observations: timestamped snapshots used for trend calculations.
- shopee_intel_imports: import provenance, file hash, row counts, and row errors.
- Existing products table remains the UI/video catalog. Its revenue field is separate from sold; a revenue amount must not be interpreted as units sold.

## Win Score (0-100)

| Pillar | Weight | Rule |
|---|---:|---|
| Sales momentum | 35 | Daily sales velocity derived from at least two timestamped observations |
| Commission | 25 | Current displayed total rate, or base + Xtra if entered separately |
| Product quality | 20 | Rating and rating-count evidence |
| Price band | 10 | Heuristic VND price band |
| Media readiness | 5 | Image/video reference with rights marked owned, licensed, permission, or official |
| Affiliate readiness | 5 | Custom Link and commission evidence |

Products without two usable observations are marked PROVISIONAL; they are not labelled as confirmed trending. The score is a prioritisation heuristic, not an earnings forecast. Commission rates are time-sensitive and should be refreshed from the Dashboard.

## Media rights

Allowed values: unknown, owned, licensed, permission, official.

The video pipeline receives a Shopee product image only when the media rights field is one of owned, licensed, permission, or official. Video URLs are stored as references; this module does not download or republish another creator's video. Upload a permitted local clip through the existing job media/clip workflow if it is needed in a generated video.

## Existing pipeline integration

The product's Shopee Custom Link is passed to workflow.create_trend_job. This pre-populates the job with the product and affiliate link, so creating the video does not require Shopee Open API credentials. The existing Shopee Video and Facebook Reels publishing flows are unchanged.
