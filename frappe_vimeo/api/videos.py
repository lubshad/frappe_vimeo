# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt
"""Whitelisted video endpoints for the frappe_vimeo app."""

from __future__ import annotations

import frappe
import requests
from frappe import _

from frappe_vimeo.vimeo_client import VimeoAPIError, get_client
from frappe_vimeo import tasks
from frappe_vimeo.api._utils import (
	_require_manager,
	_serialize_video_doc,
)

TUS_VERSION = "1.0.0"


@frappe.whitelist()
def get_upload_chunk_size_bytes() -> int:
	_require_manager()
	chunk_size_mb = frappe.db.get_single_value("Vimeo Settings", "upload_chunk_size_mb") or 1
	return max(1, int(chunk_size_mb)) * 1024 * 1024


def _create_vimeo_upload_ticket(payload: dict, privacy: str | None = None) -> dict:
	try:
		return get_client().post("/me/videos", json=payload)
	except VimeoAPIError as e:
		if not privacy or e.status != 400:
			raise

		retry_payload = {key: value for key, value in payload.items() if key != "privacy"}
		frappe.log_error(
			message=frappe.as_json(
				{
					"privacy": privacy,
					"payload": payload,
					"vimeo_response": e.body or e.message,
				},
				indent=2,
			),
			title="Vimeo upload ticket privacy rejected",
		)
		return get_client().post("/me/videos", json=retry_payload)


def _clear_stored_upload_ticket(doc: frappe.model.document.Document) -> None:
	"""Remove any stale Vimeo upload session from the local record."""
	doc.vimeo_upload_link = None
	doc.vimeo_id = None
	doc.vimeo_uri = None
	doc.flags.from_remote_sync = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def _get_resumable_ticket_if_valid(doc: frappe.model.document.Document, size: int) -> dict | None:
	"""Return the saved ticket only if the remote tus session is still valid."""
	if not doc.vimeo_id or not doc.vimeo_upload_link:
		return None

	try:
		response = requests.head(
			doc.vimeo_upload_link,
			headers={"Tus-Resumable": TUS_VERSION},
			timeout=30,
		)
	except requests.RequestException:
		_clear_stored_upload_ticket(doc)
		return None

	if response.status_code not in (200, 204):
		_clear_stored_upload_ticket(doc)
		return None

	upload_length = response.headers.get("Upload-Length")
	if upload_length and int(upload_length) != int(size):
		_clear_stored_upload_ticket(doc)
		return None

	return {
		"upload_link": doc.vimeo_upload_link,
		"vimeo_uri": doc.vimeo_uri,
		"vimeo_id": doc.vimeo_id,
		"resuming": True,
	}


@frappe.whitelist()
def get_vimeo_upload_ticket(name: str, video_title: str, size: int, description: str | None = None, privacy: str | None = None) -> dict:
	"""Call Vimeo API to request a TUS upload ticket and save it to the local doc."""
	_require_manager()
	
	if not name:
		frappe.throw(_("Record name is required"))
		
	# Check if we already have a link that might be resumable
	doc = frappe.get_doc("Vimeo Video", name)
	resumable_ticket = _get_resumable_ticket_if_valid(doc, size)
	if resumable_ticket:
		return resumable_ticket

	payload = {
		"upload": {
			"approach": "tus",
			"size": int(size)
		},
		"name": video_title,
		"description": description or "",
	}
	if privacy:
		payload["privacy"] = {"view": privacy}
		
	try:
		res = _create_vimeo_upload_ticket(payload, privacy=privacy)
	except VimeoAPIError as e:
		frappe.log_error(
			message=frappe.as_json(
				{
					"payload": payload,
					"vimeo_response": e.body or e.message,
				},
				indent=2,
			),
			title=f"Vimeo upload ticket failed for {name}",
		)
		frappe.throw(_("Vimeo rejected the upload request: {0}").format(e.message))
	
	upload = res.get("upload") or {}
	upload_link = upload.get("upload_link")
	vimeo_uri = res.get("uri")
	vimeo_id = vimeo_uri.split("/")[-1] if vimeo_uri else None
	
	if not upload_link:
		frappe.throw(_("Failed to get upload link from Vimeo"))
		
	# Persist the ticket so it can be resumed
	doc.vimeo_upload_link = upload_link
	doc.vimeo_id = vimeo_id
	doc.vimeo_uri = vimeo_uri
	doc.flags.from_remote_sync = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()
		
	return {
		"upload_link": upload_link,
		"vimeo_uri": vimeo_uri,
		"vimeo_id": vimeo_id,
		"resuming": False
	}


