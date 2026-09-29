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
from frappe_vimeo.vimeo_client import VimeoAPIError, VimeoClient, get_client

NORMALIZED_FIELDS = (
	"uri,name,description,duration,created_time,modified_time,"
	"privacy.view,link,player_embed_url,pictures.sizes,"
	"download,status,transcode.status,width,height,language,license,tags,embed.html"
)
PULL_PAGE_SIZE = 100
PULL_MAX_PAGES = 200
PULL_MAX_FOLDERS = 2000
FOLDER_TOPOLOGICAL_MAX_PASSES = 20
SYNC_POLL_INTERVAL_SECONDS = 60
SYNC_MAX_POLLS = 5  # Total 5 minutes (5 * 60 seconds)


def _fetch_folder_items(client: VimeoClient, folder_id: str) -> list[dict]:
	"""Fetch a complete folder listing, including subfolders and videos."""
	items: list[dict] = []
	for page in range(1, PULL_MAX_PAGES + 1):
		raw = client.list_folder_items(folder_id, params={"page": page, "per_page": PULL_PAGE_SIZE})
		for entry in raw.get("data") or []:
			if not isinstance(entry, dict):
				continue
			kind = entry.get("type")
			if kind in ("folder", "video") and kind in entry:
				item = entry[kind]
				if not isinstance(item, dict) or not item.get("uri"):
					raise VimeoAPIError(0, f"Vimeo returned an invalid {kind} item in folder {folder_id}")
				items.append(item)
			elif entry.get("uri"):
				items.append(entry)
		if not (raw.get("paging") or {}).get("next"):
			return items
	raise VimeoAPIError(0, f"Vimeo item listing exceeded the page limit for folder {folder_id}")


def _fetch_app_subtree(client: VimeoClient) -> tuple[list[dict], dict[str, list[dict]]]:
	"""Walk from the configured root; /me/folders need not contain descendants."""
	root_id = frappe.db.get_single_value("Vimeo Settings", "app_folder_vimeo_id")
	root_uri = frappe.db.get_single_value("Vimeo Settings", "app_folder_vimeo_uri")
	root_name = frappe.db.get_single_value("Vimeo Settings", "app_folder_name")
	if not root_id or not root_uri:
		raise VimeoAPIError(0, "Configure and test the Vimeo app folder before importing")
	root = {"id": root_id, "uri": root_uri, "name": root_name or root_id, "parent_uri": None}
	queue = [root]
	folders: list[dict] = []
	videos: dict[str, list[dict]] = {}
	seen: set[str] = set()
	while queue:
		folder = queue.pop(0)
		folder_id = folder["id"]
		if folder_id in seen:
			continue
		if len(seen) >= PULL_MAX_FOLDERS:
			raise VimeoAPIError(0, "Vimeo folder import exceeded the folder limit")
		seen.add(folder_id)
		folders.append(folder)
		items = _fetch_folder_items(client, folder_id)
		videos[folder_id] = []
		for item in items:
			uri = item.get("uri") or ""
			if uri.startswith("/videos/"):
				videos[folder_id].append(item)
			elif "/projects/" in uri or "/folders/" in uri:
				child = _normalize_folder(item)
				if child.get("id") and child["id"] not in seen:
					child["parent_uri"] = folder["uri"]
					queue.append(child)
	return folders, videos


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
	desired_folder_name = doc.folder_name

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
	normalized_folder = _normalize_folder(raw)
	if doc.vimeo_id:
		# Vimeo can return stale or partial folder data immediately after a
		# rename. Keep the local name that triggered this sync instead of
		# reverting the UI back to the previous remote value.
		normalized_folder["name"] = desired_folder_name
	_apply_folder_to_doc(doc, normalized_folder)
	if frappe.db.exists("Vimeo Folder", {"parent_vimeo_folder": doc.name}):
		doc.is_group = 1
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


