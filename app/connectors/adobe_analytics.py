"""
Adobe Analytics Connector

Uses the Adobe Analytics 2.0 API via Adobe IMS OAuth (client credentials grant).

Auth: Adobe IMS OAuth — client_id + client_secret + org_id.
Obtains JWT access token via client credentials grant:
POST https://ims-na1.adobelogin.com/ims/token/v3

All methods accept connection params and obtain/cache tokens internally.

Layer 1 (Read): list_report_suites, get_dimensions, get_metrics, run_report, get_segments, get_calculated_metrics

Layer 2 (Audit): audit_report_suite, check_data_quality

Layer 3 (Write): create_segment, update_segment, delete_segment, create_calculated_metric, delete_calculated_metric

Layer 4 (Workspace projects): list_projects, get_project, create_project, update_project, delete_project, copy_project
"""

import json
import logging
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from app.connectors.errors import friendly_errors

logger = logging.getLogger(__name__)

_IMS_BASE = "https://ims-na1.adobelogin.com"
_ANALYTICS_BASE = "https://analytics.adobe.io"


class AdobeAnalyticsConnector:
    """Interfaces with Adobe Analytics 2.0 API using client credentials grant."""

    def __init__(self):
        # In-memory token cache: {org_id: {token, expiry}}
        self._token_cache: dict[str, dict[str, Any]] = {}

    async def _get_adobe_token(self, client_id: str, client_secret: str, org_id: str) -> dict:
        """
        Get or refresh Adobe IMS access token.
        Caches tokens with expiry; refreshes if expired.
        """
        cache_key = org_id
        cached = self._token_cache.get(cache_key, {})

        # Check if cached token is still valid (with 60s buffer)
        if cached.get("token") and cached.get("expiry", 0) > time.time() + 60:
            return {"token": cached["token"]}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{_IMS_BASE}/ims/token/v3",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "scope": "openid,AdobeID,read_organizations,additional_info.projectedProductContext",
                    },
                )

                if response.status_code >= 400:
                    return {
                        "error": True,
                        "status_code": response.status_code,
                        "message": response.text,
                    }

                data = response.json()
                token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)

                # Cache the token
                self._token_cache[cache_key] = {
                    "token": token,
                    "expiry": time.time() + expires_in,
                }

                return {"token": token}

        except Exception as e:
            logger.error(f"Adobe token request error: {e}")
            return {"error": True, "message": str(e)}

    async def _request(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict:
        """
        Make an authenticated request to Adobe Analytics API.
        Automatically obtains and injects bearer token.
        """
        token_result = await self._get_adobe_token(client_id, client_secret, org_id)
        if token_result.get("error"):
            return token_result

        token = token_result.get("token")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-api-key": client_id,
            "x-proxy-global-company-id": org_id,
        }

        try:
            url = f"{_ANALYTICS_BASE}{endpoint}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers, params=params)
                elif method == "POST":
                    response = await client.post(url, headers=headers, params=params, json=json_body)
                elif method == "PUT":
                    response = await client.put(url, headers=headers, params=params, json=json_body)
                elif method == "DELETE":
                    response = await client.delete(url, headers=headers, params=params)
                else:
                    return {"error": True, "message": f"Unsupported HTTP method: {method}"}

                if response.status_code >= 400:
                    return {
                        "error": True,
                        "status_code": response.status_code,
                        "message": response.text,
                    }

                try:
                    return response.json()
                except Exception:
                    return {"success": response.status_code < 300, "body": response.text}

        except Exception as e:
            logger.error(f"Adobe Analytics API request error: {e}")
            return {"error": True, "message": str(e)}

    # ------------------------------------------------------------------
    # Layer 1: Data Access
    # ------------------------------------------------------------------

    @friendly_errors("Adobe Analytics")
    async def list_report_suites(self, client_id: str, client_secret: str, org_id: str) -> dict:
        """
        List all report suites accessible to the org.
        GET /api/{company_id}/reportsuites
        """
        result = await self._request(client_id, client_secret, org_id, "GET", f"/api/{org_id}/reportsuites")
        if result.get("error"):
            return result

        suites = result.get("suites", [])
        return {
            "report_suites": [
                {
                    "rsid": s.get("rsid"),
                    "name": s.get("name"),
                    "parent_rsid": s.get("parentRsid"),
                    "time_zone": s.get("timezoneId"),
                    "created": s.get("created"),
                }
                for s in suites
            ],
            "total": len(suites),
        }

    @friendly_errors("Adobe Analytics")
    async def get_dimensions(self, client_id: str, client_secret: str, org_id: str, rsid: str) -> dict:
        """
        List all dimensions for a report suite.
        GET /api/{company_id}/dimensions
        """
        params = {"rsid": rsid}
        result = await self._request(
            client_id, client_secret, org_id, "GET", f"/api/{org_id}/dimensions", params=params
        )
        if result.get("error"):
            return result

        dimensions = result.get("dimensions", [])
        return {
            "rsid": rsid,
            "dimensions": [
                {
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "type": d.get("type"),
                    "category": d.get("category"),
                }
                for d in dimensions
            ],
            "total": len(dimensions),
        }

    @friendly_errors("Adobe Analytics")
    async def get_metrics(self, client_id: str, client_secret: str, org_id: str, rsid: str) -> dict:
        """
        List all metrics for a report suite.
        GET /api/{company_id}/metrics
        """
        params = {"rsid": rsid}
        result = await self._request(
            client_id, client_secret, org_id, "GET", f"/api/{org_id}/metrics", params=params
        )
        if result.get("error"):
            return result

        metrics = result.get("metrics", [])
        return {
            "rsid": rsid,
            "metrics": [
                {
                    "id": m.get("id"),
                    "name": m.get("name"),
                    "type": m.get("type"),
                    "category": m.get("category"),
                }
                for m in metrics
            ],
            "total": len(metrics),
        }

    @friendly_errors("Adobe Analytics")
    async def run_report(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        rsid: str,
        dimensions: list[str],
        metrics: list[str],
        date_range: dict[str, str],
        limit: int = 100,
    ) -> dict:
        """
        Run a standard report.
        POST /api/{company_id}/reports
        """
        json_body = {
            "rsid": rsid,
            "globalFilters": [],
            "metricContainer": {
                "metrics": [{"columnId": m} for m in metrics],
            },
            "dimension": dimensions[0] if dimensions else "variables/page",
            "settings": {
                "limit": limit,
                "page": 0,
                "nonesBehavior": "exclude-nones",
            },
            "dateRange": date_range,
        }

        result = await self._request(
            client_id, client_secret, org_id, "POST", f"/api/{org_id}/reports", json_body=json_body
        )
        if result.get("error"):
            return result

        return {
            "rsid": rsid,
            "dimensions": dimensions,
            "metrics": metrics,
            "rows": result.get("rows", []),
            "summary": result.get("summaryData", {}),
            "row_count": len(result.get("rows", [])),
        }

    @friendly_errors("Adobe Analytics")
    async def get_segments(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        rsid: str | None = None,
    ) -> dict:
        """
        List all segments.
        GET /api/{company_id}/segments
        """
        params = {}
        if rsid:
            params["rsid"] = rsid

        result = await self._request(
            client_id, client_secret, org_id, "GET", f"/api/{org_id}/segments", params=params
        )
        if result.get("error"):
            return result

        segments = result.get("segments", [])
        return {
            "rsid": rsid,
            "segments": [
                {
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "description": s.get("description"),
                    "owner": s.get("owner"),
                    "created": s.get("created"),
                }
                for s in segments
            ],
            "total": len(segments),
        }

    @friendly_errors("Adobe Analytics")
    async def get_calculated_metrics(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        rsid: str | None = None,
    ) -> dict:
        """
        List all calculated metrics.
        GET /api/{company_id}/calculatedmetrics
        """
        params = {}
        if rsid:
            params["rsid"] = rsid

        result = await self._request(
            client_id,
            client_secret,
            org_id,
            "GET",
            f"/api/{org_id}/calculatedmetrics",
            params=params,
        )
        if result.get("error"):
            return result

        metrics = result.get("calculatedMetrics", [])
        return {
            "rsid": rsid,
            "calculated_metrics": [
                {
                    "id": m.get("id"),
                    "name": m.get("name"),
                    "description": m.get("description"),
                    "owner": m.get("owner"),
                }
                for m in metrics
            ],
            "total": len(metrics),
        }

    # ------------------------------------------------------------------
    # Layer 2: Audit
    # ------------------------------------------------------------------

    @friendly_errors("Adobe Analytics")
    async def audit_report_suite(self, client_id: str, client_secret: str, org_id: str, rsid: str) -> dict:
        """
        Audit a report suite: check enabled dimensions, custom events usage, eVar configuration.
        """
        dims_result = await self.get_dimensions(client_id, client_secret, org_id, rsid)
        if dims_result.get("error"):
            return dims_result

        metrics_result = await self.get_metrics(client_id, client_secret, org_id, rsid)
        if metrics_result.get("error"):
            return metrics_result

        dims = dims_result.get("dimensions", [])
        metrics_list = metrics_result.get("metrics", [])

        # Count custom evars (typically id:evar* format)
        custom_evars = [d for d in dims if "evar" in d.get("id", "").lower()]
        custom_metrics = [m for m in metrics_list if "event" in m.get("id", "").lower()]

        return {
            "rsid": rsid,
            "total_dimensions": len(dims),
            "total_metrics": len(metrics_list),
            "custom_evars": len(custom_evars),
            "custom_events": len(custom_metrics),
            "health_score": min(100, (len(dims) + len(metrics_list)) * 5),  # Simple scoring
        }

    @friendly_errors("Adobe Analytics")
    async def check_data_quality(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        rsid: str,
        days_back: int = 30,
    ) -> dict:
        """
        Run a basic traffic report to check data quality over recent period.
        """
        date_range = {
            "dateRange": f"Last {days_back} days",
        }

        result = await self.run_report(
            client_id,
            client_secret,
            org_id,
            rsid,
            dimensions=["variables/page"],
            metrics=["metrics/visits"],
            date_range=date_range,
            limit=10,
        )

        if result.get("error"):
            return result

        return {
            "rsid": rsid,
            "days_checked": days_back,
            "rows_returned": result.get("row_count", 0),
            "has_data": result.get("row_count", 0) > 0,
            "status": "healthy" if result.get("row_count", 0) > 0 else "no_data",
        }

    # ------------------------------------------------------------------
    # Layer 3: Write Operations
    # ------------------------------------------------------------------

    @friendly_errors("Adobe Analytics")
    async def create_segment(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        name: str,
        rsid: str,
        definition: dict[str, Any],
        description: str | None = None,
    ) -> dict:
        """
        Create a new segment.
        POST /api/{company_id}/segments
        """
        json_body = {
            "name": name,
            "rsid": rsid,
            "definition": definition,
        }
        if description:
            json_body["description"] = description

        result = await self._request(
            client_id, client_secret, org_id, "POST", f"/api/{org_id}/segments", json_body=json_body
        )
        if result.get("error"):
            return result

        return {
            "success": True,
            "segment_id": result.get("id"),
            "name": name,
            "message": "Segment created successfully",
        }

    @friendly_errors("Adobe Analytics")
    async def update_segment(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        segment_id: str,
        name: str | None = None,
        description: str | None = None,
        definition: dict[str, Any] | None = None,
    ) -> dict:
        """
        Update an existing segment.
        PUT /api/{company_id}/segments/{segment_id}
        """
        json_body = {}
        if name:
            json_body["name"] = name
        if description:
            json_body["description"] = description
        if definition:
            json_body["definition"] = definition

        result = await self._request(
            client_id,
            client_secret,
            org_id,
            "PUT",
            f"/api/{org_id}/segments/{segment_id}",
            json_body=json_body,
        )
        if result.get("error"):
            return result

        return {
            "success": True,
            "segment_id": segment_id,
            "message": "Segment updated successfully",
        }

    @friendly_errors("Adobe Analytics")
    async def delete_segment(self, client_id: str, client_secret: str, org_id: str, segment_id: str) -> dict:
        """
        Delete a segment.
        DELETE /api/{company_id}/segments/{segment_id}
        """
        result = await self._request(
            client_id,
            client_secret,
            org_id,
            "DELETE",
            f"/api/{org_id}/segments/{segment_id}",
        )
        if result.get("error"):
            return result

        return {
            "success": True,
            "segment_id": segment_id,
            "message": "Segment deleted successfully",
        }

    @friendly_errors("Adobe Analytics")
    async def create_calculated_metric(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        name: str,
        rsid: str,
        definition: dict[str, Any],
        description: str | None = None,
    ) -> dict:
        """
        Create a new calculated metric.
        POST /api/{company_id}/calculatedmetrics
        """
        json_body = {
            "name": name,
            "rsid": rsid,
            "definition": definition,
        }
        if description:
            json_body["description"] = description

        result = await self._request(
            client_id,
            client_secret,
            org_id,
            "POST",
            f"/api/{org_id}/calculatedmetrics",
            json_body=json_body,
        )
        if result.get("error"):
            return result

        return {
            "success": True,
            "metric_id": result.get("id"),
            "name": name,
            "message": "Calculated metric created successfully",
        }

    @friendly_errors("Adobe Analytics")
    async def delete_calculated_metric(
        self, client_id: str, client_secret: str, org_id: str, metric_id: str
    ) -> dict:
        """
        Delete a calculated metric.
        DELETE /api/{company_id}/calculatedmetrics/{metric_id}
        """
        result = await self._request(
            client_id,
            client_secret,
            org_id,
            "DELETE",
            f"/api/{org_id}/calculatedmetrics/{metric_id}",
        )
        if result.get("error"):
            return result

        return {
            "success": True,
            "metric_id": metric_id,
            "message": "Calculated metric deleted successfully",
        }

    # ------------------------------------------------------------------
    # Layer 4: Workspace projects (Analysis Workspace reports)
    # GET/POST/PUT/DELETE /api/{company_id}/projects[/{id}]
    # ------------------------------------------------------------------

    @friendly_errors("Adobe Analytics")
    async def list_projects(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        expansion: list[str] | str | None = None,
        include_type: str | None = None,
        limit: int | None = None,
        page: int | None = None,
        locale: str | None = None,
    ) -> dict:
        """
        List Analysis Workspace projects.

        GET /api/{company_id}/projects

        Query strings are limited to Adobe's published project parameters:
        expansion, includeType, locale, limit, page
        (https://developer.adobe.com/analytics-apis/docs/2.0/guides/endpoints/projects/parameters).

        Default expansion is reportSuiteName,ownerFullName (compact names, no
        definition). Pass expansion including ``definition`` only when the full
        Workspace JSON is needed.
        """
        params = _project_list_query_params(
            expansion=expansion,
            include_type=include_type,
            limit=limit,
            page=page,
            locale=locale,
        )
        result = await self._request(
            client_id,
            client_secret,
            org_id,
            "GET",
            f"/api/{org_id}/projects",
            params=params or None,
        )
        if result.get("error"):
            return _map_adobe_http_error(result, resource="Workspace project")

        raw_items = _project_list_items(result)
        include_definition = _csv_contains(params.get("expansion"), "definition")
        return {
            "projects": [_summarize_project(p, include_definition=include_definition) for p in raw_items],
            "total": result.get("totalElements", len(raw_items)),
            "page": result.get("number", page if page is not None else 0),
            "total_pages": result.get("totalPages"),
            "limit": result.get("size", limit),
            "first_page": result.get("firstPage"),
            "last_page": result.get("lastPage"),
        }

    @friendly_errors("Adobe Analytics")
    async def get_project(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        project_id: str,
        expansion: list[str] | str | None = None,
    ) -> dict:
        """
        Fetch one Workspace project by id, including its full definition.

        GET /api/{company_id}/projects/{id}?expansion=definition

        ``expansion`` always includes ``definition`` so the result is suitable
        for a subsequent edit. Returns the full project object (not a compact
        summary).
        """
        err = validate_project_id(project_id)
        if err:
            return err

        expansion_list = _as_str_list(expansion)
        if "definition" not in expansion_list:
            expansion_list.append("definition")

        result = await self._request(
            client_id,
            client_secret,
            org_id,
            "GET",
            _project_resource_path(org_id, project_id),
            params={"expansion": ",".join(expansion_list)},
        )
        if result.get("error"):
            return _map_adobe_http_error(result, resource="Workspace project")

        project = _unwrap_project(result)
        if not isinstance(project, dict) or not project.get("id"):
            return {
                "error": True,
                "error_type": "upstream_error",
                "message": "Adobe Analytics returned an unexpected project payload.",
            }
        return project

    @friendly_errors("Adobe Analytics")
    async def create_project(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        name: str,
        rsid: str,
        definition: dict[str, Any],
        description: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict:
        """
        Create a Workspace project.

        POST /api/{company_id}/projects

        Only documented writable fields are sent: name, rsid, definition,
        type=project, optional description, and optional extra keys from
        ``_PROJECT_CREATE_OPTIONAL_FIELDS`` (tags, shares). Server-managed
        fields such as owner, id, created, modified, and expansion-only
        names are never forwarded.
        """
        err = _require_nonempty("name", name)
        if err:
            return err
        err = _require_nonempty("rsid", rsid)
        if err:
            return err
        if not isinstance(definition, dict):
            return {
                "error": True,
                "error_type": "invalid_param",
                "message": "definition must be a JSON object (Workspace project definition).",
            }

        json_body: dict[str, Any] = {
            "name": name,
            "rsid": rsid,
            "definition": definition,
            "type": "project",
        }
        if description is not None:
            json_body["description"] = description
        if extra:
            for key, value in extra.items():
                if key in _PROJECT_CREATE_OPTIONAL_FIELDS:
                    json_body[key] = value

        result = await self._request(
            client_id,
            client_secret,
            org_id,
            "POST",
            f"/api/{org_id}/projects",
            json_body=json_body,
        )
        if result.get("error"):
            return _map_adobe_http_error(result, resource="Workspace project")

        created = _unwrap_project(result)
        return {
            "success": True,
            "project_id": created.get("id"),
            "name": created.get("name", name),
            "rsid": created.get("rsid", rsid),
            "message": "Workspace project created successfully",
        }

    @friendly_errors("Adobe Analytics")
    async def update_project(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        project_id: str,
        name: str | None = None,
        description: str | None = None,
        rsid: str | None = None,
        definition: dict[str, Any] | None = None,
        owner: dict[str, Any] | None = None,
        updates: dict[str, Any] | None = None,
        merge_definition: bool = False,
    ) -> dict:
        """
        Update a Workspace project with a minimal partial PUT.

        Adobe's PUT /api/{company_id}/projects/{id} supports partial updates —
        a body of just ``{"name": "..."}`` is the documented example
        (https://developer.adobe.com/analytics-apis/docs/2.0/guides/endpoints/projects/#update-a-project).

        This method sends **only** caller-supplied writable fields. It does
        not GET first and never resends the stored project representation
        (id, owner, type, created, modified, expansion-only names, etc.).

        ``merge_definition=True`` is the one opt-in exception: if the caller
        supplies a partial ``definition``, GET the current definition, deep-merge
        that subtree only, and PUT ``{"definition": <merged>, ...other caller
        fields}``. The GET representation is never reused as the PUT body.
        """
        err = validate_project_id(project_id)
        if err:
            return err
        if name is not None:
            err = _require_nonempty("name", name)
            if err:
                return err
        if definition is not None and not isinstance(definition, dict):
            return {
                "error": True,
                "error_type": "invalid_param",
                "message": "definition must be a JSON object when provided.",
            }

        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if rsid is not None:
            body["rsid"] = rsid
        if owner is not None:
            body["owner"] = owner
        if definition is not None:
            body["definition"] = definition
        if updates:
            for key, value in updates.items():
                if key in _PROJECT_UPDATE_OPTIONAL_FIELDS and key not in body:
                    body[key] = value

        if not body:
            return {
                "error": True,
                "error_type": "invalid_param",
                "message": (
                    "update_project requires at least one writable field "
                    "(name, description, rsid, definition, owner, tags, shares)."
                ),
            }

        merged_definition = False
        if merge_definition and isinstance(body.get("definition"), dict):
            current = await self.get_project(client_id, client_secret, org_id, project_id)
            if current.get("error"):
                return current
            existing_definition = current.get("definition")
            if not isinstance(existing_definition, dict):
                existing_definition = {}
            body["definition"] = _deep_merge(existing_definition, body["definition"])
            merged_definition = True

        result = await self._request(
            client_id,
            client_secret,
            org_id,
            "PUT",
            _project_resource_path(org_id, project_id),
            json_body=body,
        )
        if result.get("error"):
            return _map_adobe_http_error(result, resource="Workspace project")

        updated = _unwrap_project(result)
        message = "Workspace project updated via partial PUT."
        if merged_definition:
            message = (
                "Workspace project updated via partial PUT with opt-in definition merge. "
                "Only the definition subtree was fetched and merged; server-managed "
                "fields were not resent."
            )
        return {
            "success": True,
            "project_id": updated.get("id", project_id),
            "name": updated.get("name", body.get("name")),
            "rsid": updated.get("rsid", body.get("rsid")),
            "updated_fields": sorted(body.keys()),
            "merged_definition": merged_definition,
            "message": message,
        }

    @friendly_errors("Adobe Analytics")
    async def delete_project(self, client_id: str, client_secret: str, org_id: str, project_id: str) -> dict:
        """
        Delete one Workspace project by explicit id.

        DELETE /api/{company_id}/projects/{id}

        Destructive. Requires a concrete project id — no wildcard or bulk delete.
        """
        err = validate_project_id(project_id)
        if err:
            return err

        result = await self._request(
            client_id,
            client_secret,
            org_id,
            "DELETE",
            _project_resource_path(org_id, project_id),
        )
        if result.get("error"):
            return _map_adobe_http_error(result, resource="Workspace project")

        return {
            "success": True,
            "project_id": project_id,
            "message": "Workspace project deleted",
        }

    @friendly_errors("Adobe Analytics")
    async def copy_project(
        self,
        client_id: str,
        client_secret: str,
        org_id: str,
        project_id: str,
        name: str,
    ) -> dict:
        """
        Duplicate a Workspace project: GET the source (with definition) and
        POST a new project under ``name``. Only writable create fields are
        copied (definition, rsid, description, tags, shares) — never owner,
        type, id, created, modified, or expansion-only values.
        """
        err = validate_project_id(project_id)
        if err:
            return err
        err = _require_nonempty("name", name)
        if err:
            return err

        source = await self.get_project(client_id, client_secret, org_id, project_id)
        if source.get("error"):
            return source

        definition = source.get("definition")
        if not isinstance(definition, dict):
            return {
                "error": True,
                "error_type": "invalid_param",
                "message": "Source project has no definition; cannot copy.",
            }
        rsid = source.get("rsid")
        if not rsid:
            return {
                "error": True,
                "error_type": "invalid_param",
                "message": "Source project has no rsid; cannot copy.",
            }

        extra = {key: source[key] for key in _PROJECT_CREATE_OPTIONAL_FIELDS if key in source}
        created = await self.create_project(
            client_id,
            client_secret,
            org_id,
            name=name,
            rsid=str(rsid),
            definition=definition,
            description=source.get("description"),
            extra=extra,
        )
        if created.get("error"):
            return created
        created["copied_from"] = project_id
        created["message"] = "Workspace project copied successfully"
        return created


# ---------------------------------------------------------------------------
# Workspace project helpers (module-private)
# ---------------------------------------------------------------------------

_DEFAULT_LIST_EXPANSION = "reportSuiteName,ownerFullName"

# Adobe Workspace project ids in public examples are hex-like tokens
# (e.g. "6091a10005c7706c0acdd751"). Conservative charset + length bound
# rejects wildcards, path traversal, and URL injection before interpolation.
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Writable top-level fields accepted on POST /projects (create/copy).
# Not forwarded: id, owner, created, modified, type (set by us),
# reportSuiteName, ownerFullName, accessLevel, externalReferences.
_PROJECT_CREATE_OPTIONAL_FIELDS = frozenset({"tags", "shares"})

# Extra keys allowed on a partial PUT besides the first-class kwargs.
_PROJECT_UPDATE_OPTIONAL_FIELDS = frozenset({"tags", "shares"})


def validate_project_id(project_id: Any) -> dict[str, Any] | None:
    """Reject blank, wildcard, or path-like project ids before they are interpolated."""
    if project_id is None or (isinstance(project_id, str) and not project_id.strip()):
        return {
            "error": True,
            "error_type": "invalid_param",
            "message": (
                "project_id is required and must be a non-empty Adobe Workspace "
                "project id (letters, digits, underscore, hyphen only; max 128 chars). "
                "Wildcards, paths, and slashes are not allowed. "
                "Use list_projects to obtain a valid id."
            ),
        }
    pid = str(project_id).strip()
    if not _PROJECT_ID_RE.fullmatch(pid):
        return {
            "error": True,
            "error_type": "invalid_param",
            "message": (
                f"Invalid project_id {pid!r}. Adobe Workspace project ids contain "
                "only letters, digits, underscores, and hyphens (1-128 chars). "
                "Wildcards ('*'), path segments ('../'), and slashes are rejected. "
                "Use list_projects to obtain a valid id."
            ),
        }
    return None


def _project_resource_path(org_id: str, project_id: str) -> str:
    """Build /api/{org}/projects/{id} with a URL-quoted id segment."""
    return f"/api/{org_id}/projects/{quote(str(project_id).strip(), safe='')}"


def _require_nonempty(field: str, value: Any) -> dict[str, Any] | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return {
            "error": True,
            "error_type": "invalid_param",
            "message": f"{field} is required and must be a non-empty string.",
        }
    return None


def _as_str_list(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part).strip() for part in value if str(part).strip()]