@frappe.whitelist()
def finalize_vimeo_upload(name: str, vimeo_id: str) -> dict:
	"""Update the local record with the new Vimeo ID after client-side upload finishes."""
	_require_manager()
	if not name:
		frappe.throw(_("name is required"))
		
	doc = frappe.get_doc("Vimeo Video", name)
	doc.vimeo_id = vimeo_id
	doc.vimeo_upload_link = None
	doc.status = "transcoding"  # Mark as processing immediately
	doc.flags.from_remote_sync = True
	doc.save(ignore_permissions=True)
	
	# Kick off a background sync to wait until transcoding is complete
	frappe.enqueue(
		"frappe_vimeo.tasks.sync_video_until_ready",
		video_name=name,
		queue="default",
		timeout=600  # 10 minutes (covers the 5 minute polling)
	)
	
	frappe.db.commit()
	return {"status": "success"}


@frappe.whitelist()
def get_video_record(name: str) -> dict:
	_require_manager()
	if not name:
		frappe.throw(_("name is required"))
	doc = frappe.get_doc("Vimeo Video", name)
	return _serialize_video_doc(doc)


@frappe.whitelist()
def sync_video_record(name: str) -> dict:
	_require_manager()
	if not name:
		frappe.throw(_("name is required"))
	tasks.sync_video_status(name)
	doc = frappe.get_doc("Vimeo Video", name)
	return _serialize_video_doc(doc)


@frappe.whitelist()
def update_video_record(
	name: str,
	video_title: str | None = None,
	description: str | None = None,
	privacy: str | None = None,
) -> dict:
	_require_manager()
	if not name:
		frappe.throw(_("name is required"))
	if all(value is None for value in (video_title, description, privacy)):
		frappe.throw(_("At least one field must be provided"))

	doc = frappe.get_doc("Vimeo Video", name)
	if video_title is not None:
		doc.video_title = video_title
	if description is not None:
		doc.description = description
	if privacy is not None:
		doc.privacy = privacy
	doc.save()
	frappe.db.commit()
	return _serialize_video_doc(doc)


@frappe.whitelist()
def upload_video_thumbnail(name: str) -> dict:
	_require_manager()
	if not name:
		frappe.throw(_("name is required"))

	uploaded_file = frappe.request.files.get("file")
	if not uploaded_file:
		frappe.throw(_("Thumbnail file is required"))
	if uploaded_file.mimetype and not uploaded_file.mimetype.startswith("image/"):
		frappe.throw(_("Thumbnail must be an image file"))

	doc = frappe.get_doc("Vimeo Video", name)
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": uploaded_file.filename,
			"attached_to_doctype": "Vimeo Video",
			"attached_to_name": name,
			"attached_to_field": "thumbnail_image",
			"is_private": 0,
			"content": uploaded_file.stream.read(),
		}
	)
	file_doc.insert(ignore_permissions=True)

	doc.thumbnail_image = file_doc.file_url
	doc.save()
	frappe.db.commit()
	return _serialize_video_doc(doc)


@frappe.whitelist()
def pull_videos_from_vimeo() -> dict:
	"""Kick off a background pull that imports any remote videos missing locally."""
	_require_manager()

	from frappe_vimeo.frappe_vimeo.doctype.vimeo_settings.vimeo_settings import get_settings
	get_settings().get_access_token()

	if not frappe.db.get_single_value("Vimeo Settings", "app_folder_vimeo_id"):
		frappe.throw(_("Configure App Folder Name in Vimeo Settings before pulling."))

	frappe.enqueue(
		"frappe_vimeo.tasks.pull_videos_from_vimeo",
		queue="long",
		timeout=3600,
	)
	return {"status": "queued"}


@frappe.whitelist()
def delete_video_record(name: str) -> dict:
	_require_manager()
	if not name:
		frappe.throw(_("name is required"))
	doc = frappe.get_doc("Vimeo Video", name)
	doc.delete()
	frappe.db.commit()
	return {"deleted": True, "name": name}


@frappe.whitelist()
def upload_video_to_vimeo(name: str) -> dict:
	"""Kick off a background job to upload the attached video_file to Vimeo."""
	_require_manager()
	if not name:
		frappe.throw(_("name is required"))

	doc = frappe.get_doc("Vimeo Video", name)
	if not doc.video_file:
		frappe.throw(_("Please attach a Video File first"))

	if doc.vimeo_id:
		frappe.throw(_("This record is already linked to a Vimeo video (ID: {0})").format(doc.vimeo_id))

	frappe.enqueue(
		"frappe_vimeo.tasks.upload_video_to_vimeo",
		video_name=name,
		queue="long",
		timeout=3600,
	)
	return {"status": "queued"}
