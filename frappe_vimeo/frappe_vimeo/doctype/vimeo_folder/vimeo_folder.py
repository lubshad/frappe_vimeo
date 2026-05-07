# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet

from frappe_vimeo.api._utils import (
	_folder_needs_metadata_sync,
	_get_folder_video_names,
	_get_membership_deltas,
	_require_manager,
	_require_vimeo_configured,
)

MAX_FOLDER_DEPTH = 10


class VimeoFolder(NestedSet):
	nsm_parent_field = "parent_vimeo_folder"

	def validate(self) -> None:
		if getattr(self.flags, "from_remote_sync", False):
			self._set_is_group_from_children()
			return

		_require_manager()
		self.folder_name = (self.folder_name or "").strip()
		if not self.folder_name:
			frappe.throw(_("Folder Name is required"))
		_require_vimeo_configured()
		self._validate_depth()
		self._validate_video_memberships()
		self._set_is_group_from_children()

	def on_update(self) -> None:
		NestedSet.on_update(self)
		if getattr(self.flags, "from_remote_sync", False):
			return
		self._queue_folder_sync()

	def on_trash(self) -> None:
		_require_manager()
		self._validate_not_configured_app_folder()
		NestedSet.on_trash(self, allow_root_deletion=True)
		if not self.vimeo_id or getattr(self.flags, "ignore_vimeo_delete", False):
			return
		frappe.enqueue(
			"frappe_vimeo.tasks.delete_vimeo_folder",
			folder_name=self.name,
			folder_id=self.vimeo_id,
			delete_remote_contents=bool(getattr(self.flags, "delete_remote_contents", False)),
			queue="default",
			timeout=600,
			enqueue_after_commit=True,
		)

	def _validate_not_configured_app_folder(self) -> None:
		app_folder_name = frappe.db.get_single_value("Vimeo Settings", "app_folder_name")
		app_folder_vimeo_id = frappe.db.get_single_value("Vimeo Settings", "app_folder_vimeo_id")
		app_folder_vimeo_uri = frappe.db.get_single_value("Vimeo Settings", "app_folder_vimeo_uri")
		is_configured_folder = any(
			(
				app_folder_name and self.name == app_folder_name,
				app_folder_name and self.folder_name == app_folder_name,
				app_folder_vimeo_id and self.vimeo_id == app_folder_vimeo_id,
				app_folder_vimeo_uri and self.vimeo_uri == app_folder_vimeo_uri,
			)
		)
		if is_configured_folder:
			frappe.throw(
				_("Cannot delete the Vimeo parent folder configured in Vimeo Settings.")
			)

	def _validate_depth(self) -> None:
		depth = 1
		parent_name = self.parent_vimeo_folder
		visited = {self.name} if self.name else set()
		while parent_name:
			if parent_name in visited:
				frappe.throw(_("Folder hierarchy cannot contain cycles"))
			visited.add(parent_name)
			depth += 1
			if depth > MAX_FOLDER_DEPTH:
				frappe.throw(_("Vimeo folders can only be nested up to 10 levels"))
			parent_name = frappe.db.get_value("Vimeo Folder", parent_name, "parent_vimeo_folder")

	def _set_is_group_from_children(self) -> None:
		if not self.name:
			return
		if frappe.db.exists("Vimeo Folder", {"parent_vimeo_folder": self.name}):
			self.is_group = 1

	def _validate_video_memberships(self) -> None:
		seen_videos: set[str] = set()
		for row in self.get("videos") or []:
			if not row.video:
				continue
			if row.video in seen_videos:
				frappe.throw(_("Video {0} is linked more than once").format(row.video))
			seen_videos.add(row.video)
			vimeo_uri = frappe.db.get_value("Vimeo Video", row.video, "vimeo_uri")
			if not vimeo_uri:
				frappe.throw(_("Video {0} must be synced to Vimeo before it can be assigned to a folder").format(row.video))
			# Vimeo allows a video to live in at most one folder. Mirror that
			# behavior locally: if this video is already linked from another
			# folder, detach it there so the two sides stay consistent. The
			# remote move is handled automatically by Vimeo when this folder's
			# reconcile step calls add_folder_items().
			frappe.db.sql(
				"""
				DELETE FROM `tabVimeo Folder Video`
				WHERE video = %s AND parenttype = 'Vimeo Folder' AND parent != %s
				""",
				(row.video, self.name or ""),
			)

	def _queue_folder_sync(self) -> None:
		previous = self.get_doc_before_save()
		memberships_changed = True
		if previous:
			memberships_changed = _get_folder_video_names(self) != _get_folder_video_names(previous)
		if not previous or _folder_needs_metadata_sync(self) or memberships_changed:
			self.db_set("sync_status", "queued", update_modified=False)
			self.db_set("sync_error", "", update_modified=False)
			frappe.enqueue(
				"frappe_vimeo.tasks.sync_vimeo_folder",
				folder_name=self.name,
				queue="default",
				timeout=900,
				enqueue_after_commit=True,
			)

			if previous and self.vimeo_id and memberships_changed:
				added, removed = _get_membership_deltas(previous, self)
				if added or removed:
					frappe.enqueue(
						"frappe_vimeo.tasks.reconcile_folder_memberships",
						folder_name=self.name,
						added_videos=added,
						removed_videos=removed,
						queue="default",
						timeout=900,
						enqueue_after_commit=True,
					)
