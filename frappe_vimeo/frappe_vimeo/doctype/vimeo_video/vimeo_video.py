# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_vimeo.api._utils import (
	_require_manager,
	_serialize_video_doc,
	SYNCABLE_VIDEO_FIELDS,
	_video_needs_status_sync,
)


class VimeoVideo(Document):
	def validate(self) -> None:
		if getattr(self.flags, "from_status_sync", False) or getattr(self.flags, "from_remote_sync", False):
			return

		_require_manager()
		self.video_title = (self.video_title or "").strip()
		if not self.video_title:
			frappe.throw(_("Video Title is required"))

	def on_trash(self) -> None:
		_require_manager()
		if not self.vimeo_id or getattr(self.flags, "ignore_vimeo_delete", False):
			return
		frappe.enqueue(
			"frappe_vimeo.tasks.delete_vimeo_video",
			video_id=self.vimeo_id,
			record_name=self.name,
			queue="default",
			timeout=300,
			enqueue_after_commit=True,
		)

	def as_api_dict(self) -> dict:
		return _serialize_video_doc(self)

	def on_update(self) -> None:
		if getattr(self.flags, "from_status_sync", False) or getattr(self.flags, "from_remote_sync", False):
			return
		self._enqueue_metadata_sync_if_needed()


	def _enqueue_metadata_sync_if_needed(self) -> None:
		if not self.name or not self.vimeo_id:
			return
		previous = self.get_doc_before_save()
		if not previous:
			return
		if not any(getattr(self, fieldname) != getattr(previous, fieldname) for fieldname in SYNCABLE_VIDEO_FIELDS):
			return
		frappe.enqueue(
			"frappe_vimeo.tasks.sync_video_metadata",
			video_name=self.name,
			queue="default",
			timeout=600,
			enqueue_after_commit=True,
		)
