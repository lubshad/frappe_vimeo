# Copyright (c) 2026, CoreAxis Solutions and contributors
"""Search the local Vimeo library across folders and their videos."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from frappe_vimeo.api._utils import _require_manager


@frappe.whitelist()
def search_contents(query: str, limit: int = 20, offset: int = 0) -> dict:
	"""Return a page of folder and video matches, including their location.

	Only content beneath the configured app folder is searchable when one is
	configured, matching the root Contents view. Videos linked in two folders
	appear once per location so each result has an unambiguous folder context.
	"""
	_require_manager()
	query = (query or "").strip()
	if not query:
		frappe.throw(_("Search query is required"))
	limit = cint(limit)
	offset = cint(offset)
	if limit < 1 or limit > 100 or offset < 0:
		frappe.throw(_("Invalid search pagination"))

	app_folder_title = frappe.db.get_single_value("Vimeo Settings", "app_folder_name")
	folder_scope = ""
	video_scope = ""
	scope_params: tuple = ()
	if app_folder_title:
		app_folder = frappe.db.get_value(
			"Vimeo Folder",
			{"folder_name": app_folder_title, "parent_vimeo_folder": ("in", ["", None])},
			["lft", "rgt"],
			as_dict=True,
		)
		if not app_folder:
			return {"items": [], "total": 0}
		folder_scope = "AND location.lft > %s AND location.rgt < %s"
		video_scope = "AND location.lft >= %s AND location.rgt <= %s"
		scope_params = (app_folder.lft, app_folder.rgt)

	# MariaDB LIKE treats backslash as the escape character.
	escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
	pattern = f"%{escaped_query}%"
	folder_query = f"""
		SELECT 'folder' AS kind, location.name, location.folder_name AS title,
			location.parent_vimeo_folder AS folder_name, parent.folder_name AS folder_title,
			location.last_synced_on, location.vimeo_id, location.vimeo_uri,
			location.sync_status, location.sync_error, location.creation,
			NULL AS description, NULL AS privacy, NULL AS duration,
			NULL AS thumbnail_url, NULL AS thumbnail_image, NULL AS status,
			NULL AS transcode_status, NULL AS vimeo_url, NULL AS player_embed_url
		FROM `tabVimeo Folder` location
		LEFT JOIN `tabVimeo Folder` parent ON parent.name = location.parent_vimeo_folder
		WHERE location.folder_name LIKE %s {folder_scope}
	"""
	video_query = f"""
		SELECT 'video' AS kind, video.name, video.video_title AS title,
			location.name AS folder_name, location.folder_name AS folder_title,
			video.last_synced_on, video.vimeo_id, video.vimeo_uri,
			NULL AS sync_status, NULL AS sync_error, video.creation,
			video.description, video.privacy, video.duration,
			video.thumbnail_url, video.thumbnail_image, video.status,
			video.transcode_status, video.vimeo_url, video.player_embed_url
		FROM `tabVimeo Video` video
		INNER JOIN `tabVimeo Folder Video` link
			ON link.video = video.name AND link.parenttype = 'Vimeo Folder'
		INNER JOIN `tabVimeo Folder` location ON location.name = link.parent
		WHERE video.video_title LIKE %s {video_scope}
	"""
	params = (pattern, *scope_params, pattern, *scope_params)
	union = f"{folder_query} UNION {video_query}"
	total = frappe.db.sql(f"SELECT COUNT(*) FROM ({union}) matches", params)[0][0]
	rows = frappe.db.sql(
		f"SELECT * FROM ({union}) matches ORDER BY kind, title, name, folder_name LIMIT %s OFFSET %s",
		(*params, limit, offset),
		as_dict=True,
	)
	return {"items": rows, "total": total}
