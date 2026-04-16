# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt
"""Shared helpers for the frappe_vimeo API package (not whitelisted)."""

from __future__ import annotations

import os
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint

MANAGER_ROLES = ("System Manager", "Vimeo Manager")
VIDEO_FIELD_MAP = {
	"vimeo_id": "id",
	"vimeo_uri": "uri",
	"video_title": "name",
	"description": "description",
	"duration": "duration",
	"created_time": "created_time",
	"modified_time": "modified_time",
	"privacy": "privacy",
	"vimeo_url": "link",
	"player_embed_url": "player_embed_url",
	"thumbnail_url": "thumbnail",
	"status": "status",
	"transcode_status": "transcode_status",
	"width": "width",
	"height": "height",
	"language": "language",
	"license": "license",
	"tags": "tags",
	"embed_html": "embed_html",
}
FOLDER_FIELD_MAP = {
	"vimeo_id": "id",
	"vimeo_uri": "uri",
	"folder_name": "name",
}
TERMINAL_TRANSCODE_STATES = {"complete", "error"}
TERMINAL_VIDEO_STATES = {"available"}
FAILED_VIDEO_STATES = {"error", "failed"}
FAILED_TRANSCODE_STATES = {"error", "failed"}
SYNCABLE_VIDEO_FIELDS = ("video_title", "description", "privacy", "thumbnail_image")
SYNCABLE_FOLDER_FIELDS = ("folder_name", "parent_vimeo_folder")


def _require_manager() -> None:
	"""Raise PermissionError unless the current user can manage Vimeo."""
	user_roles = set(frappe.get_roles(frappe.session.user))
	if not user_roles.intersection(MANAGER_ROLES):
		frappe.throw(
			_("You are not permitted to access Vimeo APIs"),
			frappe.PermissionError,
		)


def _resolve_file_path(file_url: str) -> str:
	"""Resolve a Frappe File `file_url` to an absolute path on disk."""
	if not file_url:
		frappe.throw(_("file_url is required"))

	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not file_name:
		frappe.throw(_("File not found for url: {0}").format(file_url))

	file_doc = frappe.get_doc("File", file_name)
	path = file_doc.get_full_path()
	if not os.path.isfile(path):
		frappe.throw(_("File on disk missing for url: {0}").format(file_url))
	return path


def _pick_largest_thumbnail(sizes: list[dict] | None) -> str | None:
	if not sizes:
		return None
	largest = max(sizes, key=lambda s: (s.get("width") or 0) * (s.get("height") or 0))
	return largest.get("link")


def _normalize_datetime(value: str | None) -> str | None:
	if not value:
		return None
	return frappe.utils.get_datetime(value).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_video(raw: dict) -> dict:
	"""Return the stable public video shape shared across get/list/update/upload."""
	if not raw:
		return {}
	uri = raw.get("uri") or ""
	privacy = raw.get("privacy") or {}
	pictures = raw.get("pictures") or {}
	transcode = raw.get("transcode") or {}
	return {
		"id": uri.rsplit("/", 1)[-1] if uri else None,
		"uri": uri or None,
		"name": raw.get("name"),
		"description": raw.get("description"),
		"duration": raw.get("duration"),
		"created_time": _normalize_datetime(raw.get("created_time")),
		"modified_time": _normalize_datetime(raw.get("modified_time")),
		"privacy": privacy.get("view"),
		"link": raw.get("link"),
		"player_embed_url": raw.get("player_embed_url"),
		"thumbnail": _pick_largest_thumbnail(pictures.get("sizes")),
		"downloads": [
			{"quality": d.get("quality"), "link": d.get("link"), "size": d.get("size")}
			for d in (raw.get("download") or [])
		],
		"status": raw.get("status"),
		"transcode_status": transcode.get("status"),
		"width": raw.get("width"),
		"height": raw.get("height"),
		"language": raw.get("language"),
		"license": raw.get("license"),
		"tags": ", ".join(t.get("name") for t in (raw.get("tags") or []) if t.get("name")),
		"embed_html": (raw.get("embed") or {}).get("html"),
	}


