# Vertical accelerator: Media / content / publisher

Use for content/engagement businesses (ads + subscriptions/newsletter).

**Primary conversions:** `subscribe` (newsletter/paid) and engagement depth; ad
revenue depends on quality sessions, not a single click.
**Core events:** `content_view → scroll_depth → video_play → subscribe`, plus
`share` / `comment` where relevant.

**Proof params:**
- `content_view`: `content_id`, `content_type`, `author`, `category`.
- `scroll_depth`: `percent_scrolled` (25/50/75/100), `content_id`.
- `subscribe`: `subscription_type`.

**KPIs:** engaged sessions, read-through/scroll depth, video completion, newsletter
signups, subscriber conversion, return frequency.

**The big watch-out:** vanity pageviews ≠ engagement. Pair `content_view` with
`scroll_depth`/time so the SDR measures *real* consumption. Consent matters a lot
(ad/analytics storage) — model consent gating explicitly.

**Cross-platform:** GA4 primary; `subscribe→Subscribe` (Meta) for acquisition campaigns.
