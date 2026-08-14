from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .config import ServiceConfig
from .crop import crop_image_annotation, valid_png
from .store import MappingStore, StateStore
from .zotero import ZoteroClient, ZoteroNotFound


SUPPORTED_TYPES = {"highlight", "image"}
LINK_SCHEMA_VERSION = 2
LAYOUT_SCHEMA_VERSION = 7
ZOTERO_ITEM_KEY_PATTERN = re.compile(r"^[A-Z0-9]{8}$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def source_snapshot(annotation: dict[str, Any]) -> dict[str, Any]:
    data = annotation["data"]
    return {
        "key": data["key"],
        "version": int(data.get("version", annotation.get("version", 0))),
        "type": data.get("annotationType", ""),
        "text": data.get("annotationText", ""),
        "comment": data.get("annotationComment", ""),
        "color": data.get("annotationColor", "#ffd400"),
        "pageLabel": data.get("annotationPageLabel", ""),
        "sortIndex": data.get("annotationSortIndex", ""),
        "position": data.get("annotationPosition", "{}"),
        "dateAdded": data.get("dateAdded", ""),
        "dateModified": data.get("dateModified", ""),
    }


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def page_sort_value(label: str) -> tuple[int, str]:
    digits = "".join(character for character in label if character.isdigit())
    return (int(digits) if digits else 10**9, label)


def annotation_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    source = item["source"]
    return (*page_sort_value(str(source.get("pageLabel", ""))), source.get("sortIndex", ""), source.get("dateAdded", ""))


def enclosure_path(attachment: dict[str, Any]) -> Path:
    href = attachment.get("links", {}).get("enclosure", {}).get("href", "")
    parsed = urlparse(href)
    if parsed.scheme != "file":
        raise ValueError("attachment does not expose a local file enclosure")
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(path)


class SyncEngine:
    def __init__(
        self,
        config: ServiceConfig,
        client: ZoteroClient | None = None,
        state: StateStore | None = None,
        mappings: MappingStore | None = None,
    ) -> None:
        self.config = config
        self.client = client or ZoteroClient(config.zotero_api_url)
        self.state = state or StateStore(config.state_file)
        self.mappings = mappings or MappingStore(config.mapping_file)
        self.lock = threading.RLock()
        self.logger = logging.getLogger("zotero-excalidraw-sync")

    def sync(self) -> dict[str, Any]:
        with self.lock, self.state.lock:
            previous_version = int(self.state.data["lastLibraryVersion"])
            limit = self.config.fetch_limit if previous_version else self.config.bootstrap_limit
            response = self.client.annotations(previous_version, limit)
            annotations = response.value if isinstance(response.value, list) else []
            attachment_cache: dict[str, dict[str, Any]] = {}
            for annotation in annotations:
                key = str(annotation.get("key") or annotation.get("data", {}).get("key", ""))
                if self.config.annotation_allowlist and key not in self.config.annotation_allowlist:
                    continue
                annotation_type = annotation.get("data", {}).get("annotationType")
                if annotation_type not in SUPPORTED_TYPES:
                    continue
                self._upsert_annotation(annotation, attachment_cache)

            self._mark_deleted(previous_version)
            self._verify_tracked_sources()
            latest_version = response.last_modified_version or previous_version
            self.state.data["lastLibraryVersion"] = max(previous_version, latest_version)
            self._reconcile_mappings_and_images()
            self.state.save()
            self.logger.info(
                "sync complete version=%s received=%s tracked=%s",
                self.state.data["lastLibraryVersion"],
                len(annotations),
                len(self.state.data["annotations"]),
            )
            return self.state.summary()

    def _upsert_annotation(
        self,
        annotation: dict[str, Any],
        attachment_cache: dict[str, dict[str, Any]],
    ) -> None:
        data = annotation["data"]
        key = str(data["key"])
        attachment_key = str(data["parentItem"])
        attachment = attachment_cache.get(attachment_key)
        if attachment is None:
            attachment = self.client.item(attachment_key)
            attachment_cache[attachment_key] = attachment
        parent_key = str(attachment.get("data", {}).get("parentItem", ""))
        if not parent_key:
            self.logger.warning("annotation skipped key=%s reason=attachment-without-parent", key)
            return

        snapshot = source_snapshot(annotation)
        digest = snapshot_hash(snapshot)
        record = self.state.data["annotations"].get(key)
        if record is None:
            self.state.data["annotations"][key] = {
                "annotationKey": key,
                "attachmentKey": attachment_key,
                "parentItemKey": parent_key,
                "status": "pending",
                "source": snapshot,
                "sourceHash": digest,
                "firstSeenAt": utc_now(),
                "pdfPath": str(enclosure_path(attachment)),
                "zoteroLink": self._zotero_link(attachment_key, snapshot),
                "linkSchemaVersion": LINK_SCHEMA_VERSION,
            }
            self.logger.info("annotation discovered key=%s type=%s", key, snapshot["type"])
            return

        record["source"] = snapshot
        record["pdfPath"] = str(enclosure_path(attachment))
        record["zoteroLink"] = self._zotero_link(attachment_key, snapshot)
        if digest != record.get("sourceHash"):
            record["sourceHash"] = digest
            record["sourceChangedAt"] = utc_now()
            if record.get("status") in {"imported", "source_updated_notified"}:
                record["status"] = "source_updated"
                self.logger.info("source updated key=%s", key)

    @staticmethod
    def _zotero_link(attachment_key: str, snapshot: dict[str, Any]) -> str:
        page = snapshot.get("pageLabel", "")
        try:
            position = json.loads(str(snapshot.get("position", "{}")))
            if isinstance(position.get("pageIndex"), int):
                page = int(position["pageIndex"]) + 1
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        key = snapshot["key"]
        return f"zotero://open-pdf/library/items/{attachment_key}?page={page}&annotation={key}"

    def _mark_deleted(self, previous_version: int) -> None:
        if previous_version <= 0:
            return
        response = self.client.deleted(previous_version)
        deleted = response.value.get("items", []) if isinstance(response.value, dict) else []
        for key in deleted:
            record = self.state.data["annotations"].get(str(key))
            if record and record.get("status") not in {"pending", "ready"}:
                record["status"] = "source_missing"
                record["sourceMissingAt"] = utc_now()
                self.logger.info("source missing key=%s", key)

    def _verify_tracked_sources(self) -> None:
        last_check = self.state.data.get("lastSourceCheckAt")
        if last_check:
            try:
                last_datetime = datetime.fromisoformat(str(last_check))
                if datetime.now(UTC) - last_datetime < timedelta(seconds=self.config.deletion_check_interval_seconds):
                    return
            except ValueError:
                pass

        checkable_statuses = {
            "ready",
            "imported",
            "source_updated",
            "source_updated_notified",
            "manually_deleted",
        }
        for record in self.state.data["annotations"].values():
            if record.get("status") not in checkable_statuses:
                continue
            try:
                self.client.item(record["annotationKey"])
            except ZoteroNotFound:
                record["status"] = "source_missing"
                record["sourceMissingAt"] = utc_now()
                self.logger.info("source missing key=%s", record["annotationKey"])
        self.state.data["lastSourceCheckAt"] = utc_now()

    def _reconcile_mappings_and_images(self) -> None:
        mappings = self.mappings.read()
        for record in self.state.data["annotations"].values():
            correct_link = self._zotero_link(record["attachmentKey"], record["source"])
            previous_link = record.get("zoteroLink")
            if (
                record.get("status") in {"imported", "source_updated_notified", "link_repair"}
                and previous_link != correct_link
            ):
                record["status"] = "link_repair"
            record["zoteroLink"] = correct_link
            if previous_link == correct_link and not record.get("linkSchemaVersion"):
                record["linkSchemaVersion"] = LINK_SCHEMA_VERSION
            if (
                record.get("status") in {"imported", "source_updated_notified", "source_missing", "link_repair"}
                and int(record.get("layoutSchemaVersion", 1)) < LAYOUT_SCHEMA_VERSION
            ):
                if record.get("status") == "source_missing":
                    record["layoutRepairReturnStatus"] = "source_missing"
                record["status"] = "layout_repair"
            if record.get("status") not in {"pending", "ready"}:
                continue
            canvas_path = mappings.get(record["parentItemKey"])
            if not canvas_path:
                record["status"] = "pending"
                record.pop("canvasPath", None)
                continue
            record["canvasPath"] = canvas_path
            if record["source"]["type"] == "image":
                try:
                    image_path = self.config.vault_path / self.config.images_path / f"{record['annotationKey']}.png"
                    crop_image_annotation(
                        Path(record["pdfPath"]),
                        record["source"]["position"],
                        image_path,
                        self.config.image_scale,
                    )
                    record["imagePath"] = image_path.relative_to(self.config.vault_path).as_posix()
                except Exception as error:  # keep the annotation retryable
                    record["status"] = "pending"
                    record["lastError"] = f"image crop failed: {type(error).__name__}: {error}"
                    self.logger.warning("image crop failed key=%s error=%s", record["annotationKey"], type(error).__name__)
                    continue
            record.pop("lastError", None)
            record["status"] = "ready"

    def queue(self) -> list[dict[str, Any]]:
        with self.lock, self.state.lock:
            self._reconcile_mappings_and_images()
            self.state.save()
            records = [
                dict(record)
                for record in self.state.data["annotations"].values()
                if record.get("status") in {"ready", "source_updated", "link_repair", "layout_repair"}
            ]
            return sorted(records, key=annotation_sort_key)

    def acknowledge(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = str(payload.get("annotationKey", ""))
        action = str(payload.get("action", "imported"))
        with self.lock, self.state.lock:
            record = self.state.data["annotations"].get(key)
            if record is None:
                raise KeyError(f"unknown annotation key: {key}")
            if action == "imported":
                return_status = record.pop("layoutRepairReturnStatus", None)
                record["status"] = str(return_status or "imported")
                record["canvasPath"] = str(payload.get("canvasPath", record.get("canvasPath", "")))
                record["elementIds"] = [str(value) for value in payload.get("elementIds", [])]
                record.setdefault("sourceSnapshot", dict(record["source"]))
                record.setdefault("importedAt", utc_now())
                record["linkSchemaVersion"] = LINK_SCHEMA_VERSION
                record["layoutSchemaVersion"] = LAYOUT_SCHEMA_VERSION
            elif action == "source-updated-notified":
                record["status"] = "source_updated_notified"
                record["sourceUpdateNotifiedAt"] = utc_now()
                record["updateElementIds"] = [str(value) for value in payload.get("elementIds", [])]
            elif action == "manual-delete":
                record["status"] = "manually_deleted"
                record["manuallyDeletedAt"] = utc_now()
            else:
                raise ValueError(f"unsupported acknowledgement action: {action}")
            self.state.save()
            self.logger.info("ack key=%s action=%s", key, action)
            return {"annotationKey": key, "status": record["status"]}

    def bind(self, parent_item_key: str, canvas_path: str) -> dict[str, Any]:
        with self.lock:
            self.mappings.bind(parent_item_key, canvas_path)
            with self.state.lock:
                self._reconcile_mappings_and_images()
                self.state.save()
            return {"parentItemKey": parent_item_key, "canvasPath": canvas_path.replace("\\", "/")}

    def canvas_status(self, parent_item_key: str) -> dict[str, Any]:
        key = parent_item_key.strip().upper()
        if not ZOTERO_ITEM_KEY_PATTERN.fullmatch(key):
            raise ValueError("parent item key must be an 8-character Zotero item key")
        with self.lock:
            canvas_path = self.mappings.read().get(key)
            return {
                "parentItemKey": key,
                "mapped": bool(canvas_path),
                "canvasPath": canvas_path or "",
            }

    def reimport(self, annotation_key: str) -> dict[str, Any]:
        with self.lock, self.state.lock:
            record = self.state.data["annotations"].get(annotation_key)
            if record is None:
                raise KeyError(f"unknown annotation key: {annotation_key}")
            record["status"] = "pending"
            record.pop("elementIds", None)
            record["reimportRequestedAt"] = utc_now()
            self._reconcile_mappings_and_images()
            self.state.save()
            return {"annotationKey": annotation_key, "status": record["status"]}

    def request_canvas(self, payload: dict[str, Any]) -> dict[str, Any]:
        parent_item_key = str(payload.get("parentItemKey", "")).strip().upper()
        if not ZOTERO_ITEM_KEY_PATTERN.fullmatch(parent_item_key):
            raise ValueError("parent item key must be an 8-character Zotero item key")

        metadata = {
            "title": str(payload.get("title", "")).strip()[:500],
            "year": str(payload.get("year", "")).strip()[:20],
            "firstCreator": str(payload.get("firstCreator", "")).strip()[:200],
        }
        with self.lock, self.state.lock:
            requests = self.state.data.setdefault("canvasRequests", {})
            for request in requests.values():
                if request.get("parentItemKey") == parent_item_key:
                    request.update(metadata)
                    request["requestedAt"] = utc_now()
                    self.state.save()
                    return dict(request)

            request_id = uuid.uuid4().hex
            request = {
                "requestId": request_id,
                "parentItemKey": parent_item_key,
                **metadata,
                "requestedAt": utc_now(),
            }
            canvas_path = self.mappings.read().get(parent_item_key)
            if canvas_path:
                request["canvasPath"] = canvas_path
            requests[request_id] = request
            self.state.save()
            self.logger.info("canvas requested parent=%s mapped=%s", parent_item_key, bool(canvas_path))
            return dict(request)

    def canvas_requests(self) -> list[dict[str, Any]]:
        with self.lock, self.state.lock:
            mappings = self.mappings.read()
            requests = []
            for request in self.state.data.setdefault("canvasRequests", {}).values():
                item = dict(request)
                canvas_path = mappings.get(str(item.get("parentItemKey", "")))
                if canvas_path:
                    item["canvasPath"] = canvas_path
                requests.append(item)
            return sorted(requests, key=lambda item: str(item.get("requestedAt", "")))

    def acknowledge_canvas_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(payload.get("requestId", "")).strip()
        action = str(payload.get("action", "completed")).strip()
        with self.lock, self.state.lock:
            requests = self.state.data.setdefault("canvasRequests", {})
            request = requests.get(request_id)
            if request is None:
                raise KeyError(f"unknown canvas request: {request_id}")

            if action == "completed":
                canvas_path = str(payload.get("canvasPath", "")).strip().replace("\\", "/")
                if not canvas_path:
                    raise ValueError("canvasPath is required for a completed canvas request")
                self.mappings.bind(str(request["parentItemKey"]), canvas_path)
                self._reconcile_mappings_and_images()
                result = {
                    "requestId": request_id,
                    "status": "completed",
                    "parentItemKey": request["parentItemKey"],
                    "canvasPath": canvas_path,
                }
                self.logger.info("canvas completed parent=%s", request["parentItemKey"])
            elif action == "failed":
                result = {
                    "requestId": request_id,
                    "status": "failed",
                    "parentItemKey": request["parentItemKey"],
                }
                self.logger.warning("canvas failed parent=%s", request["parentItemKey"])
            else:
                raise ValueError(f"unsupported canvas request action: {action}")

            del requests[request_id]
            self.state.save()
            return result
