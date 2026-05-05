# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt
"""Thin HTTP client for the Vimeo API v3.4 with tus resumable upload support."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urljoin

import frappe
import requests

VIMEO_ACCEPT = "application/vnd.vimeo.*+json;version=3.4"
TUS_VERSION = "1.0.0"
CHUNK_SIZE = 64 * 1024 * 1024  # 64 MB
DEFAULT_TIMEOUT = 60
UPLOAD_TIMEOUT = 600

CACHE_KEY = "vimeo_settings_snapshot"
CACHE_TTL_SECONDS = 300


class VimeoAPIError(Exception):
	"""Raised for any non-2xx response from Vimeo or transport failure."""

	def __init__(self, status: int, message: str, body: Any = None) -> None:
		super().__init__(f"[{status}] {message}")
		self.status = status
		self.message = message
		self.body = body


class VimeoClient:
	def __init__(self, access_token: str, api_base: str = "https://api.vimeo.com", timeout: int = DEFAULT_TIMEOUT) -> None:
		self.access_token = access_token
		self.api_base = api_base.rstrip("/") + "/"
		self.timeout = timeout
		self._session: requests.Session | None = None

	def _get_session(self) -> requests.Session:
		if self._session is None:
			s = requests.Session()
			s.headers.update(
				{
					"Authorization": f"Bearer {self.access_token}",
					"Accept": VIMEO_ACCEPT,
					"Content-Type": "application/json",
				}
			)
			self._session = s
		return self._session

	def _absolute(self, path_or_url: str) -> str:
		if path_or_url.startswith(("http://", "https://")):
			return path_or_url
		return urljoin(self.api_base, path_or_url.lstrip("/"))

	def _request(self, method: str, path_or_url: str, **kwargs: Any) -> dict:
		url = self._absolute(path_or_url)
		try:
			response = self._get_session().request(method, url, timeout=self.timeout, **kwargs)
		except requests.RequestException as e:
			raise VimeoAPIError(0, f"Vimeo request failed: {e}", body=None) from e

		if response.status_code == 204 or not response.content:
			return {}

		try:
			body = response.json()
		except ValueError:
			body = response.text

		if not (200 <= response.status_code < 300):
			message = body.get("error") if isinstance(body, dict) else str(body)
			raise VimeoAPIError(response.status_code, message or response.reason, body=body)

		return body if isinstance(body, dict) else {"data": body}

	def get(self, path: str, params: dict | None = None) -> dict:
		return self._request("GET", path, params=params)

	def post(self, path: str, json: dict | None = None) -> dict:
		return self._request("POST", path, json=json)

	def patch(self, path: str, json: dict | None = None) -> dict:
		return self._request("PATCH", path, json=json)

	def delete(self, path: str, params: dict | None = None) -> dict:
		return self._request("DELETE", path, params=params)

	def put(self, path_or_url: str, data: Any = None, headers: dict[str, str] | None = None) -> dict:
		request_headers = headers or {}
		url = self._absolute(path_or_url)
		try:
			response = self._get_session().put(url, data=data, headers=request_headers, timeout=self.timeout)
		except requests.RequestException as e:
			raise VimeoAPIError(0, f"Vimeo request failed: {e}", body=None) from e

		if response.status_code == 204 or not response.content:
			return {}

		try:
			body = response.json()
		except ValueError:
			body = response.text

		if not (200 <= response.status_code < 300):
			message = body.get("error") if isinstance(body, dict) else str(body)
			raise VimeoAPIError(response.status_code, message or response.reason, body=body)

		return body if isinstance(body, dict) else {"data": body}

	def upload_picture(self, video_uri: str, file_path: str, activate: bool = True) -> dict:
		if not os.path.isfile(file_path):
			raise VimeoAPIError(0, f"File not found on disk: {file_path}")

		video = self.get(video_uri, params={"fields": "metadata.connections.pictures.uri"})
		pictures_uri = (((video.get("metadata") or {}).get("connections") or {}).get("pictures") or {}).get("uri")
		if not pictures_uri:
			raise VimeoAPIError(0, "Vimeo did not return a pictures upload endpoint", body=video)

		picture = self.post(pictures_uri, json=None)
		upload_link = picture.get("link")
		picture_uri = picture.get("uri")
		if not upload_link or not picture_uri:
			raise VimeoAPIError(0, "Vimeo did not return picture upload details", body=picture)

		with open(file_path, "rb") as file_handle:
			try:
				response = requests.put(
					upload_link,
					data=file_handle,
					headers={
						"Accept": VIMEO_ACCEPT,
						"Content-Type": "application/octet-stream",
					},
					timeout=self.timeout,
				)
			except requests.RequestException as e:
				raise VimeoAPIError(0, f"Vimeo picture upload failed: {e}", body=None) from e
			if response.status_code not in (200, 201):
				try:
					body: Any = response.json()
				except ValueError:
					body = response.text
				message = body.get("error") if isinstance(body, dict) else str(body)
				raise VimeoAPIError(response.status_code, message or "Vimeo picture upload failed", body=body)

		if activate:
			self.patch(picture_uri, json={"active": True})

		return self.get(video_uri)

	def tus_upload(
		self,
		file_path: str,
		name: str,
		description: str | None = None,
		privacy: str | None = None,
	) -> dict:
		"""Upload a local file to Vimeo using the tus resumable protocol.

		Returns the fully populated video resource (post-upload GET).
		"""
		if not os.path.isfile(file_path):
			raise VimeoAPIError(0, f"File not found on disk: {file_path}")

		size = os.path.getsize(file_path)
		payload: dict = {
			"upload": {"approach": "tus", "size": size},
			"name": name,
		}
		if description is not None:
			payload["description"] = description
		if privacy:
			payload["privacy"] = {"view": privacy}

		created = self.post("/me/videos", json=payload)
		upload_info = created.get("upload") or {}
		upload_link = upload_info.get("upload_link")
		video_uri = created.get("uri")
		if not upload_link or not video_uri:
			raise VimeoAPIError(0, "Vimeo did not return an upload link", body=created)

		offset = 0
		with open(file_path, "rb") as fh:
			while offset < size:
				fh.seek(offset)
				chunk = fh.read(CHUNK_SIZE)
				if not chunk:
					break
				try:
					resp = requests.patch(
						upload_link,
						data=chunk,
						headers={
							"Tus-Resumable": TUS_VERSION,
							"Upload-Offset": str(offset),
							"Content-Type": "application/offset+octet-stream",
							"Accept": VIMEO_ACCEPT,
						},
						timeout=UPLOAD_TIMEOUT,
					)
				except requests.RequestException as e:
					raise VimeoAPIError(0, f"Vimeo tus PATCH failed: {e}") from e

				if resp.status_code not in (200, 204):
					body: Any
					try:
						body = resp.json()
					except ValueError:
						body = resp.text
					raise VimeoAPIError(resp.status_code, "tus chunk upload rejected", body=body)

				new_offset_str = resp.headers.get("Upload-Offset")
				if new_offset_str is None:
					raise VimeoAPIError(resp.status_code, "tus response missing Upload-Offset", body=None)
				new_offset = int(new_offset_str)
				if new_offset <= offset:
					raise VimeoAPIError(resp.status_code, "tus offset did not advance")
				offset = new_offset

		# Confirm the upload is complete on the tus endpoint.
		try:
			head = requests.head(
				upload_link,
				headers={"Tus-Resumable": TUS_VERSION, "Accept": VIMEO_ACCEPT},
				timeout=self.timeout,
			)
			upload_length = head.headers.get("Upload-Length")
			final_offset = head.headers.get("Upload-Offset")
			if upload_length and final_offset and upload_length != final_offset:
				raise VimeoAPIError(
					head.status_code,
					f"Upload incomplete: {final_offset}/{upload_length} bytes",
				)
		except requests.RequestException:
			# Non-fatal: fall through to GET which will reflect the final state.
			pass

		return self.get(video_uri)

	def list_folders(self, params: dict | None = None) -> dict:
		return self.get("/me/folders", params=params)

	def get_folder(self, folder_id: str) -> dict:
		return self.get(f"/me/folders/{folder_id}")

	def create_folder(self, name: str, parent_folder_uri: str | None = None) -> dict:
		payload: dict[str, Any] = {"name": name}
		if parent_folder_uri:
			payload["parent_folder_uri"] = parent_folder_uri
		return self.post("/me/folders", json=payload)

	def update_folder(
		self,
		folder_id: str,
		name: str | None = None,
		parent_folder_uri: str | None = None,
		set_parent_folder_uri: bool = False,
	) -> dict:
		payload: dict[str, Any] = {}
		if name is not None:
			payload["name"] = name
		if set_parent_folder_uri or parent_folder_uri is not None:
			payload["parent_folder_uri"] = parent_folder_uri
		try:
			return self.patch(f"/me/projects/{folder_id}", json=payload)
		except VimeoAPIError:
			return self.patch(f"/me/folders/{folder_id}", json=payload)

	def delete_folder(self, folder_id: str, delete_remote_contents: bool = False) -> dict:
		params = {"should_delete_clips": "true"} if delete_remote_contents else None
		return self.delete(f"/me/folders/{folder_id}", params=params)

	def list_folder_items(self, folder_id: str, params: dict | None = None) -> dict:
		return self.get(f"/me/projects/{folder_id}/items", params=params)

	def add_folder_items(self, folder_id: str, item_uris: list[str]) -> dict:
		return self.post(
			f"/me/projects/{folder_id}/items",
			json={"items": [{"uri": item_uri} for item_uri in item_uris]},
		)

	def remove_folder_items(self, folder_id: str, item_uris: list[str], should_delete_items: bool = False) -> dict:
		return self.delete(
			f"/me/projects/{folder_id}/items",
			params={
				"uris": ",".join(item_uris),
				"should_delete_items": "true" if should_delete_items else "false",
			},
		)


def get_client(force: bool = False) -> VimeoClient:
	"""Build a VimeoClient from the cached Vimeo Settings snapshot."""
	cache = frappe.cache()
	data = None if force else cache.get_value(CACHE_KEY)
	if not data:
		settings = frappe.get_cached_doc("Vimeo Settings", "Vimeo Settings")
		data = {
			"token": settings.get_access_token(),
			"base": settings.api_base_url or "https://api.vimeo.com",
		}
		cache.set_value(CACHE_KEY, data, expires_in_sec=CACHE_TTL_SECONDS)
	return VimeoClient(data["token"], data["base"])