def _csv_contains(csv: Any, token: str) -> bool:
    if not csv:
        return False
    return token in {part.strip() for part in str(csv).split(",")}


def _project_list_query_params(
    *,
    expansion: list[str] | str | None,
    include_type: str | None,
    limit: int | None,
    page: int | None,
    locale: str | None,
) -> dict[str, Any]:
    # Official GET /projects query strings only:
    # https://developer.adobe.com/analytics-apis/docs/2.0/guides/endpoints/projects/parameters
    # expansion, includeType, locale, limit, page.
    # filterByIds, ownerId, sortProperty, and sortDirection are not in that
    # guide and must not be emitted.
    params: dict[str, Any] = {}
    if expansion is None:
        params["expansion"] = _DEFAULT_LIST_EXPANSION
    else:
        expansion_csv = ",".join(_as_str_list(expansion))
        if expansion_csv:
            params["expansion"] = expansion_csv
    if include_type:
        params["includeType"] = include_type
    if limit is not None:
        params["limit"] = limit
    if page is not None:
        params["page"] = page
    if locale:
        params["locale"] = locale
    return params


def _project_list_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(result.get("content"), list):
        return [p for p in result["content"] if isinstance(p, dict)]
    if isinstance(result.get("projects"), list):
        return [p for p in result["projects"] if isinstance(p, dict)]
    return []


