# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt

from __future__ import annotations

import os
import tempfile
import time

import frappe

from frappe_vimeo.api._utils import (
	_apply_video_to_doc,
	_apply_folder_to_doc,
	_folder_needs_metadata_sync,
	_folder_parent_ready,
	_folder_parent_uri,
	_get_folder_video_names,
	_normalize_folder,
	_normalize_video,
	_only_present,
	_requeue_blocked_child_folders,
	_resolve_file_path,
	_video_has_permanent_failure,
	_video_needs_metadata_sync,
	_video_needs_status_sync,
)
from frappe_vimeo.vimeo_client import VimeoAPIError, get_client

NORMALIZED_FIELDS = (
	"uri,name,description,duration,created_time,modified_time,"
	"privacy.view,link,player_embed_url,pictures.sizes,"
	"download,status,transcode.status,width,height,language,license,tags,embed.html"
)
FOLDER_FIELDS = "uri,name,parent_folder.uri"
PULL_PAGE_SIZE = 100
PULL_MAX_PAGES = 200
FOLDER_TOPOLOGICAL_MAX_PASSES = 20
SYNC_POLL_INTERVAL_SECONDS = 60
SYNC_MAX_POLLS = 5  # Total 5 minutes (5 * 60 seconds)


def _mark_last_synced(doc) -> None:
	last_synced_on = frappe.utils.now_datetime()
	doc.db_set("last_synced_on", last_synced_on, update_modified=False)
	doc.last_synced_on = last_synced_on


def _fetch_video(video_id: str) -> dict:
	return get_client().get(f"/videos/{video_id}", params={"fields": NORMALIZED_FIELDS})


def _mark_folder_sync_state(folder_name: str, sync_status: str, sync_error: str | None = None) -> None:
	if not frappe.db.exists("Vimeo Folder", folder_name):
		return
	doc = frappe.get_doc("Vimeo Folder", folder_name)
	doc.db_set("sync_status", sync_status, update_modified=False)
	doc.sync_status = sync_status
	if sync_error is not None:
		doc.db_set("sync_error", sync_error, update_modified=False)
		doc.sync_error = sync_error


def _mark_folder_last_synced(doc) -> None:
	last_synced_on = frappe.utils.now_datetime()
	doc.db_set("last_synced_on", last_synced_on, update_modified=False)
	doc.last_synced_on = last_synced_on


def sync_video_status(video_name: str) -> None:
	if not frappe.db.exists("Vimeo Video", video_name):
		return

	doc = frappe.get_doc("Vimeo Video", video_name)
	if not doc.vimeo_id:
		return

	try:
		raw = _fetch_video(doc.vimeo_id)
	except VimeoAPIError as e:
		frappe.log_error(message=str(e.body or e.message), title=f"Vimeo sync failed for {video_name}")
		return

	_apply_video_to_doc(doc, _normalize_video(raw))
	doc.flags.from_status_sync = True
	doc.flags.ignore_version = True
	doc.save(ignore_permissions=True)
	_mark_last_synced(doc)
	frappe.db.commit()

	# Notify frontend to refresh
	frappe.publish_realtime(
		"vimeo_video_synced",
		{"video_name": video_name, "status": doc.status},
		doctype="Vimeo Video",
		docname=video_name,
		after_commit=True
	)


def sync_video_until_ready(video_name: str) -> None:
	for _ in range(SYNC_MAX_POLLS):
		if not frappe.db.exists("Vimeo Video", video_name):
			return

		sync_video_status(video_name)
		doc = frappe.get_doc("Vimeo Video", video_name)
		if _video_has_permanent_failure(doc):
			frappe.logger().warning(
				"Vimeo video sync reached permanent failure state",
				extra={
					"video_name": video_name,
					"status": doc.status,
					"transcode_status": doc.transcode_status,
				},
			)
			return
		if not _video_needs_status_sync(doc):
			return
		time.sleep(SYNC_POLL_INTERVAL_SECONDS)


