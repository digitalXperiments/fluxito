"""
GCS Storage Service for Dashboard Card Scripts

Manages card scripts in Google Cloud Storage:
  gs://{bucket}/{user_id}/{dashboard_id}/{card_id}.py

Features:
  - Upload: generated card scripts to GCS
  - Download: cached scripts (for re-execution without regeneration)
  - Delete: cleanup when card is removed
  - Auth: service account JSON or Application Default Credentials (ADC)

ADC fallback is useful for GKE, Cloud Run, and local gcloud auth.
"""

from __future__ import annotations

import json
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def _get_client():
    """Return an authenticated GCS client."""
    from google.cloud import storage

    if settings.GCS_SERVICE_ACCOUNT_JSON:
        info = json.loads(settings.GCS_SERVICE_ACCOUNT_JSON)
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(info)
        return storage.Client(credentials=creds, project=info.get("project_id"))

    # Fall back to ADC (works on GKE, Cloud Run, local gcloud auth)
    return storage.Client()


def _blob_path(user_id: str, dashboard_id: str, card_id: str) -> str:
    return f"{user_id}/{dashboard_id}/{card_id}.py"


def upload_card_script(
    user_id: str,
    dashboard_id: str,
    card_id: str,
    script_content: str,
) -> str:
    """
    Upload a card script to GCS.
    Returns the GCS path: gs://{bucket}/{blob_path}
    """
    client = _get_client()
    bucket = client.bucket(settings.GCS_BUCKET_NAME)
    blob_path = _blob_path(user_id, dashboard_id, card_id)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(
        script_content.encode("utf-8"),
        content_type="text/x-python",
    )
    gcs_path = f"gs://{settings.GCS_BUCKET_NAME}/{blob_path}"
    logger.info("Uploaded card script to %s", gcs_path)
    return gcs_path


def download_card_script(gcs_path: str) -> str | None:
    """
    Download a card script from GCS by its full gs:// path.
    Returns the script content as a string, or None if not found.
    """
    try:
        client = _get_client()
        # Strip gs://bucket/ prefix to get blob path
        prefix = f"gs://{settings.GCS_BUCKET_NAME}/"
        if gcs_path.startswith(prefix):
            blob_path = gcs_path[len(prefix) :]
        else:
            blob_path = gcs_path

        bucket = client.bucket(settings.GCS_BUCKET_NAME)
        blob = bucket.blob(blob_path)
        content = blob.download_as_bytes()
        return content.decode("utf-8")
    except Exception as exc:
        logger.warning("Failed to download card script %s: %s", gcs_path, exc)
        return None


def delete_card_script(gcs_path: str) -> None:
    """Delete a card script from GCS. Silently ignores not-found errors."""
    try:
        client = _get_client()
        prefix = f"gs://{settings.GCS_BUCKET_NAME}/"
        blob_path = gcs_path[len(prefix) :] if gcs_path.startswith(prefix) else gcs_path
        bucket = client.bucket(settings.GCS_BUCKET_NAME)
        blob = bucket.blob(blob_path)
        blob.delete()
        logger.info("Deleted card script %s", gcs_path)
    except Exception as exc:
        logger.warning("Failed to delete card script %s: %s", gcs_path, exc)


def delete_dashboard_scripts(user_id: str, dashboard_id: str) -> None:
    """Delete all card scripts for a dashboard (used on dashboard deletion)."""
    try:
        client = _get_client()
        bucket = client.bucket(settings.GCS_BUCKET_NAME)
        prefix = f"{user_id}/{dashboard_id}/"
        blobs = list(bucket.list_blobs(prefix=prefix))
        for blob in blobs:
            blob.delete()
        logger.info("Deleted %d scripts for dashboard %s", len(blobs), dashboard_id)
    except Exception as exc:
        logger.warning("Failed to delete dashboard scripts for %s: %s", dashboard_id, exc)