def _unwrap_project(result: dict[str, Any]) -> dict[str, Any]:
    if isinstance(result.get("project"), dict):
        return result["project"]
    return result


def _summarize_project(project: dict[str, Any], *, include_definition: bool) -> dict[str, Any]:
    owner = project.get("owner")
    owner_out: Any = owner
    if isinstance(owner, dict):
        owner_out = {
            "id": owner.get("id"),
            "name": owner.get("name") or owner.get("fullName") or owner.get("full_name"),
        }
        if project.get("ownerFullName") and not owner_out.get("name"):
            owner_out["name"] = project.get("ownerFullName")
    elif project.get("ownerFullName"):
        owner_out = {"id": owner, "name": project.get("ownerFullName")}

    summary: dict[str, Any] = {
        "id": project.get("id"),
        "name": project.get("name"),
        "description": project.get("description"),
        "rsid": project.get("rsid"),
        "type": project.get("type"),
        "created": project.get("created"),
        "modified": project.get("modified"),
        "owner": owner_out,
    }
    report_suite_name = project.get("reportSuiteName") or project.get("report_suite_name")
    if report_suite_name:
        summary["report_suite_name"] = report_suite_name
    if include_definition and "definition" in project:
        summary["definition"] = project["definition"]
    if "shares" in project:
        summary["shares"] = project["shares"]
    # Official GET /projects expansion extras (see Adobe project parameters).
    # These were previously dropped even when the caller requested them, while
    # unofficial `shares` was forwarded — keep compact defaults, but pass
    # through Adobe-documented expansion fields when present.
    if "tags" in project:
        summary["tags"] = project["tags"]
    access_level = project.get("accessLevel")
    if access_level is not None:
        summary["access_level"] = access_level
    if "externalReferences" in project:
        summary["external_references"] = project["externalReferences"]
    return summary


