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
"""

import logging
import time
from typing import Any

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