def sync_pending_videos() -> None:
	video_names = frappe.get_all(
		"Vimeo Video",
		fields=["name", "status", "transcode_status"],
		limit=100,
		order_by="modified asc",
	)
	for video in video_names:
		if _video_needs_status_sync(video):
			# Do a single status update instead of starting a long-running loop
			frappe.enqueue(
				"frappe_vimeo.tasks.sync_video_status",
				video_name=video.name,
				queue="default",
				timeout=300
			)


def sync_dirty_videos() -> None:
	video_names = frappe.get_all(
		"Vimeo Video",
		fields=["name", "modified", "last_synced_on", "vimeo_id"],
		limit=100,
		order_by="modified asc",
	)
	for video in video_names:
		if not _video_needs_metadata_sync(video):
			continue
		frappe.enqueue(
			"frappe_vimeo.tasks.sync_video_metadata",
			video_name=video.name,
			queue="default",
			timeout=600,
		)


def sync_pending_folders() -> None:
	folder_names = frappe.get_all(
		"Vimeo Folder",
		fields=["name", "sync_status"],
		limit=100,
		order_by="modified asc",
	)
	for folder in folder_names:
		if folder.sync_status not in {"queued", "blocked", "failed"}:
			continue
		frappe.enqueue(
			"frappe_vimeo.tasks.sync_vimeo_folder",
			folder_name=folder.name,
			queue="default",
			timeout=900,
		)


def sync_dirty_folders() -> None:
	folder_names = frappe.get_all(
		"Vimeo Folder",
		fields=["name", "modified", "last_synced_on", "sync_status"],
		limit=100,
		order_by="modified asc",
	)
	for folder in folder_names:
		if not _folder_needs_metadata_sync(folder):
			continue
		frappe.enqueue(
			"frappe_vimeo.tasks.sync_vimeo_folder",
			folder_name=folder.name,
			queue="default",
			timeout=900,
		)


def delete_vimeo_video(video_id: str, record_name: str | None = None) -> None:
	if not video_id:
		return
	try:
		get_client().delete(f"/videos/{video_id}")
	except VimeoAPIError as e:
		frappe.log_error(
			message=str(e.body or e.message),
			title=f"Vimeo delete failed for {record_name or video_id}",
		)


def delete_vimeo_folder(folder_name: str, folder_id: str, delete_remote_contents: bool = False) -> None:
	if not folder_id:
		return
	try:
		get_client().delete_folder(folder_id, delete_remote_contents=delete_remote_contents)
	except VimeoAPIError as e:
		frappe.log_error(
			message=str(e.body or e.message),
			title=f"Vimeo folder delete failed for {folder_name or folder_id}",
		)


def sync_video_metadata(video_name: str) -> None:
	if not frappe.db.exists("Vimeo Video", video_name):
		return

	doc = frappe.get_doc("Vimeo Video", video_name)
	if not doc.vimeo_id:
		return

	raw: dict | None = None

	try:
		payload = _only_present(
			{
				"name": doc.video_title,
				"description": doc.description,
			}
		)
		if doc.privacy:
			payload["privacy"] = {"view": doc.privacy}
		if payload:
			raw = get_client().patch(f"/videos/{doc.vimeo_id}", json=payload)

		if getattr(doc, "thumbnail_image", None):
			raw = _upload_video_thumbnail(doc)

		if not raw:
			raw = _fetch_video(doc.vimeo_id)
	except VimeoAPIError as e:
		frappe.log_error(
			message=str(e.body or e.message),
			title=f"Vimeo metadata sync failed for {video_name}",
		)
		return

	_apply_video_to_doc(doc, _normalize_video(raw))
	doc.flags.from_remote_sync = True
	doc.flags.ignore_version = True
	doc.save(ignore_permissions=True)
	_mark_last_synced(doc)
	frappe.db.commit()


