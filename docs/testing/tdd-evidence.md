# TDD Evidence

Journeys derived per milestone. One RED + GREEN excerpt per milestone below.

## M0 - Bootstrap

Smoke test green on Python 3.15.0, coverage gate active (80%).

```
tests/unit/test_smoke.py .                                               [100%]
Required test coverage of 80% reached. Total coverage: 100.00%
1 passed
```

## M1 - normalize.py

Journey: as the pipeline, I normalize raw scraped strings (city, work mode,
salary, tags, title, company) into canonical values and a stable cross-source
fingerprint, so duplicates across sources collapse into one logical job.

RED (module did not exist):

```
tests/unit/test_normalize.py:3: in <module>
    from ptjobs.normalize import (...)
E   ModuleNotFoundError: No module named 'ptjobs.normalize'
1 error in 0.10s
```

GREEN (after implementing `src/ptjobs/normalize.py`):

```
tests/unit/test_normalize.py ........................................    [100%]
56 passed in 0.04s
```

ruff: All checks passed. mypy (strict): Success, no issues.

Guarantees:

| # | What is guaranteed | Test | Type | Result |
|---|--------------------|------|------|--------|
| 1 | PT/EN city names map to one slug; noise -> None | `test_normalize.py::TestNormalizeCity` | unit | PASS |
| 2 | remote/hybrid/onsite classified PT+EN, else unknown | `TestNormalizeWorkMode` | unit | PASS |
| 3 | Salary ranges, monthly->yearly, k-suffix, EUR parsed | `TestParseSalary` | unit | PASS |
| 4 | Known stack extracted, aliased, deduped, sorted | `TestNormalizeTags` | unit | PASS |
| 5 | Gender markers and legal suffixes stripped | `TestNormalizeTitleCompany` | unit | PASS |
| 6 | Fingerprint stable across sources, differs on role | `TestFingerprint` | unit | PASS |

Code review blocked the first pass: parse_salary
corrupted separators and mixed per-number k; normalize_tags dropped
punctuation-trailing tags and .NET; the gender-marker regex mangled slashed
titles; the fingerprint separator was not escaped. All turned into failing
cases first (13 RED), then fixed. Second pass: 56 passed, ruff clean, mypy
strict clean, 97% coverage.

Known gap: fuzzy dedup (pg_trgm) not implemented; exact fingerprint only.
Salary parser is conservative: foreign currency and hourly rates return
(None, None) rather than storing a wrong EUR yearly value.

## M2 - sources/http.py

Journey: as a scraper, I fetch source pages politely (crawl-delay throttle)
and survive transient failures (retry 429 and 5xx with backoff, honor
Retry-After), while failing fast on 4xx, so one flaky response does not abort
a run and a hostile site is not hammered.

RED (module did not exist):

```
tests/unit/test_http.py:5: in <module>
    from ptjobs.sources.http import HttpClient
E   ModuleNotFoundError: No module named 'ptjobs.sources.http'
1 error
```

GREEN (after implementing http.py; clock and sleep injected, httpx.MockTransport):

```
tests/unit/test_http.py ........                                         [100%]
8 passed in 0.11s
```

Full suite: 65 passed, 97% coverage. ruff clean, mypy strict clean.

Guarantees:

| # | What is guaranteed | Test | Type | Result |
|---|--------------------|------|------|--------|
| 1 | 200 returned with no retry, no sleep | `test_returns_ok_without_retry` | unit | PASS |
| 2 | Identifiable User-Agent sent | `test_sets_identifiable_user_agent` | unit | PASS |
| 3 | 503 retried then succeeds, exp backoff | `test_retries_on_503_then_succeeds` | unit | PASS |
| 4 | Retry-After header honored over backoff | `test_honors_retry_after_header` | unit | PASS |
| 5 | 404 not retried, raises SourceError | `test_does_not_retry_on_404` | unit | PASS |
| 6 | Persistent 500 exhausts retries then raises | `test_exhausts_retries_on_persistent_500` | unit | PASS |
| 7 | Transport error retried | `test_retries_on_network_error` | unit | PASS |
| 8 | min_interval enforced between requests | `test_throttles_between_requests` | unit | PASS |

### M2 review gate (second pass)

Blocked the first pass with real defects, each turned into a failing test
first (4 RED), then fixed:

- Retry-After was unbounded: a server sending `Retry-After: 86400` blocked the
  crawler. Now capped at `MAX_RETRY_AFTER` (120s); larger values raise
  SourceError immediately. (`test_gives_up_on_absurd_retry_after`)