def _normalize_folder(raw: dict) -> dict:
	if not raw:
		return {}
	uri = raw.get("uri") or ""
	parent = raw.get("parent_folder") or {}
	return {
		"id": uri.rsplit("/", 1)[-1] if uri else None,
		"uri": uri or None,
		"name": raw.get("name"),
		"parent_uri": parent.get("uri") or raw.get("parent_folder_uri"),
	}


def _only_present(payload: dict[str, Any]) -> dict[str, Any]:
	"""Drop keys whose value is None (PATCH payload helper)."""
	return {k: v for k, v in payload.items() if v is not None}


def _apply_video_to_doc(doc: Any, normalized_video: dict) -> None:
	for fieldname, payload_key in VIDEO_FIELD_MAP.items():
		setattr(doc, fieldname, normalized_video.get(payload_key))
	
	if normalized_video.get("downloads"):
		doc.set("downloads", [])
		for d in normalized_video["downloads"]:
			doc.append("downloads", {
				"quality": d.get("quality"),
				"link": d.get("link"),
				"size": d.get("size")
			})
			
	doc.last_synced_on = frappe.utils.now_datetime()


def _apply_folder_to_doc(doc: Any, normalized_folder: dict) -> None:
	for fieldname, payload_key in FOLDER_FIELD_MAP.items():
		setattr(doc, fieldname, normalized_folder.get(payload_key))
	doc.sync_status = "synced"
	doc.sync_error = ""
	doc.last_synced_on = frappe.utils.now_datetime()


def _serialize_video_doc(doc: Any) -> dict:
	return {
		"name": doc.name,
		"vimeo_id": doc.vimeo_id,
		"vimeo_uri": doc.vimeo_uri,
		"video_title": doc.video_title,
		"description": doc.description,
		"duration": doc.duration,
		"created_time": doc.created_time,
		"modified_time": doc.modified_time,
		"privacy": doc.privacy,
		"vimeo_url": doc.vimeo_url,
		"player_embed_url": doc.player_embed_url,
		"thumbnail_url": doc.thumbnail_url,
		"thumbnail_image": getattr(doc, "thumbnail_image", None),
		"status": doc.status,
		"transcode_status": doc.transcode_status,
		"last_synced_on": doc.last_synced_on,
		"width": getattr(doc, "width", None),
		"height": getattr(doc, "height", None),
		"language": getattr(doc, "language", None),
		"license": getattr(doc, "license", None),
		"tags": getattr(doc, "tags", None),
		"embed_html": getattr(doc, "embed_html", None),
		"downloads": [{"quality": d.quality, "link": d.link, "size": d.size} for d in (getattr(doc, "downloads", []) or [])],
		"owner": doc.owner,
		"creation": doc.creation,
		"modified": doc.modified,
	}


def _serialize_folder_doc(doc: Any, include_videos: bool = False) -> dict:
	payload = {
		"name": doc.name,
		"folder_name": doc.folder_name,
		"parent_folder_name": getattr(doc, "parent_vimeo_folder", None),
		"vimeo_id": doc.vimeo_id,
		"vimeo_uri": doc.vimeo_uri,
		"sync_status": doc.sync_status,
		"sync_error": doc.sync_error,
		"last_synced_on": doc.last_synced_on,
		"owner": doc.owner,
		"creation": doc.creation,
		"modified": doc.modified,
	}
	if include_videos:
		payload["videos"] = _serialize_folder_videos(doc)
	return payload


def _serialize_folder_videos(doc: Any) -> list[dict]:
	video_names = _get_folder_video_names(doc)
	if not video_names:
		return []
	video_docs = frappe.get_all(
		"Vimeo Video",
		filters={"name": ("in", list(video_names))},
		fields=["name"],
		order_by="creation asc",
	)
	return [_serialize_video_doc(frappe.get_doc("Vimeo Video", row.name)) for row in video_docs]