def sync_vimeo_folder(folder_name: str) -> None:
	if not frappe.db.exists("Vimeo Folder", folder_name):
		return

	doc = frappe.get_doc("Vimeo Folder", folder_name)

	if not _folder_parent_ready(doc):
		doc.db_set("sync_status", "blocked", update_modified=False)
		doc.db_set("sync_error", "Waiting for parent folder to sync", update_modified=False)
		frappe.db.commit()
		return

	_mark_folder_sync_state(folder_name, "syncing", "")
	doc = frappe.get_doc("Vimeo Folder", folder_name)
	parent_folder_uri = _folder_parent_uri(doc)

	try:
		if doc.vimeo_id:
			raw = get_client().update_folder(
				doc.vimeo_id,
				name=doc.folder_name,
				parent_folder_uri=parent_folder_uri,
				set_parent_folder_uri=True,
			)
			if not raw:
				raw = get_client().get_folder(doc.vimeo_id)
		else:
			raw = get_client().create_folder(doc.folder_name, parent_folder_uri=parent_folder_uri)
	except VimeoAPIError as e:
		_mark_folder_sync_state(folder_name, "failed", str(e.message))
		frappe.log_error(
			message=str(e.body or e.message),
			title=f"Vimeo folder sync failed for {folder_name}",
		)
		return

	doc = frappe.get_doc("Vimeo Folder", folder_name)
	_apply_folder_to_doc(doc, _normalize_folder(raw))
	doc.flags.from_remote_sync = True
	doc.flags.ignore_version = True
	doc.save(ignore_permissions=True)
	_mark_folder_last_synced(doc)
	frappe.db.commit()

	reconcile_folder_memberships(folder_name)
	_requeue_blocked_child_folders(folder_name)


def reconcile_folder_memberships(
	folder_name: str,
	added_videos: list[str] | None = None,
	removed_videos: list[str] | None = None,
) -> None:
	if not frappe.db.exists("Vimeo Folder", folder_name):
		return

	doc = frappe.get_doc("Vimeo Folder", folder_name)
	if not doc.vimeo_id:
		return
	if not _folder_parent_ready(doc):
		return

	try:
		if added_videos is None and removed_videos is None:
			remote_items = get_client().list_folder_items(doc.vimeo_id, params={"filter": "video"})
			remote_video_uris = {
				item.get("uri")
				for item in (remote_items.get("data") or [])
				if isinstance(item, dict) and item.get("uri", "").startswith("/videos/")
			}
			local_video_uris = {
				frappe.db.get_value("Vimeo Video", video_name, "vimeo_uri")
				for video_name in _get_folder_video_names(doc)
			}
			local_video_uris = {uri for uri in local_video_uris if uri}
			add_uris = sorted(local_video_uris - remote_video_uris)
			remove_uris = sorted(remote_video_uris - local_video_uris)
		else:
			add_uris = sorted(
				filter(
					None,
					[frappe.db.get_value("Vimeo Video", video_name, "vimeo_uri") for video_name in (added_videos or [])],
				)
			)
			remove_uris = sorted(
				filter(
					None,
					[frappe.db.get_value("Vimeo Video", video_name, "vimeo_uri") for video_name in (removed_videos or [])],
				)
			)

		if add_uris:
			get_client().add_folder_items(doc.vimeo_id, add_uris)
		if remove_uris:
			get_client().remove_folder_items(doc.vimeo_id, remove_uris, should_delete_items=False)
	except VimeoAPIError as e:
		_mark_folder_sync_state(folder_name, "failed", str(e.message))
		frappe.log_error(
			message=str(e.body or e.message),
			title=f"Vimeo folder membership sync failed for {folder_name}",
		)
		return

	doc.db_set("sync_status", "synced", update_modified=False)
	doc.db_set("sync_error", "", update_modified=False)
	_mark_folder_last_synced(doc)
	frappe.db.commit()


def pull_videos_from_vimeo() -> dict:
	"""Import any videos from the connected Vimeo account that aren't tracked locally."""
	created = 0
	skipped = 0
	errors = 0
	client = get_client()

	page = 1
	while page <= PULL_MAX_PAGES:
		try:
			raw = client.get(
				"/me/videos",
				params={"page": page, "per_page": PULL_PAGE_SIZE},
			)
		except VimeoAPIError as e:
			frappe.log_error(
				message=str(e.body or e.message),
				title="Vimeo video pull failed",
			)
			return {"created": created, "skipped": skipped, "errors": errors + 1}

		items = raw.get("data") or []
		for item in items:
			normalized = _normalize_video(item)
			if not normalized.get("id"):
				continue
			if frappe.db.exists("Vimeo Video", {"vimeo_id": normalized["id"]}):
				skipped += 1
				continue
			try:
				doc = frappe.new_doc("Vimeo Video")
				doc.flags.from_remote_sync = True
				doc.flags.ignore_version = True
				_apply_video_to_doc(doc, normalized)
				doc.insert(ignore_permissions=True)
				created += 1
			except Exception as exc:
				errors += 1
				frappe.log_error(
					message=f"{type(exc).__name__}: {exc}",
					title=f"Vimeo video pull insert failed for {normalized.get('id')}",
				)

		if not (raw.get("paging") or {}).get("next"):
			break
		page += 1

	frappe.db.commit()
	return {"created": created, "skipped": skipped, "errors": errors}


