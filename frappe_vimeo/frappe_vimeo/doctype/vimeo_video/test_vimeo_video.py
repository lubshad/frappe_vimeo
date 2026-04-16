# Copyright (c) 2026, CoreAxis Solutions and contributors
# See license.txt

import unittest
from unittest.mock import patch

from frappe_vimeo.api._utils import (
	_apply_video_to_doc,
	_normalize_video,
	_serialize_video_doc,
	_video_needs_metadata_sync,
)


class DummyVideoDoc:
	def __init__(self) -> None:
		self.name = "VIMEO-00001"
		self.owner = "Administrator"
		self.creation = "2026-04-16 12:30:00"
		self.modified = "2026-04-16 12:30:00"
		self.last_synced_on = None
		self.vimeo_id = None
		self.vimeo_uri = None
		self.video_title = None
		self.description = None
		self.duration = None
		self.created_time = None
		self.modified_time = None
		self.privacy = None
		self.vimeo_url = None
		self.player_embed_url = None
		self.thumbnail_url = None
		self.thumbnail_image = None
		self.status = None
		self.transcode_status = None


class TestVimeoVideoHelpers(unittest.TestCase):
	def test_apply_video_to_doc_maps_normalized_fields(self) -> None:
		doc = DummyVideoDoc()
		normalized = {
			"id": "1234567890",
			"uri": "/videos/1234567890",
			"name": "Sample",
			"description": "hello",
			"duration": 42,
			"created_time": "2026-04-16T10:00:00+00:00",
			"modified_time": "2026-04-16T10:05:00+00:00",
			"privacy": "unlisted",
			"link": "https://vimeo.com/1234567890",
			"player_embed_url": "https://player.vimeo.com/video/1234567890?h=abc",
			"thumbnail": "https://image.example/thumb.jpg",
			"status": "available",
			"transcode_status": "complete",
		}

		with patch("frappe_vimeo.api._utils.frappe.utils.now_datetime", return_value="2026-04-16 12:45:00"):
			_apply_video_to_doc(doc, normalized)

		self.assertEqual(doc.vimeo_id, "1234567890")
		self.assertEqual(doc.video_title, "Sample")
		self.assertEqual(doc.thumbnail_url, "https://image.example/thumb.jpg")
		self.assertEqual(doc.transcode_status, "complete")
		self.assertIsNotNone(doc.last_synced_on)

	def test_serialize_video_doc_returns_public_shape(self) -> None:
		doc = DummyVideoDoc()
		doc.vimeo_id = "123"
		doc.video_title = "Demo"
		doc.vimeo_url = "https://vimeo.com/123"

		payload = _serialize_video_doc(doc)

		self.assertEqual(payload["name"], "VIMEO-00001")
		self.assertEqual(payload["vimeo_id"], "123")
		self.assertEqual(payload["video_title"], "Demo")
		self.assertEqual(payload["vimeo_url"], "https://vimeo.com/123")

	def test_normalize_video_formats_datetimes_for_frappe(self) -> None:
		out = _normalize_video(
			{
				"uri": "/videos/123",
				"privacy": {"view": "unlisted"},
				"created_time": "2026-04-16T06:21:45+00:00",
				"modified_time": "2026-04-16T06:31:45+00:00",
			}
		)

		self.assertEqual(out["created_time"], "2026-04-16 06:21:45")
		self.assertEqual(out["modified_time"], "2026-04-16 06:31:45")

	def test_video_needs_metadata_sync_when_modified_after_last_sync(self) -> None:
		doc = DummyVideoDoc()
		doc.vimeo_id = "123"
		doc.modified = "2026-04-16 12:40:00"
		doc.last_synced_on = "2026-04-16 12:30:00"

		self.assertTrue(_video_needs_metadata_sync(doc))

	def test_video_does_not_need_metadata_sync_without_vimeo_id(self) -> None:
		doc = DummyVideoDoc()
		doc.modified = "2026-04-16 12:40:00"
		doc.last_synced_on = "2026-04-16 12:30:00"

		self.assertFalse(_video_needs_metadata_sync(doc))
