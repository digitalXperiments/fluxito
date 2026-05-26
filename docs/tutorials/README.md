# Platform Tutorials

Step-by-step setup guides for every connector supported by Fluxito. Written for marketing analysts and growth leads who have admin access to their company's marketing platforms and want to get credentials into Fluxito quickly.

**Recommended reading order for new self-hosters:**
1. Start with [Google Cloud setup](google-cloud-setup.md) if you plan to use any Google connector — all five share one OAuth client.
2. Proceed to platform-specific tutorials in the order relevant to your stack.
3. Credential connectors (Snowflake, Redshift, Amplitude, Adobe) can be set up in any order independently.

---

## Google platforms — shared OAuth app

All five Google-backed connectors share a single OAuth 2.0 client created in Google Cloud. Complete the foundational guide first; the platform-specific tutorials only cover what differs.

| Tutorial | What it covers | Time |
|---|---|---|
| [google-cloud-setup.md](google-cloud-setup.md) | **Start here.** Create a GCP project, enable 6 APIs, configure the OAuth consent screen, and create the OAuth client ID. | ~20 min |
| [google-analytics-4.md](google-analytics-4.md) | Confirm GA4 APIs are enabled; check property access (Viewer/Editor roles); find your property ID. | ~10 min |
| [google-tag-manager.md](google-tag-manager.md) | Confirm Tag Manager API is enabled; check GTM container roles (Read/Edit/Publish). | ~10 min |
| [google-ads.md](google-ads.md) | Apply for a Google Ads developer token (required separately); Basic vs Standard access in one line. | ~15 min + up to 1–3 day approval |
| [search-console.md](search-console.md) | Confirm Search Console API is enabled; check Full User / Owner access; property identifier formats. | ~10 min |
| [bigquery.md](bigquery.md) | Two connection modes: service account JSON (recommended) and Google OAuth. Service account IAM setup. | ~15 min |

---

## Social and paid marketing — OAuth apps

Each platform requires its own OAuth app. App Review timelines are noted for platforms that require production review.

| Tutorial | What it covers | Time |
|---|---|---|
| [meta-ads.md](meta-ads.md) | Business App creation, Marketing API product, Facebook Login redirect URI, App Review note. | ~20 min setup; 3–10 day review |
| [tiktok-ads.md](tiktok-ads.md) | Business API app creation, permissions, redirect URI, production review note. | ~20 min setup; 1–2 week review |
| [linkedin-ads.md](linkedin-ads.md) | App creation, company page verification, Marketing Developer Platform request, redirect URI. | ~20 min setup; 1–5 day review |
| [pinterest-ads.md](pinterest-ads.md) | Developer portal app registration, scope configuration, redirect URI, production review note. | ~15 min setup; 1–5 day review |
| [snap-ads.md](snap-ads.md) | App creation, Ads API capability, confidential client setup, redirect URI. | ~20 min setup; variable review |

---

## Data warehouses — credential connectors

No OAuth app required. Each user connects with their own database credentials via `/connect`.

| Tutorial | What it covers | Time |
|---|---|---|
| [snowflake.md](snowflake.md) | Account identifier format, dedicated user/role SQL, warehouse and database grants, future table grants. | ~15 min |
| [redshift.md](redshift.md) | Cluster endpoint discovery, dedicated user creation, VPC security group inbound rule. | ~20 min |

---

## Analytics and tag management — credential connectors

No OAuth app required.

| Tutorial | What it covers | Time |
|---|---|---|
| [amplitude.md](amplitude.md) | API Key and Secret Key per project; multi-project setup. | ~5 min |
| [adobe-analytics.md](adobe-analytics.md) | Adobe Developer Console project, OAuth Server-to-Server credential, Admin Console product profile grant. | ~25 min |
| [adobe-launch.md](adobe-launch.md) | Reuse or create Adobe I/O project for Experience Platform Tags; product profile rights. | ~15 min (~5 min if reusing Analytics project) |

---

## Cross-references

- **Google connectors share one OAuth client:** once you complete [google-cloud-setup.md](google-cloud-setup.md) and connect a Google account, that single connection covers GA4, GTM, Google Ads, Search Console, and BigQuery (OAuth mode).
- **Adobe Analytics and Adobe Launch share one Adobe I/O project:** complete [adobe-analytics.md](adobe-analytics.md) first, then [adobe-launch.md](adobe-launch.md) reuses the same three credentials with one additional Admin Console step.
- **BigQuery service account vs Google OAuth:** BigQuery is the only Google connector with an alternative credential mode. See [bigquery.md](bigquery.md) for both options.
