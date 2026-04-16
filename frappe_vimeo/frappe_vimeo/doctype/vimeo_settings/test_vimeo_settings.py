# Copyright (c) 2026, CoreAxis Solutions and contributors
# See license.txt

import unittest

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