- `float(retry_after)` could crash on a non-ASCII digit header. Guarded with
  `isascii() and isdigit()`. (`test_invalid_retry_after_falls_back_to_backoff`)
- Throttle measured start-to-start, defeating the delay after a slow request.
  Now stamped in a `finally`, measured from request end.
  (`test_throttle_measured_from_request_end`)
- 3xx was treated as success. Now redirects are followed by default and only
  `< 300` counts as success. (`test_follows_redirects`)
- Non-transport httpx errors escaped unwrapped. Now catches `httpx.RequestError`
  and wraps in SourceError. (`test_wraps_non_transport_request_error`)

Skipped as gold-plating (we control our own callers): case-insensitive
User-Agent merge, negative max_retries validation.

Second pass: 70 passed, ruff clean, mypy strict clean, 97% coverage.

## M3 - source parsers and fetchers

Journey: as the pipeline, for each supported source I fetch its listing and
parse it into RawJob records, and if a site's markup changes the parser raises
ParseError instead of silently returning nothing.

Sources built: net_empregos (RSS, xml.etree), itjobs (HTML, Parsel), linkedin
(Playwright + playwright-stealth guest cards, Parsel). Contract tests use
trimmed real captures in tests/fixtures/<source>/.

RED (modules did not exist):

```
tests/unit/test_parsers.py:6: in <module>
    from ptjobs.sources import itjobs, linkedin, net_empregos
E   ImportError: cannot import name 'itjobs' from 'ptjobs.sources'
```

GREEN:

```
tests/unit/test_parsers.py .......                                       [100%]
tests/unit/test_fetch.py ......                                          [100%]
tests/integration/test_linkedin_browser.py .   (real chromium, 4.2s)
83 passed, 94% coverage. ruff clean, mypy strict clean.
```

Guarantees:

| # | What is guaranteed | Test | Type | Result |
|---|--------------------|------|------|--------|
| 1 | net-empregos RSS parsed; id, company, zona extracted | `test_parsers.py::TestNetEmpregos` | unit | PASS |
| 2 | Empty/broken feed raises ParseError | `TestNetEmpregos::test_*_raises` | unit | PASS |
| 3 | itjobs listing parsed; id from /oferta/<id>/ | `TestItjobs::test_parses_items` | unit | PASS |
| 4 | itjobs missing listing raises ParseError | `TestItjobs::test_missing_listing_raises` | unit | PASS |
| 5 | linkedin guest cards parsed; urn id, company, location | `TestLinkedin::test_parses_cards` | unit | PASS |
| 6 | linkedin empty html raises ParseError | `TestLinkedin::test_empty_html_raises` | unit | PASS |
| 7 | net fetch decodes and parses the feed | `test_fetch.py::test_net_empregos_fetch_yields_jobs` | unit | PASS |
| 8 | itjobs fetch paginates, stops on empty page | `test_itjobs_fetch_paginates_until_empty` | unit | PASS |
| 9 | itjobs fetch raises if first page breaks | `test_itjobs_fetch_raises_when_first_page_breaks` | unit | PASS |
| 10 | linkedin search URL well-formed | `test_linkedin_search_url` | unit | PASS |
| 11 | linkedin fetch paces between keywords | `test_linkedin_fetch_uses_injected_fetcher_and_paces` | unit | PASS |
| 12 | linkedin fetch_html drives real Chromium | `test_linkedin_browser.py` | integration | PASS |

Decision pending: sapo emprego /offers is JavaScript-rendered (no job markup
in the server HTML, no RSS, no JobPosting ld+json), so it needs Playwright like
LinkedIn. The user named only LinkedIn, net-empregos and itjobs in words, so
sapo is not built yet.

### M3 review gate

Blocked with real defects, each turned into a failing test first, then fixed:

- linkedin.fetch dropped every earlier keyword's jobs when one keyword returned
  an empty (no-results) page, since parse raises ParseError on zero cards. Now
  tolerant per keyword: partials kept, raises only if all keywords fail.
  (`test_linkedin_fetch_keeps_partials_when_one_keyword_empty`,
  `test_linkedin_fetch_raises_when_all_keywords_empty`)
- itjobs.fetch threw away page-1 results on a SourceError from a later page, and
  a site that ignores `?page=N` looped appending duplicates. Now catches
  SourceError past page 1 (break) and stops on no new external_ids.
  (`test_itjobs_fetch_keeps_page1_when_later_page_errors`,
  `test_itjobs_fetch_stops_when_page_repeats`)
