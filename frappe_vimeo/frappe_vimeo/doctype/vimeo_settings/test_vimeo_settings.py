# Copyright (c) 2026, CoreAxis Solutions and contributors
# See license.txt

import unittest
from unittest.mock import patch

import frappe
from frappe_vimeo.api._utils import _normalize_video, _pick_largest_thumbnail


class TestVimeoNormalization(unittest.TestCase):
	def test_normalize_video_extracts_id_and_fields(self) -> None:
		raw = {
			"uri": "/videos/1234567890",
			"name": "Sample",
			"description": "hi",
			"duration": 42,
			"created_time": "2026-04-16T10:00:00+00:00",
			"modified_time": "2026-04-16T10:05:00+00:00",
			"privacy": {"view": "unlisted"},
			"link": "https://vimeo.com/1234567890",
			"player_embed_url": "https://player.vimeo.com/video/1234567890?h=abc",
			"pictures": {
				"sizes": [
					{"width": 295, "height": 166, "link": "small.jpg"},
					{"width": 1920, "height": 1080, "link": "big.jpg"},
				]
			},
			"download": [
				{"quality": "hd", "link": "dl.mp4", "size": 1000},
			],
			"status": "available",
			"transcode": {"status": "complete"},
		}
		out = _normalize_video(raw)
		self.assertEqual(out["id"], "1234567890")
		self.assertEqual(out["privacy"], "unlisted")
		self.assertEqual(out["thumbnail"], "big.jpg")
		self.assertEqual(out["transcode_status"], "complete")
		self.assertEqual(out["downloads"][0]["quality"], "hd")

	def test_normalize_video_empty(self) -> None:
		self.assertEqual(_normalize_video({}), {})
		self.assertIsNone(_pick_largest_thumbnail(None))
		self.assertIsNone(_pick_largest_thumbnail([]))


class TestVimeoSettings(unittest.TestCase):
	def test_save_does_not_connect_before_token_is_stored(self) -> None:
		settings = frappe.get_doc({"doctype": "Vimeo Settings", "app_folder_name": "MCAL"})
		settings.upload_chunk_size_mb = None
		with patch("frappe_vimeo.vimeo_client.get_client") as get_client:
			settings.validate()
		get_client.assert_not_called()
		self.assertEqual(settings.upload_chunk_size_mb, 1)

	def test_connection_creates_folder_after_settings_are_saved(self) -> None:
		settings = frappe.get_doc({"doctype": "Vimeo Settings", "app_folder_name": "MCAL"})
		with (
			patch("frappe_vimeo.vimeo_client.get_client") as get_client,
			patch.object(settings, "_set_app_folder_from_remote") as set_folder,
			patch.object(settings, "db_set") as db_set,
		):
			get_client.return_value.get.return_value = {"name": "Vimeo User", "uri": "/users/1"}
			result = settings.test_connection()
		get_client.assert_called_once_with(force=True)
		set_folder.assert_called_once_with(get_client.return_value, create_if_missing=True)
		self.assertTrue(db_set.called)
		self.assertTrue(result["ok"])

	def test_sync_requires_manager(self) -> None:
		settings = frappe.get_doc({"doctype": "Vimeo Settings", "app_folder_vimeo_id": "123"})
		with (
			patch("frappe_vimeo.frappe_vimeo.doctype.vimeo_settings.vimeo_settings._require_manager", side_effect=frappe.PermissionError),
			patch("frappe_vimeo.frappe_vimeo.doctype.vimeo_settings.vimeo_settings.frappe.enqueue") as enqueue,
		):
			with self.assertRaises(frappe.PermissionError):
				settings.sync_from_vimeo()
		enqueue.assert_not_called()

	def test_sync_queues_one_job_after_commit(self) -> None:
		settings = frappe.get_doc({"doctype": "Vimeo Settings", "app_folder_vimeo_id": "123"})
		with (
			patch("frappe_vimeo.frappe_vimeo.doctype.vimeo_settings.vimeo_settings._require_manager"),
			patch.object(settings, "get_access_token", return_value="token"),
			patch.object(settings, "db_set"),
			patch("frappe_vimeo.frappe_vimeo.doctype.vimeo_settings.vimeo_settings.frappe.enqueue") as enqueue,
		):
			self.assertEqual(settings.sync_from_vimeo(), {"status": "queued"})
		enqueue.assert_called_once_with(
			"frappe_vimeo.tasks.pull_from_vimeo",
			queue="long",
			timeout=3600,
			enqueue_after_commit=True,
		)