def pull_videos_from_vimeo(snapshot: tuple[list[dict], dict[str, list[dict]]] | None = None) -> dict:
	"""Import videos and memberships in the configured app folder subtree."""
	created = 0
	skipped = 0
	errors = 0

	app_folder_id = frappe.db.get_single_value("Vimeo Settings", "app_folder_vimeo_id")
	if not app_folder_id:
		frappe.log_error(
			message="app_folder_vimeo_id is not set on Vimeo Settings",
			title="Vimeo video pull skipped",
		)
		return {"created": 0, "skipped": 0, "errors": 1}

	try:
		remotes, remote_videos = snapshot if snapshot is not None else _fetch_app_subtree(get_client())
	except VimeoAPIError as e:
		frappe.log_error(
			message=e.message,
			title="Vimeo video pull failed (folder fetch)",
		)
		return {"created": 0, "skipped": 0, "errors": 1}

	allowed_folder_ids = {remote["id"] for remote in remotes}

	# Folder item listings are authoritative for membership; /me/videos does
	# not consistently include a parent_folder field.
	folder_names = {
		row.vimeo_id: row.name
		for row in frappe.get_all(
			"Vimeo Folder",
			filters={"vimeo_id": ("in", list(allowed_folder_ids))},
			fields=["name", "vimeo_id"],
		)
	}
	if len(folder_names) != len(allowed_folder_ids):
		frappe.log_error("Import folders before importing their videos", "Vimeo video pull failed")
		return {"created": 0, "skipped": 0, "errors": 1}

	memberships: dict[str, set[str]] = {}
	video_names: dict[str, str] = {}
	for folder_id, folder_name in folder_names.items():
		items = remote_videos[folder_id]
		memberships[folder_name] = set()
		for item in items:
			normalized = _normalize_video(item)
			video_id = normalized.get("id")
			if not video_id:
				continue
			if video_id not in video_names:
				video_name = frappe.db.get_value("Vimeo Video", {"vimeo_id": video_id}, "name")
				if video_name:
					skipped += 1
					video = frappe.get_doc("Vimeo Video", video_name)
					if (normalized.get("name") and video.video_title != normalized["name"]) or (
						normalized.get("privacy") and video.privacy != normalized["privacy"]
					):
						if normalized.get("name"):
							video.video_title = normalized["name"]
						if normalized.get("privacy"):
							video.privacy = normalized["privacy"]
						video.last_synced_on = frappe.utils.now_datetime()
						video.flags.from_remote_sync = True
						video.flags.ignore_version = True
						video.save(ignore_permissions=True)
				else:
					try:
						frappe.db.savepoint("vimeo_video_pull_insert")
						doc = frappe.new_doc("Vimeo Video")
						doc.flags.from_remote_sync = True
						doc.flags.ignore_version = True
						_apply_video_to_doc(doc, normalized)
						doc.insert(ignore_permissions=True)
						video_name = doc.name
						created += 1
					except Exception as exc:
						frappe.db.rollback(save_point="vimeo_video_pull_insert")
						errors += 1
						frappe.log_error(message=f"{type(exc).__name__}: {exc}", title=f"Vimeo video pull insert failed for {video_id}")
						continue
				video_names[video_id] = video_name
			memberships[folder_name].add(video_names[video_id])

	# Do not remove local links based on a partial/failed Vimeo listing.
	if errors:
		frappe.db.commit()
		return {"created": created, "skipped": skipped, "errors": errors}
	for folder_name, names in memberships.items():
		folder = frappe.get_doc("Vimeo Folder", folder_name)
		if _get_folder_video_names(folder) == names:
			continue
		folder.set("videos", [])
		for video_name in sorted(names):
			folder.append("videos", {"video": video_name})
		folder.flags.from_remote_sync = True
		folder.flags.ignore_version = True
		folder.save(ignore_permissions=True)

	frappe.db.commit()
	return {"created": created, "skipped": skipped, "errors": errors}


def pull_folders_from_vimeo(snapshot: tuple[list[dict], dict[str, list[dict]]] | None = None) -> dict:
	"""Import folders from the connected Vimeo account in parent-first order.

	Scoped to the configured app folder subtree — folders outside it are ignored.
	"""
	app_folder_id = frappe.db.get_single_value("Vimeo Settings", "app_folder_vimeo_id")
	if not app_folder_id:
		frappe.log_error(
			message="app_folder_vimeo_id is not set on Vimeo Settings",
			title="Vimeo folder pull skipped",
		)
		return {"created": 0, "skipped": 0, "errors": 1}

	try:
		remotes, _ = snapshot if snapshot is not None else _fetch_app_subtree(get_client())
	except VimeoAPIError as e:
		frappe.log_error(
			message=e.message,
			title="Vimeo folder pull failed",
		)
		return {"created": 0, "skipped": 0, "errors": 1}

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
				frappe.db.savepoint("vimeo_folder_pull_insert")
				if parent_name:
					parent = frappe.get_doc("Vimeo Folder", parent_name)
					if not parent.is_group:
						parent.is_group = 1
						parent.flags.from_remote_sync = True
						parent.save(ignore_permissions=True)
				doc = frappe.new_doc("Vimeo Folder")
				doc.flags.from_remote_sync = True
				doc.flags.ignore_version = True
				_apply_folder_to_doc(doc, remote)
				if not doc.folder_name:
					doc.folder_name = remote["id"]
				doc.parent_vimeo_folder = parent_name
				if any(r.get("parent_uri") == remote.get("uri") for r in remotes):
					doc.is_group = 1
				doc.insert(ignore_permissions=True)
				created += 1
				progress = True
			except Exception as exc:
				frappe.db.rollback(save_point="vimeo_folder_pull_insert")
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


def pull_from_vimeo() -> dict:
	"""Import the configured folder subtree in order, with durable job status."""
	settings = frappe.get_doc("Vimeo Settings", "Vimeo Settings")
	settings.db_set("pull_status", "syncing")
	frappe.db.commit()
	try:
		snapshot = _fetch_app_subtree(get_client())
		folders = pull_folders_from_vimeo(snapshot)
		if folders["errors"]:
			raise RuntimeError(f"Folder import reported {folders['errors']} error(s). Check Error Log.")
		videos = pull_videos_from_vimeo(snapshot)
		if videos["errors"]:
			raise RuntimeError(f"Video import reported {videos['errors']} error(s). Check Error Log.")
		summary = (
			f"Folders: {folders['created']} added, {folders['skipped']} existing; "
			f"videos: {videos['created']} added, {videos['skipped']} existing."
		)
		settings.db_set("last_pull_summary", summary)
		settings.db_set("last_pull_on", frappe.utils.now_datetime())
		settings.db_set("pull_error", "")
		settings.db_set("pull_status", "complete")
		frappe.db.commit()
		return {"folders": folders, "videos": videos}
	except Exception as exc:
		frappe.db.rollback()
		message = str(exc) if isinstance(exc, RuntimeError) else "Vimeo import failed. Check Error Log."
		settings.db_set("pull_error", message)
		settings.db_set("pull_status", "failed")
		frappe.db.commit()
		if not isinstance(exc, RuntimeError):
			frappe.log_error(message=type(exc).__name__, title="Vimeo import failed")
		return {"status": "failed", "error": message}


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