- net_empregos decoding depended on the HTTP Content-Type charset; a missing
  charset corrupted iso-8859-1 accents. fetch now passes raw bytes so expat
  honors the XML declaration.
  (`test_net_empregos_fetch_preserves_iso8859_accents`,
  `test_net_empregos_parse_bytes_honors_declaration`)

Hardening (no behavior regression): itjobs url via urljoin; net company looks
up both http and https Dublin Core namespaces; net catches ValueError too;
linkedin reads data-entity-urn via card.attrib (not descendant css).

Skipped (fails loud by design): LinkedIn goto status check. A 429 or authwall
page yields no cards, raising ParseError, which marks the run failed and alerts
the admin, which is the intended monitoring behavior.

Second pass: 89 passed, ruff clean, mypy strict clean, 94% coverage.

## M4 - models, migration, pipeline

Journey: as the scheduler, I run a source, normalize each posting, upsert it
idempotently keyed on (source, external_id), drop non-tech postings from
general sources, and record a ScrapeRun (ok/empty/failed) so failures alert the
admin and monitoring can see drift.

Migration: Alembic autogenerate from models. Read before applying (global rule):
`grep -niE "drop table|drop column|truncate"` returned nothing; upgrade() is
only create_table/create_index. `alembic upgrade head` applied on Postgres 18.6;
jobs.id confirmed BIGSERIAL (`nextval('jobs_id_seq')`).

Tests use a dedicated ptjobs_test database; each test runs in a transaction
rolled back on teardown (conftest.py, savepoint join mode).

RED:

```
tests/integration/test_pipeline.py:5: in <module>
    from ptjobs.pipeline import run_source
E   ModuleNotFoundError: No module named 'ptjobs.pipeline'
```

GREEN: 94 passed, 89% coverage, ruff clean, mypy strict clean.

Guarantees:

| # | What is guaranteed | Test | Type | Result |
|---|--------------------|------|------|--------|
| 1 | Insert normalizes city/work_mode/salary/tags/fingerprint; run ok | `test_run_source_inserts_and_normalizes` | integration (pg) | PASS |
| 2 | Re-run is idempotent: no dup, first_seen kept, jobs_new=0 | `test_run_source_is_idempotent` | integration (pg) | PASS |
| 3 | No postings marks run empty, no rows written | `test_run_source_empty_marks_empty` | integration (pg) | PASS |
| 4 | Fetch error marks failed, records message, fires alert | `test_run_source_failure_marks_failed_and_alerts` | integration (pg) | PASS |
| 5 | Non-tech net-empregos postings dropped (found counted, new 0) | `test_run_source_drops_non_tech_netempregos_jobs` | integration (pg) | PASS |

### M4 review gate

Blocked with three HIGH defects, each turned into a failing test first, then
fixed:

- A duplicate external_id within one fetch raised a Postgres CardinalityViolation
  (ON CONFLICT cannot touch a row twice). Now rows are deduplicated on
  external_id before the upsert. (`test_run_source_dedups_within_batch`)
- jobs_new counted every occurrence, not distinct rows. Fixed by the same dedup
  plus `len(rows) - len(existing)`.
- A DB error during the upsert aborted the transaction, so the failed ScrapeRun
  and the admin alert were lost. The fetch+upsert now run inside a savepoint
  (`session.begin_nested()`); on error only the job writes roll back, the run is
  recorded, and the alert fires.
  (`test_run_source_db_error_records_failed_run_and_alerts`)

Also fixed: last_seen_at/finished_at use `func.clock_timestamp()` not `now()`
(now() is frozen at transaction start, so a re-scrape could not prove the
refresh); the idempotent test now asserts last_seen advanced. Rows are written
with the `source` argument, not raw.source. The admin alert call is guarded so
a failing Discord webhook cannot discard the failed-run record.

Noted as known ceilings (YAGNI at current volumes): chunking the multi-row
INSERT below psycopg's 65535 bind-param limit (~4681 rows; our fetches stay far
under), and COALESCE-preserving nullable fields on re-scrape (our listing
scrapers emit the same fields every run).

Second pass: 96 passed, 89% coverage, ruff clean, mypy strict clean.

## M5 - API and OpenAPI

Journey: as a consumer, I query active jobs with filters and pagination and get
them deduplicated across sources, open one job to see its siblings on other
sources, check each source's last run, and read the whole contract from Swagger.

