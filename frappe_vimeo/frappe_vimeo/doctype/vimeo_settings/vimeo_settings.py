# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


from frappe_vimeo.api._utils import _require_manager

CACHE_KEY = "vimeo_settings_snapshot"


class VimeoSettings(Document):
	def on_update(self) -> None:
		# Invalidate cached credentials snapshot used by the Vimeo client.
		frappe.cache().delete_value(CACHE_KEY)

	def validate(self) -> None:
		if self.upload_chunk_size_mb is None:
			self.upload_chunk_size_mb = 1
		self.upload_chunk_size_mb = max(1, int(self.upload_chunk_size_mb))
		self._ensure_app_folder()

	def _ensure_app_folder(self) -> None:
		if not self.app_folder_name:
			return
		if self.app_folder_vimeo_id:
			return

		from frappe_vimeo.vimeo_client import get_client

		try:
			client = get_client(force=True)
		except Exception as exc:
			frappe.throw(_("Could not build Vimeo client: {0}").format(exc))

		self._set_app_folder_from_remote(client, create_if_missing=True)

	def _set_app_folder_from_remote(self, client, create_if_missing: bool = False) -> None:
		from frappe_vimeo.vimeo_client import VimeoAPIError

		name = (self.app_folder_name or "").strip()
		self.app_folder_name = name
		if not name:
			return

		folder = None
		if self.app_folder_vimeo_id:
			try:
				folder = client.get(f"/me/projects/{self.app_folder_vimeo_id}")
			except VimeoAPIError:
				folder = None

		if not folder:
			folder = self._find_top_level_app_folder(client, name)

		if not folder:
			if not create_if_missing:
				frappe.throw(_("Could not find Vimeo app folder '{0}'.").format(name))
			folder = self._create_app_folder(client, name)

		uri = folder.get("uri") or ""
		if not uri:
			frappe.throw(_("Vimeo did not return a URI for the app folder."))

		self.app_folder_vimeo_uri = uri
		self.app_folder_vimeo_id = uri.rsplit("/", 1)[-1]
		self._upsert_local_app_folder()

	def _find_top_level_app_folder(self, client, name: str) -> dict | None:
		from frappe_vimeo.vimeo_client import VimeoAPIError

		page = 1
		while page <= 200:
			try:
				raw = client.list_folders(
					params={"query": name, "page": page, "per_page": 100}
				)
			except VimeoAPIError as e:
				frappe.throw(
					_("Could not check existing Vimeo folders: {0}").format(e.message)
				)

			for item in raw.get("data") or []:
				parent = item.get("parent_folder")
				if parent:
					continue
				if (item.get("name") or "").strip().lower() == name.lower():
					return item

			if not (raw.get("paging") or {}).get("next"):
				break
			page += 1
		return None

	def _create_app_folder(self, client, name: str) -> dict:
		from frappe_vimeo.vimeo_client import VimeoAPIError

		try:
			return client.create_folder(name=name)
		except VimeoAPIError as e:
			frappe.throw(_("Could not create Vimeo folder: {0}").format(e.message))

	def _upsert_local_app_folder(self) -> None:
		# Create/Update local Vimeo Folder record
		if not frappe.db.exists("Vimeo Folder", self.app_folder_name):
			doc = frappe.new_doc("Vimeo Folder")
			doc.folder_name = self.app_folder_name
			doc.vimeo_id = self.app_folder_vimeo_id
			doc.vimeo_uri = self.app_folder_vimeo_uri
			doc.sync_status = "synced"
			doc.insert(ignore_permissions=True)
		else:
			frappe.db.set_value(
				"Vimeo Folder",
				self.app_folder_name,
				{
					"vimeo_id": self.app_folder_vimeo_id,
					"vimeo_uri": self.app_folder_vimeo_uri,
					"sync_status": "synced",
				},
			)

	def get_access_token(self) -> str:
		token = self.get_password("access_token", raise_exception=False)
		if not token:
			frappe.throw(_("Vimeo access token is not configured"))
		return token

	@frappe.whitelist()
	def test_connection(self) -> dict:
		from frappe_vimeo.vimeo_client import VimeoAPIError, get_client

		try:
			client = get_client(force=True)
			me = client.get("/me")

			# Verify or create the app folder and persist the latest remote IDs.
			if self.app_folder_name:
				self._set_app_folder_from_remote(client, create_if_missing=True)
				self.db_set("app_folder_vimeo_id", self.app_folder_vimeo_id)
				self.db_set("app_folder_vimeo_uri", self.app_folder_vimeo_uri)
				self.db_set("app_folder_name", self.app_folder_name)
		except VimeoAPIError as e:
			self.db_set("connection_status", f"FAIL: {e.status} {str(e.message)[:120]}")
			self.db_set("last_tested_on", frappe.utils.now_datetime())
			frappe.throw(_("Vimeo connection failed: {0}").format(e.message))

		self.db_set("connection_status", f"OK ({me.get('name')}) | Folder '{self.app_folder_name}' OK")
		self.db_set("last_tested_on", frappe.utils.now_datetime())
		return {"ok": True, "user": me.get("name"), "uri": me.get("uri")}

	@frappe.whitelist()
	def get_upload_chunk_size_bytes(self) -> int:
		chunk_size_mb = max(1, int(self.upload_chunk_size_mb or 1))
		return chunk_size_mb * 1024 * 1024


	@frappe.whitelist()
	def sync_from_vimeo(self) -> dict:
		"""Trigger a background sync of all folders and videos from Vimeo."""
		_require_manager()
		self.get_access_token()

		if not self.app_folder_vimeo_id:
			frappe.throw(_("Configure and test App Folder Name in Vimeo Settings before syncing."))

		# Enqueue folder pull (which often includes video relationships)
		frappe.enqueue(
			"frappe_vimeo.tasks.pull_folders_from_vimeo",
			queue="long",
			timeout=3600,
		)
		
		# Also enqueue a full video pull to ensure loose videos are imported
		frappe.enqueue(
			"frappe_vimeo.tasks.pull_videos_from_vimeo",
			queue="long",
			timeout=3600,
		)
		
		return {"status": "queued"}


def get_settings() -> "VimeoSettings":
	"""Return the cached Vimeo Settings Single document."""
	return frappe.get_cached_doc("Vimeo Settings", "Vimeo Settings")