def _video_needs_status_sync(doc: Any) -> bool:
	status = (doc.status or "").lower()
	transcode_status = (doc.transcode_status or "").lower()
	if _video_has_permanent_failure(doc):
		return False
	if status and status not in TERMINAL_VIDEO_STATES:
		return True
	if transcode_status and transcode_status not in TERMINAL_TRANSCODE_STATES:
		return True
	return not status or not transcode_status


def _video_has_permanent_failure(doc: Any) -> bool:
	status = (getattr(doc, "status", None) or "").lower()
	transcode_status = (getattr(doc, "transcode_status", None) or "").lower()
	return status in FAILED_VIDEO_STATES or transcode_status in FAILED_TRANSCODE_STATES


def _video_needs_metadata_sync(doc: Any) -> bool:
	if not getattr(doc, "vimeo_id", None):
		return False

	last_synced_on = getattr(doc, "last_synced_on", None)
	if not last_synced_on:
		return False

	return frappe.utils.get_datetime(doc.modified) > frappe.utils.get_datetime(last_synced_on)


def _folder_needs_metadata_sync(doc: Any) -> bool:
	if not getattr(doc, "last_synced_on", None):
		return True
	if getattr(doc, "sync_status", None) in {"queued", "blocked", "failed"}:
		return True
	return frappe.utils.get_datetime(doc.modified) > frappe.utils.get_datetime(doc.last_synced_on)


def _get_folder_video_names(doc: Any) -> set[str]:
	return {row.video for row in (doc.get("videos") or []) if getattr(row, "video", None)}


def _get_membership_deltas(previous: Any, current: Any) -> tuple[list[str], list[str]]:
	previous_videos = _get_folder_video_names(previous)
	current_videos = _get_folder_video_names(current)
	return sorted(current_videos - previous_videos), sorted(previous_videos - current_videos)


def _queue_folder_sync(doc: Any) -> None:
	doc.db_set("sync_status", "queued", update_modified=False)
	doc.db_set("sync_error", "", update_modified=False)
	frappe.enqueue(
		"frappe_vimeo.tasks.sync_vimeo_folder",
		folder_name=doc.name,
		queue="default",
		timeout=900,
		enqueue_after_commit=True,
	)


def _parse_video_names(video_names: list[str] | str | None) -> list[str]:
	if video_names is None:
		return []
	if isinstance(video_names, str):
		return [value.strip() for value in video_names.split(",") if value.strip()]
	return [str(value).strip() for value in video_names if str(value).strip()]


def _rebuild_folder_videos(doc: Any, video_names: list[str] | str | None) -> None:
	doc.set("videos", [])
	for video_name in _parse_video_names(video_names):
		doc.append("videos", {"video": video_name})


def _get_descendant_folder_names(folder_name: str) -> list[str]:
	values = frappe.db.get_value("Vimeo Folder", folder_name, ["lft", "rgt"], as_dict=True)
	if not values:
		return []
	return frappe.get_all(
		"Vimeo Folder",
		filters={"lft": (">", values.lft), "rgt": ("<", values.rgt)},
		pluck="name",
		order_by="lft asc",
	)


def _folder_parent_uri(doc: Any) -> str | None:
	if not getattr(doc, "parent_vimeo_folder", None):
		return None
	return frappe.db.get_value("Vimeo Folder", doc.parent_vimeo_folder, "vimeo_uri")


def _folder_parent_ready(doc: Any) -> bool:
	if not getattr(doc, "parent_vimeo_folder", None):
		return True
	return bool(_folder_parent_uri(doc))


def _requeue_blocked_child_folders(parent_folder_name: str) -> None:
	child_names = frappe.get_all(
		"Vimeo Folder",
		filters={"parent_vimeo_folder": parent_folder_name, "sync_status": ("in", ["blocked", "queued", "failed"])},
		pluck="name",
	)
	for child_name in child_names:
		frappe.enqueue(
			"frappe_vimeo.tasks.sync_vimeo_folder",
			folder_name=child_name,
			queue="default",
			timeout=900,
			enqueue_after_commit=True,
		)


def _coerce_bool(value: Any) -> bool:
	return bool(cint(value)) if isinstance(value, (str, int)) else bool(value)