Endpoints: GET /health, /jobs (q, city, work_mode, tag, min_salary, source,
since, page, size; DISTINCT ON fingerprint; active = last_seen within 7 days),
/jobs/{id} (with also_on), /sources (latest run per source). Errors use the
envelope {"error": {code, message, details}} via AppError and
RequestValidationError handlers. OpenAPI dumped by `python -m ptjobs openapi`
to docs/openapi.json (versioned); `alembic check` reports no drift.

RED: `ModuleNotFoundError: No module named 'ptjobs.api'`.
GREEN: 106 passed, 92% coverage, ruff clean, mypy strict clean.

Guarantees:

| # | What is guaranteed | Test | Type | Result |
|---|--------------------|------|------|--------|
| 1 | /health pings the DB and returns ok | `test_health_ok` | integration (pg) | PASS |
| 2 | /jobs lists active jobs with tags | `test_jobs_lists_active` | integration | PASS |
| 3 | Stale jobs (>7d) hidden | `test_jobs_hides_stale` | integration | PASS |
| 4 | Same fingerprint deduped to one | `test_jobs_dedup_by_fingerprint` | integration | PASS |
| 5 | city/work_mode/tag/min_salary/q/source filters | `test_jobs_filters` | integration | PASS |
| 6 | Pagination returns total/page/size | `test_jobs_pagination` | integration | PASS |
| 7 | Job detail lists also_on siblings | `test_job_detail_with_also_on` | integration | PASS |
| 8 | Missing job returns 404 envelope | `test_job_detail_404_envelope` | integration | PASS |
| 9 | Bad query returns 422 envelope | `test_jobs_validation_error_envelope` | integration | PASS |
| 10 | /sources returns latest run per source | `test_sources_last_run` | integration | PASS |

### M5 review gate (API review)

Blocked with two HIGH and several MEDIUM defects, each turned into a failing
test first, then fixed:

- User input caused unhandled 500s with no error envelope, and there was no
  generic/HTTP exception handler. Added Exception and StarletteHTTPException
  handlers (unknown routes now return the envelope), bounded page (<=10000),
  min_salary (<=int32), job_id (1..int64), and max_length on string params;
  NUL bytes stripped from q/tag.
  (`test_unknown_route_returns_envelope`, `test_overflow_params_return_422_not_500`,
  `test_nul_byte_in_query_is_handled`)
- /health returned a plain 500 when the DB was down; now returns a 503
  DB_UNAVAILABLE envelope. (`test_health_returns_503_when_db_down`)
- /sources returned duplicate rows when two runs shared started_at; now DISTINCT
  ON source ordered by started_at desc, id desc (also drops the self-join).
  (`test_sources_dedups_runs_with_same_started_at`)
- Pagination had no tiebreaker, so tied first_seen_at could repeat or drop rows
  across pages; added id desc tiebreakers inner and outer.
  (`test_pagination_is_stable_with_tied_timestamps`)
- `since` replaced the active window, resurrecting stale jobs; now narrows only
  (max(since, now-7d)) and naive values are treated as UTC.
  (`test_since_cannot_resurrect_stale_jobs`)
- `q` wildcards % and _ acted as wildcards; now icontains(autoescape=True).
  (`test_q_wildcards_are_escaped`)

Hardening: dropped the dead 400 response doc, wrapped error details in
jsonable_encoder. Skipped (practically bounded by 3 sources): a LIMIT on also_on
siblings.

Second pass: 114 passed, 92% coverage, ruff clean, mypy strict clean. Swagger
UI verified in the browser (OAS 3.1, all four routes, error schemas).

## M6 - subscriptions, notifications, telegram webhook

Journey: as a user, I subscribe with filters on a channel (email, Discord,
Telegram), confirm via opt-in, manage or unsubscribe with a secret token, and
receive one digest of new matching jobs per run; a channel failure does not lose
my place.

Added: Subscription model + migration (uuidv7() id, jsonb filters, sha256 token
hashes; read before apply, no DROP). notify.py (job_matches, secure tokens,
Discord SSRF validation, email/Discord/Telegram senders, run_digest with
watermark). API routes POST /subscriptions (token once, opt-in, cap of 3
unverified per target), GET /subscriptions/verify, GET/PATCH/DELETE
/subscriptions/me (Bearer manage token), POST /subscriptions/unsubscribe, POST
/telegram/webhook (secret header, /start <token> binds chat_id and verifies).

RED: `No module named 'ptjobs.notify'` / `cannot import name 'get_verifier'`.
GREEN: 157 passed, 92.5% coverage, ruff clean, mypy strict clean.

