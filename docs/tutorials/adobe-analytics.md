# Adobe Analytics setup

Create an Adobe Developer Console project, add the Adobe Analytics API using OAuth Server-to-Server credentials, grant the service account access to your report suites, and save the credentials in Fluxito.

**Time:** ~25 minutes
**You'll need:** an Adobe ID with **System Administrator** access in the Adobe Admin Console. If you're not a System Admin, coordinate with someone who is — they're needed for Step 4.

---

## 1. Open Adobe Developer Console

Go to [https://developer.adobe.com/console/](https://developer.adobe.com/console/) and sign in with your Adobe ID.

---

## 2. Create a project

1. Click **Create new project**.
2. Adobe creates a project with an auto-generated name. Click the pencil icon to rename it (e.g. `Fluxito`). Click **Save**.

---

## 3. Add the Adobe Analytics API

1. On the project dashboard, click **Add API**.
2. Under **Experience Cloud**, find and click **Adobe Analytics**.
3. Click **Next**.
4. When asked for a credential type, select **OAuth Server-to-Server**.
5. Click **Save configured API**.

---

## 4. Collect your credentials

After saving, click on the **OAuth Server-to-Server** credential in your project. You'll find:

| Field | Where to find it |
|---|---|
| **Client ID** | Shown directly on the credential page |
| **Client Secret** | Click **Retrieve client secret** — copy it immediately |
| **Organization ID** | Shown at the top of the credential page as "IMS Organization ID", ending in `@AdobeOrg` |

Copy all three values. Include the full Organization ID string including the `@AdobeOrg` suffix.

---

## 5. Grant the service account access to Analytics

The service account needs to be added to an Adobe Analytics product profile that covers the report suites you want Fluxito to query.

1. Go to [https://adminconsole.adobe.com/](https://adminconsole.adobe.com/) and sign in as a System Administrator.
2. Click **Products → Adobe Analytics**.
3. Click a product profile that includes the report suites you need. (Check the **Permissions** tab to see which report suites are included.)
4. Click the **Developers** tab (not Users — Developers is for service accounts).
5. Click **Add developer** (or **+**).
6. In the field, enter the **Technical Account email** from the Developer Console credential page (ends in `@techacct.adobe.com`). Click **Save**.

If report suites span multiple product profiles, add the Technical Account email to each relevant profile.

---

## 6. Save in Fluxito

1. Go to `/connect` in Fluxito.
2. Click **Connect Adobe Analytics**.
3. Fill in:
   - **Client ID** — from Step 4
   - **Client Secret** — from Step 4
   - **Organization ID** — from Step 4 (include the `@AdobeOrg` suffix)
4. Click **Save**.

---

## 7. Verify the connection

Ask Claude to list report suites. If the service account is configured correctly and has access to at least one product profile, you'll see a list of report suites.

---

## Workspace projects (Analysis Workspace)

Once the connection works, Fluxito can list, fetch, create, edit, copy, and delete Analysis Workspace projects through the Adobe Analytics 2.0 Projects API.

| Tool | Action | What it does |
|---|---|---|
| `analytics_read` | `adobe_workspace_list_projects` | Compact project list. Official Adobe query params only: `expansion` (default `reportSuiteName,ownerFullName`), `include_type`, `limit`, `page`, `locale`. Does **not** include the full definition unless you ask for `expansion=["definition"]`. |
| `analytics_read` | `adobe_workspace_get_project` | One project by `project_id` (`[A-Za-z0-9_-]{1,128}`). Always fetches `expansion=definition` so the full Workspace JSON is available for editing. |
| `analytics_write` | `adobe_workspace_create_project` | `config={name, rsid, definition}`. Optional `description`, `tags`, `shares`. Server-managed fields (`owner`, `id`, `created`, `modified`) are not sent. |
| `analytics_write` | `adobe_workspace_update_project` | `config={project_id, ...partial fields}`. Adobe PUT supports partial updates, so Fluxito sends **only the fields you supply** — a rename is `PUT {"name": "..."}` with no prior GET. Set `merge_definition=true` to opt into GET+merge of the `definition` subtree only. |
| `analytics_write` | `adobe_workspace_delete_project` | Destructive. Requires an explicit `config.project_id` matching `[A-Za-z0-9_-]{1,128}` — no wildcard, path, or bulk delete. |
| `analytics_write` | `adobe_workspace_copy_project` | GET the source (with definition) and POST a new project under `config.name` using writable fields only. |

The former generic action names (`list_projects`, `get_project`, `create_project`, `update_project`, `delete_project`, and `copy_project`) remain accepted as deprecated compatibility aliases, but they are no longer advertised to MCP clients.

Examples:

```
analytics_read(action="adobe_workspace_list_projects", params={
  "platform": "adobe_analytics",
  "limit": 20,
  "include_type": "all"
})

analytics_read(action="adobe_workspace_get_project", params={
  "platform": "adobe_analytics",
  "project_id": "6091a10005c7706c0acdd751"
})

analytics_write(action="adobe_workspace_update_project", params={
  "platform": "adobe_analytics",
  "config": {"project_id": "6091a10005c7706c0acdd751", "name": "Renamed project"}
})
```

---

## Troubleshooting

| Error | Fix |
|---|---|
| `401 Unauthorized` / `invalid_token` | One of the three credential fields is wrong. Verify the Client ID, Client Secret, and Organization ID against the Developer Console credential page. |
| `403 Forbidden` / `insufficient_access` | The service account (Technical Account email) hasn't been added to an Analytics product profile. Complete Step 5. |
| Report suites list is empty | The product profile the service account belongs to has no report suites assigned. Check the profile's Permissions tab in the Admin Console. |
| `Organization ID format issue` | The `@AdobeOrg` suffix was omitted. Include the full string, e.g. `ABCDE12345@AdobeOrg`. |