def _deep_merge(base: Any, overlay: Any) -> Any:
    """Deep-merge ``overlay`` onto ``base``.

    Nested mappings are merged recursively. Lists and scalars in ``overlay``
    replace the corresponding ``base`` value. Keys present only in ``base``
    survive. Keys present only in ``overlay`` are kept (never dropped).
    """
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged: dict[str, Any] = dict(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return overlay


def _extract_adobe_error(raw: Any) -> tuple[str | None, str | None]:
    """Pull Adobe errorCode / errorDescription out of a raw error body."""
    if raw is None:
        return None, None
    payload: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None, None
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None, text
    if not isinstance(payload, dict):
        return None, str(raw)
    code = (
        payload.get("errorCode")
        or payload.get("error_code")
        or payload.get("errorId")
        or payload.get("error")
    )
    if isinstance(code, dict):
        code = code.get("code") or code.get("errorCode")
    message = (
        payload.get("errorDescription")
        or payload.get("error_description")
        or payload.get("errorMessage")
        or payload.get("message")
        or payload.get("errorMsg")
    )
    if isinstance(code, str) and code.lower() in {"true", "false"}:
        code = None
    return (str(code) if code else None), (str(message) if message else None)


def _map_adobe_http_error(result: dict[str, Any], *, resource: str) -> dict[str, Any]:
    """Turn an Adobe HTTP error dict into an actionable tool error envelope."""
    status = result.get("status_code")
    adobe_code, adobe_msg = _extract_adobe_error(result.get("message"))
    if status == 404:
        error_type = "invalid_param"
        message = f"Unknown {resource}. Check the id exists and this IMS org can access it."
    elif status == 403:
        error_type = "insufficient_scope"
        message = (
            f"Adobe Analytics denied access to this {resource}. "
            "The technical account needs Workspace / project permissions on the product profile."
        )
    elif status == 400:
        error_type = "invalid_param"
        message = adobe_msg or f"Adobe Analytics rejected the {resource} payload as invalid."
    elif status == 429:
        error_type = "upstream_error"
        message = "Adobe Analytics is rate-limiting requests. Wait a moment and retry."
    elif status == 401:
        error_type = "not_connected"
        message = "Adobe Analytics authentication failed. Reconnect the Adobe integration."
    else:
        error_type = "upstream_error"
        message = adobe_msg or result.get("message") or f"Adobe Analytics {resource} request failed."

    if adobe_msg and adobe_msg not in message:
        message = f"{message} Adobe: {adobe_msg}"

    out: dict[str, Any] = {
        "error": True,
        "error_type": error_type,
        "status_code": status,
        "message": message,
    }
    if adobe_code:
        out["adobe_error_code"] = adobe_code
    if adobe_msg:
        out["adobe_error_message"] = adobe_msg
    return out