Guarantees (selected):

| # | What is guaranteed | Test | Type | Result |
|---|--------------------|------|------|--------|
| 1 | Filter matching: keywords/city/work_mode/tag/min_salary, AND across dims | `test_notify.py::TestMatching` | unit | PASS |
| 2 | Tokens random+urlsafe; sha256 stable hex | `TestTokens` | unit | PASS |
| 3 | Discord webhook host/scheme/path validated (anti-SSRF) | `TestDiscordValidation` | unit | PASS |
| 4 | Discord/Telegram/email senders raise ChannelError on failure | `TestSenders`, `test_send_email_uses_smtp` | unit | PASS |
| 5 | Digest delivers new matching jobs, advances watermark | `test_digest.py` | integration | PASS |
| 6 | Channel failure keeps watermark for retry | `test_channel_failure_keeps_watermark` | integration | PASS |
| 7 | Create returns token once; stores only the hash; opt-in sent | `test_create_discord_returns_token_once` | integration | PASS |
| 8 | Invalid Discord webhook / email rejected (422) | `test_create_rejects_*` | integration | PASS |
| 9 | Verify marks verified; bad token 404 | `test_verify_*` | integration | PASS |
| 10 | /me requires valid Bearer manage token (401) | `test_me_requires_valid_token` | integration | PASS |
| 11 | Cap of 3 unverified per target (429) | `test_cap_unverified_per_target` | integration | PASS |
| 12 | Telegram /start binds chat_id and verifies; wrong secret 401 | `test_telegram_webhook.py` | integration | PASS |

### M6 security review gate

Blocked with two HIGH and several MEDIUM issues on a public, message-sending
API. Each real issue became a failing test first, then was fixed:

- HIGH: PATCH /subscriptions/me could change `target` with no re-verification,
  turning the service into a mailer/spammer to a victim. Target is now immutable
  (only filters are updatable; change target by recreating).
  (`test_patch_cannot_change_target`)
- HIGH: a Telegram subscription could be verified via GET /verify directly,
  spamming any chat_id without the bot. Telegram target is forced NULL at create
  and GET /verify refuses Telegram subscriptions (only the webhook verifies).
  (`test_create_telegram_forces_null_target`, `test_verify_rejects_telegram`)
- MEDIUM: bot token and Discord webhook URLs leaked through error text and logs.
  Senders now raise generic ChannelError ("... HTTP <status>"), never the URL or
  httpx text. (`test_send_telegram_error_hides_bot_token`)
- MEDIUM: scraped job content could ping @everyone via Discord. Payload now sets
  allowed_mentions {"parse": []}. (`test_send_discord_posts_content`)
