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
- `content` (string, rich content)
- `body_text` (string)
- `created_at` (int)
- `updated_at` (int)
- `status` (int)
- `attachments` (array)
- `small_images` (array)
- `original_images` (array)
- `tags` (array)
- `topics` (array)
- `note_type` (int)

### Observed pagination behavior

- Requested `page_size=5`
- Response `c.list` length was `10`
- Conclusion: server may enforce its own page size or normalize client value

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
- `created_at` (int)
- `updated_at` (int)
- `status` (int)
- `attachments` (array)
- `small_images` (array)
- `original_images` (array)
- `tags` (array)
- `topics` (array)

## 3) Visibility/deletion status hint

- Current captured list/detail sample has `status = 0`
- This value appears in both list item and detail payload
- For implementation phase, treat `status` as the primary visibility status field candidate
- Final status enum mapping should be validated with additional samples (e.g., deleted/recycled notes)

## 4) Fixtures produced by Task 1

- `tests/fixtures/biji/list_page_1.json`
- `tests/fixtures/biji/note_detail_sample.json`

Both are raw server responses (no redaction, no field rewrite).
