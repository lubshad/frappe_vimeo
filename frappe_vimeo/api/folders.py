# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt
"""Whitelisted folder endpoints for the frappe_vimeo app."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from frappe_vimeo.api._utils import (
	_get_descendant_folder_names,
	_queue_folder_sync,
	_rebuild_folder_videos,
	_require_manager,
	_serialize_folder_doc,
)


@frappe.whitelist()
def get_app_folder_name() -> str | None:
	_require_manager()
	app_folder_title = frappe.db.get_single_value("Vimeo Settings", "app_folder_name")
	if not app_folder_title:
		return None
	return frappe.db.get_value(
		"Vimeo Folder",
		{"folder_name": app_folder_title, "parent_vimeo_folder": ("in", ["", None])},
		"name"
	)


@frappe.whitelist()
def create_folder_record(
	folder_name: str,
	parent_folder_name: str | None = None,
	video_names: list[str] | str | None = None,
) -> dict:
	_require_manager()
	if not folder_name:
		frappe.throw(_("folder_name is required"))

	doc = frappe.new_doc("Vimeo Folder")
	doc.folder_name = folder_name
	
	if not parent_folder_name:
		# If no parent is specified, we use the App Folder from settings
		app_folder_title = frappe.db.get_single_value("Vimeo Settings", "app_folder_name")
		if app_folder_title:
			parent_folder_name = frappe.db.get_value(
				"Vimeo Folder",
				{"folder_name": app_folder_title, "parent_vimeo_folder": ("in", ["", None])},
				"name"
			)
			
	doc.parent_vimeo_folder = parent_folder_name
	_rebuild_folder_videos(doc, video_names)
	doc.insert()
	frappe.db.commit()
	return _serialize_folder_doc(doc, include_videos=True)


@frappe.whitelist()
def update_folder_record(
	name: str,
	folder_name: str | None = None,
	parent_folder_name: str | None = None,
	video_names: list[str] | str | None = None,
) -> dict:
	_require_manager()
	if not name:
		frappe.throw(_("name is required"))
	if folder_name is None and parent_folder_name is None and video_names is None:
		frappe.throw(_("At least one field must be provided"))

	doc = frappe.get_doc("Vimeo Folder", name)
	if folder_name is not None and folder_name != doc.folder_name:
		new_name = frappe.rename_doc("Vimeo Folder", name, folder_name)
		doc = frappe.get_doc("Vimeo Folder", new_name)

	if parent_folder_name is not None:
		doc.parent_vimeo_folder = parent_folder_name
	if video_names is not None:
		_rebuild_folder_videos(doc, video_names)
	doc.save()
	frappe.db.commit()
	return _serialize_folder_doc(doc, include_videos=True)


@frappe.whitelist()
def get_folder_record(name: str, include_videos: int = 0) -> dict:
	_require_manager()
	if not name:
		frappe.throw(_("name is required"))
	doc = frappe.get_doc("Vimeo Folder", name)
	return _serialize_folder_doc(doc, include_videos=bool(cint(include_videos)))


@frappe.whitelist()
def list_folder_records(
	parent_name: str | None = None,
	include_children: int = 0,
	include_videos: int = 0,
) -> list[dict]:
	_require_manager()

	if parent_name and cint(include_children):
		names = _get_descendant_folder_names(parent_name)
		if parent_name not in names:
			names.insert(0, parent_name)
		filters = {"name": ("in", names)}
	elif parent_name:
		filters = {"parent_vimeo_folder": parent_name}
	else:
		# If no parent is specified, we only show folders under the App Folder Name
		app_folder_title = frappe.db.get_single_value("Vimeo Settings", "app_folder_name")
		if app_folder_title:
			# Find the local record for the app folder
			app_folder_id = frappe.db.get_value(
				"Vimeo Folder",
				{"folder_name": app_folder_title, "parent_vimeo_folder": ("in", ["", None])},
				"name"
			)
			if app_folder_id:
				filters = {"parent_vimeo_folder": app_folder_id}
			else:
				# App folder not yet synced or doesn't exist locally
				return []
		else:
			filters = {"parent_vimeo_folder": ("in", ["", None])}

	names = frappe.get_all(
		"Vimeo Folder",
		filters=filters,
		pluck="name",
		order_by="lft asc",
	)
	return [
		_serialize_folder_doc(
			frappe.get_doc("Vimeo Folder", folder_id),
			include_videos=bool(cint(include_videos)),
		)
		for folder_id in names
	]


@frappe.whitelist()
def sync_folder_record(name: str) -> dict:
	_require_manager()
	if not name:
		frappe.throw(_("name is required"))
	doc = frappe.get_doc("Vimeo Folder", name)
	_queue_folder_sync(doc)
	frappe.db.commit()
	return _serialize_folder_doc(doc, include_videos=True)


@frappe.whitelist()
def pull_folders_from_vimeo() -> dict:
	"""Kick off a background pull that imports any remote folders missing locally."""
	_require_manager()

	from frappe_vimeo.frappe_vimeo.doctype.vimeo_settings.vimeo_settings import get_settings
	get_settings().get_access_token()

	if not frappe.db.get_single_value("Vimeo Settings", "app_folder_vimeo_id"):
		frappe.throw(_("Configure App Folder Name in Vimeo Settings before pulling."))

	frappe.enqueue(
		"frappe_vimeo.tasks.pull_folders_from_vimeo",
		queue="long",
		timeout=3600,
	)
	return {"status": "queued"}


@frappe.whitelist()
def delete_folder_record(name: str, delete_remote_contents: int = 0) -> dict:
	_require_manager()
	if not name:
		frappe.throw(_("name is required"))
	doc = frappe.get_doc("Vimeo Folder", name)
	doc.flags.delete_remote_contents = bool(cint(delete_remote_contents))
	doc.delete()
	frappe.db.commit()
	return {"deleted": True, "name": name}