def pull_folders_from_vimeo() -> dict:
	"""Import folders from the connected Vimeo account in parent-first order."""
	client = get_client()

	remotes: list[dict] = []
	page = 1
	while page <= PULL_MAX_PAGES:
		try:
			raw = client.get(
				"/me/folders",
				params={"page": page, "per_page": PULL_PAGE_SIZE},
			)
		except VimeoAPIError as e:
			frappe.log_error(
				message=str(e.body or e.message),
				title="Vimeo folder pull failed",
			)
			return {"created": 0, "skipped": 0, "errors": 1}

		items = raw.get("data") or []
		for item in items:
			normalized = _normalize_folder(item)
			if normalized.get("id"):
				remotes.append(normalized)

		if not (raw.get("paging") or {}).get("next"):
			break
		page += 1

	created = 0
	skipped = 0
	errors = 0
	pending = list(remotes)
	# Multi-pass: keep importing folders whose parents are already present
	# (either pre-existing locally or imported in an earlier pass).
	for _ in range(FOLDER_TOPOLOGICAL_MAX_PASSES):
		if not pending:
			break
		next_pending: list[dict] = []
		progress = False
		for remote in pending:
			if frappe.db.exists("Vimeo Folder", {"vimeo_id": remote["id"]}):
				skipped += 1
				progress = True
				continue

			parent_uri = remote.get("parent_uri")
			parent_name: str | None = None
			if parent_uri:
				parent_name = frappe.db.get_value("Vimeo Folder", {"vimeo_uri": parent_uri}, "name")
				if not parent_name:
					# Parent not imported yet — defer to the next pass.
					next_pending.append(remote)
					continue

			try:
				doc = frappe.new_doc("Vimeo Folder")
				doc.flags.from_remote_sync = True
				doc.flags.ignore_version = True
				_apply_folder_to_doc(doc, remote)
				if not doc.folder_name:
					doc.folder_name = remote["id"]
				doc.parent_vimeo_folder = parent_name
				doc.insert(ignore_permissions=True)
				created += 1
				progress = True
			except Exception as exc:
				errors += 1
				progress = True
				frappe.log_error(
					message=f"{type(exc).__name__}: {exc}",
					title=f"Vimeo folder pull insert failed for {remote.get('id')}",
				)
		pending = next_pending
		if not progress:
			break

	if pending:
		# Folders whose parents couldn't be resolved after all passes — log and skip.
		for remote in pending:
			errors += 1
			frappe.log_error(
				message=f"Parent folder missing for remote folder {remote.get('id')}",
				title="Vimeo folder pull parent unresolved",
			)

	frappe.db.commit()
	return {"created": created, "skipped": skipped, "errors": errors}


def _upload_video_thumbnail(doc) -> dict:
	if not doc.thumbnail_image or not doc.vimeo_uri:
		return {}

	temp_path: str | None = None
	try:
		source_path = _resolve_file_path(doc.thumbnail_image)
		_, ext = os.path.splitext(source_path)
		with tempfile.NamedTemporaryFile(delete=False, suffix=ext or ".jpg") as temp_file:
			with open(source_path, "rb") as source_file:
				temp_file.write(source_file.read())
			temp_path = temp_file.name
		return get_client().upload_picture(doc.vimeo_uri, temp_path, activate=True)
	finally:
		if temp_path and os.path.exists(temp_path):
			os.unlink(temp_path)
	return {}