- MEDIUM: the unverified cap was permanent (locked out a victim's target) and
  case-bypassable. Now counts only pending rows within a 24h TTL, and email
  targets are lowercased. (`test_cap_ignores_expired_unverified`)
- MEDIUM: a CRLF/bad email target could abort the whole digest run. send_email
  now catches ValueError/SMTPException as ChannelError (and target is immutable
  post-verify anyway).
- LOW: unsubscribe took the manage token in the query string (logged); now taken
  in the POST body. (`test_unsubscribe_deletes`)
- LOW: webhook secret compared with `!=`; now hmac.compare_digest.

Confirmed safe by the reviewer: Discord SSRF (host re-validated, redirects off),
token hashing (256-bit random, unsalted sha256 adequate), no IDOR. Noted for
deployment: per-IP rate limiting on POST /subscriptions via Coolify Traefik, and
PUBLIC_BASE_URL must be https in production. Deferred: expiring old
pending rows from the DB (they already stop counting via the TTL), and a
GET-then-POST confirm page for verify.

Second pass: 160 passed, 92% coverage, ruff clean, mypy strict clean.

## M7 - CLI, Docker, CI/CD, canary, docs

Journey: as the operator, I run `python -m ptjobs run` on a schedule to scrape
all sources then deliver digests, ship the app as one Docker image, and have CI
gate every change (lint, types, tests, OpenAPI drift, a destructive-migration
guard, Docker build) with a weekly canary that opens an issue when a source
breaks.

Added: __main__ with scrape/notify/run/openapi; notify.build_deliver (channel
dispatch) and alert_admin; Dockerfile (python:3.15-slim, multi-stage uv,
playwright chromium, non-root, entrypoint runs `alembic upgrade head` then
uvicorn); workflows ci/cd/canary/codeql (actions pinned by SHA); dependabot,
CODEOWNERS, PR and issue templates; README (PT), CONTRIBUTING, SECURITY, MIT
LICENSE; live canary tests (marker `live`, skipped by default).

RED: `AttributeError` on cli.run_scrape / `ImportError` build_deliver.
GREEN: 168 passed, 3 live deselected, 92% coverage, ruff clean, mypy strict.

Guarantees:

| # | What is guaranteed | Test | Type | Result |
|---|--------------------|------|------|--------|
| 1 | build_deliver routes to discord/telegram/email | `test_notify.py::TestBuildDeliver` | unit | PASS |
| 2 | Delivery without a target raises ChannelError | `test_missing_target_raises` | unit | PASS |
| 3 | `run` scrapes then notifies, in order | `test_cli.py::test_run_dispatches_scrape_then_notify` | unit | PASS |
| 4 | `scrape --source` passes the source through | `test_scrape_passes_source` | unit | PASS |
| 5 | `openapi` prints the schema | `test_openapi_prints_schema` | unit | PASS |
| 6 | Unknown --source is rejected | `test_unknown_source_rejected` | unit | PASS |
| 7 | Live canary tests exist and skip by default | `tests/live/` (deselected) | live | SKIP |

CI gates: lint, typecheck, test (Postgres 18 service + chromium), openapi-drift
(diff docs/openapi.json), migration-guard (grep rejects DROP/TRUNCATE),
docker-build. CD publishes to GHCR and pings the Coolify webhook. Canary runs
`pytest -m live` weekly and opens a `scraper-broken` issue on failure.

Docker image verified locally: builds, runs as non-root (appuser), bundles
chromium, and serves the full OpenAPI (9 paths). Fixed a .dockerignore that
excluded README.md (needed by the hatchling build).

## Post-launch fixes (found in production)

Running the first deploy surfaced six issues. Each became a failing test first
(10 RED), then was fixed.

| # | Problem in production | Fix | Test |
|---|-----------------------|-----|------|
| 1 | 983 non-tech net-empregos jobs passed the tech filter: the `.net` alias matched `www.net-empregos.com` in every item's link | Strip HTML tags and URLs before tag extraction | `TestTagsIgnoreMarkup` |
| 2 | Euro sign lost ("180?/mes"): the feed declares iso-8859-1 but is windows-1252 | Decode feed bytes as cp1252 | `test_net_empregos_decodes_windows_1252_euro` |
| 3 | Digest had no size cap; Telegram (4096) and Discord (2000) reject longer messages, so the watermark never advanced | Per-channel `max_chars` with an "e mais N vaga(s)" link to `/jobs` | `TestDigestLimits`, `test_discord_digest_respects_channel_limit` |
| 4 | One transaction for all sources: a crash in one source discarded the others | Commit each source separately | `test_scrape_commits_each_source_separately` |
| 5 | The bot stayed silent after `/start`, so users could not tell it worked | Best-effort confirmation or "invalid link" reply; failures never break the webhook | `test_start_replies_with_confirmation`, `test_unknown_token_replies_invalid_link`, `test_reply_failure_still_verifies_and_returns_200` |
| 6 | Swagger's generated example sent `min_salary: 0`, silently excluding jobs without salary | `min_salary: 0` means no filter; explicit request example without it | `test_min_salary_zero_means_no_filter`, `test_subscription_example_has_no_min_salary` |

GREEN: 183 passed, 93% coverage, ruff clean, mypy strict clean.

## Security review and pen test follow-ups

- **Correction to the M6 record above:** `test_send_telegram_error_hides_bot_token`
  was listed as passing but was never actually added (a text replace silently
  matched nothing after reformatting). It now exists, and it exposed a real leak:
  `ChannelError` was raised `from` the httpx error, whose message contains the
  full URL including the bot token, and `logger.exception` printed that chain.
  Senders now raise `from None`, and the `httpx` logger is pinned to WARNING so
  request URLs are never logged. (`test_send_telegram_error_hides_bot_token`,
  `test_failed_delivery_traceback_never_contains_bot_token`,
  `test_discord_error_hides_webhook_url`, `test_httpx_request_urls_are_not_logged_at_info`)
- Baseline security headers (HSTS, nosniff, DENY framing, no-referrer) on every
  response; uvicorn no longer sends a `Server` header. (`test_security_headers_present`)
- `certifi` bumped past PYSEC-2026-4320 (found by pip-audit).
- CI installs only the Chromium headless shell, without apt packages, following
  the Playwright docs; the Docker image drops the full browser (2.47 GB to 1.86 GB).
