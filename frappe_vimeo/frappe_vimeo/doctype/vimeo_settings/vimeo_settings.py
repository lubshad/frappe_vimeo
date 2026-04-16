# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


CACHE_KEY = "vimeo_settings_snapshot"


class VimeoSettings(Document):
	def on_update(self) -> None:
		# Invalidate cached credentials snapshot used by the Vimeo client.
		frappe.cache().delete_value(CACHE_KEY)

	def validate(self) -> None:
		if self.upload_chunk_size_mb is None:
			self.upload_chunk_size_mb = 1
		self.upload_chunk_size_mb = max(1, int(self.upload_chunk_size_mb))

	def get_access_token(self) -> str:
		token = self.get_password("access_token", raise_exception=False)
		if not token:
			frappe.throw(_("Vimeo access token is not configured"))
		return token

	@frappe.whitelist()
	def test_connection(self) -> dict:
		from frappe_vimeo.vimeo_client import VimeoAPIError, get_client

		try:
			me = get_client(force=True).get("/me")
		except VimeoAPIError as e:
			self.db_set("connection_status", f"FAIL: {e.status} {str(e.message)[:120]}")
			self.db_set("last_tested_on", frappe.utils.now_datetime())
			frappe.throw(_("Vimeo connection failed: {0}").format(e.message))

		self.db_set("connection_status", f"OK ({me.get('name')})")
		self.db_set("last_tested_on", frappe.utils.now_datetime())
		return {"ok": True, "user": me.get("name"), "uri": me.get("uri")}

	@frappe.whitelist()
	def get_upload_chunk_size_bytes(self) -> int:
		chunk_size_mb = max(1, int(self.upload_chunk_size_mb or 1))
		return chunk_size_mb * 1024 * 1024


def get_settings() -> "VimeoSettings":
	"""Return the cached Vimeo Settings Single document."""
	return frappe.get_cached_doc("Vimeo Settings", "Vimeo Settings")
