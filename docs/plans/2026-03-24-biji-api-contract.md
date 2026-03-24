# Biji API Contract (Task 1 Snapshot)

## Scope

- Captured at: 2026-03-24 (Asia/Shanghai)
- Runtime base URL: `https://notes-api.biji.com`
- Auth mode: `Authorization: Bearer <token>`
- Request headers used:
  - `Accept: application/json, text/plain, */*`
  - `Origin: https://www.biji.com`
  - `Referer: https://www.biji.com/note`
  - `User-Agent: Chrome-like UA`

## 1) Note list API

- Method: `GET`
- URL: `/voicenotes/web/notes`
- Example request:
  - `GET https://notes-api.biji.com/voicenotes/web/notes?page=1&page_size=5`

### Response shape

Top-level:

- `h`: metadata object
- `c`: payload object

`c` fields:

- `list` (array): note summary list
- `has_more` (bool)
- `total_items` (int)

### Actual list item key fields (from fixture)

- `id` (string)
- `note_id` (string)
- `title` (string)
- `content` (string, rich content / markdown-like text)
- `body_text` (string, HTML-like summary text)
- `created_at` (string, format `YYYY-MM-DD HH:MM:SS`)
- `updated_at` (string, format `YYYY-MM-DD HH:MM:SS`)
- `status` (int)
- `attachments` (array)
- `small_images` (array)
- `original_images` (array)
- `tags` (array)
- `topics` (array)
- `note_type` (string)

### Observed list behavior

- Requested `page_size=5`
- Response `c.list` length was `10`
- Re-running the request with `page=2&page_size=5` returned the same first-page window
- Within the returned window, items were ordered newest-to-oldest and both `created_at` and `updated_at` were monotonically descending
- Conclusion:
  - Current observation supports “list is returned in descending time order”
  - The API currently appears to ignore or normalize `page` / `page_size` for this account or this endpoint
  - Treat the descending order as an observed behavior, not a strict contract, until pagination semantics are validated with more samples

## 2) Note detail API

- Method: `GET`
- URL: `/voicenotes/web/notes/{note_id}`
- Example request:
  - `GET https://notes-api.biji.com/voicenotes/web/notes/1905199764681666160`

### Response shape

Top-level:

- `h`: metadata object
- `c`: note detail object

`c` key fields:

- `id` (string)
- `note_id` (string)
- `title` (string)
- `content` (string)
- `body_text` (string)
- `created_at` (string, format `YYYY-MM-DD HH:MM:SS`)
- `updated_at` (string, format `YYYY-MM-DD HH:MM:SS`)
- `status` (int)
- `attachments` (array)
- `small_images` (array)
- `original_images` (array)
- `tags` (array)
- `topics` (array)
- `note_type` (string)

## 3) Visibility / deletion status hint

- Current captured list/detail sample has `status = 0`
- This value appears in both list item and detail payload
- For implementation phase, treat `status` as the primary visibility status field candidate
- Final status enum mapping should be validated with additional samples (for example deleted / recycled notes)

## 4) Fixture policy for committed test data

- `tests/fixtures/biji/list_page_1.json`
- `tests/fixtures/biji/note_detail_sample.json`

These committed fixtures are sanitized stable snapshots, not byte-for-byte raw responses.

Sanitization rules used for Task 1:

- Removed unstable metadata such as request timing / tracing fields (`h.s`, `h.t`, `h.apm`)
- Removed fields not needed for parser tests (`share_id`, `share_scope`, `res_info`, and similar share / telemetry fields)
- Replaced signed or expiring asset URLs with stable placeholders under `example.invalid`
- Kept the structural fields that later client / parser / sync code needs:
  - top-level `h` / `c`
  - list `c.list`
  - detail `c`
  - note identifiers, content fields, timestamps, status, attachments, tags, topics
- Trimmed long list-item `content` / `body_text` values to representative excerpts to keep fixtures reviewable
- Kept detail `content` / `body_text` intact so later markdown / content parsing tests can use a realistic full-note sample
