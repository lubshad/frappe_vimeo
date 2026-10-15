# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from frappe_vimeo import tasks
from frappe_vimeo.frappe_vimeo.doctype.vimeo_folder.vimeo_folder import VimeoFolder
from frappe_vimeo.vimeo_client import VimeoAPIError


class TestVimeoPull(unittest.TestCase):
	def setUp(self) -> None:
		clock = patch.object(tasks.frappe.utils, "now_datetime", return_value="2026-10-15 21:00:00")
		clock.start()
		self.addCleanup(clock.stop)

	def test_imported_folders_use_remote_id_even_when_names_repeat(self) -> None:
		folder = SimpleNamespace(name=None, folder_name="PHYSICS", vimeo_id="42", flags=SimpleNamespace(from_remote_sync=True))
		VimeoFolder.autoname(folder)
		self.assertEqual(folder.name, "VIMEO-FOLDER-42")

	def test_folder_items_are_paginated(self) -> None:
		client = MagicMock()
		client.list_folder_items.side_effect = [
			{"data": [{"uri": "/videos/1"}], "paging": {"next": "/next"}},
			{"data": [{"uri": "/videos/2"}], "paging": {"next": None}},
		]
		videos = tasks._fetch_folder_items(client, "123")
		self.assertEqual([video["uri"] for video in videos], ["/videos/1", "/videos/2"])
		self.assertEqual(client.list_folder_items.call_count, 2)

	def test_folder_items_fail_instead_of_importing_a_partial_listing(self) -> None:
		client = MagicMock()
		client.list_folder_items.return_value = {"data": [], "paging": {"next": "/next"}}
		with patch.object(tasks, "PULL_MAX_PAGES", 2):
			with self.assertRaises(VimeoAPIError):
				tasks._fetch_folder_items(client, "123")

	def test_subtree_walks_children_and_retains_each_folders_videos(self) -> None:
		client = MagicMock()
		client.get_folder.return_value = {"uri": "/users/3/projects/1", "name": "MCAL"}
		client.list_folder_items.side_effect = [
			{"data": [{"type": "folder", "folder": {"uri": "/users/3/projects/2", "name": "Lessons"}}, {"type": "video", "video": {"uri": "/videos/42"}}], "paging": {}},
			{"data": [{"type": "folder", "folder": {"uri": "/users/3/projects/3", "name": "Unit 1"}}, {"type": "video", "video": {"uri": "/videos/43"}}], "paging": {}},
			{"data": [{"type": "video", "video": {"uri": "/videos/44"}}], "paging": {}},
		]
		db = MagicMock()
		db.get_single_value.side_effect = lambda _, field: {
			"app_folder_vimeo_id": "1",
			"app_folder_vimeo_uri": "/users/3/projects/1",
			"app_folder_name": "MCAL",
		}[field]
		with patch.object(tasks.frappe, "db", db):
			folders, videos = tasks._fetch_app_subtree(client)
		self.assertEqual([folder["id"] for folder in folders], ["1", "2", "3"])
		self.assertEqual(folders[2]["parent_uri"], folders[1]["uri"])
		self.assertEqual([item["uri"] for item in videos["1"]], ["/videos/42"])
		self.assertEqual([item["uri"] for item in videos["3"]], ["/videos/44"])
		self.assertEqual(client.list_folder_items.call_count, 3)

	def test_folder_pull_inserts_child_under_existing_app_folder(self) -> None:
		root = {"id": "1", "uri": "/users/3/projects/1", "name": "MCAL", "parent_uri": None}
		child = {"id": "2", "uri": "/users/3/projects/2", "name": "Lessons", "parent_uri": root["uri"]}
		parent_doc = MagicMock(is_group=0)
		new_doc = MagicMock()
		db = MagicMock()
		db.get_single_value.return_value = "1"
		db.get_value.side_effect = lambda _, filters, field: "MCAL" if filters.get("vimeo_id") == "1" or filters.get("vimeo_uri") else None
		with (
			patch.object(tasks.frappe, "db", db),
			patch.object(tasks.frappe, "get_doc", return_value=parent_doc),
			patch.object(tasks.frappe, "new_doc", return_value=new_doc),
			patch.object(tasks, "_apply_folder_to_doc"),
		):
			result = tasks.pull_folders_from_vimeo(([root, child], {"1": [], "2": []}))
		self.assertEqual(result, {"created": 1, "skipped": 1, "errors": 0})
		self.assertEqual(parent_doc.is_group, 1)
		self.assertTrue(parent_doc.flags.from_remote_sync)
		self.assertEqual(new_doc.parent_vimeo_folder, "MCAL")
		new_doc.insert.assert_called_once_with(ignore_permissions=True)

	def test_video_pull_links_existing_video_from_folder_items(self) -> None:
		folder = MagicMock()
		folder.get.return_value = []
		video = MagicMock(video_title="Lesson", privacy=None)
		db = MagicMock()
		db.get_single_value.return_value = "1"
		db.get_value.return_value = "VIMEO-0042"
		with (
			patch.object(tasks.frappe, "db", db),
			patch.object(tasks, "get_client") as get_client,
			patch.object(tasks.frappe, "get_all", return_value=[SimpleNamespace(name="MCAL", vimeo_id="1")]),
			patch.object(tasks.frappe, "get_doc", side_effect=lambda doctype, name: folder if doctype == "Vimeo Folder" else video),
		):
			result = tasks.pull_videos_from_vimeo(
				([{"id": "1"}], {"1": [{"uri": "/videos/42", "name": "Lesson"}]})
			)
		self.assertEqual(result, {"created": 0, "skipped": 1, "errors": 0})
		folder.append.assert_called_once_with("videos", {"video": "VIMEO-0042"})
		folder.save.assert_called_once_with(ignore_permissions=True)
		get_client.return_value.add_folder_items.assert_not_called()
		get_client.return_value.remove_folder_items.assert_not_called()

	def test_import_updates_existing_folder_without_outbound_calls(self) -> None:
		folder = MagicMock()
		db = MagicMock()
		db.get_single_value.return_value = "1"
		db.get_value.return_value = "VIMEO-FOLDER-1"
		remote = {"id": "1", "uri": "/users/3/projects/1", "name": "Original Vimeo title"}
		with (
			patch.object(tasks.frappe, "db", db),
			patch.object(tasks.frappe, "get_doc", return_value=folder),
			patch.object(tasks.frappe, "enqueue") as enqueue,
			patch.object(tasks, "get_client") as client,
		):
			result = tasks.pull_folders_from_vimeo(([remote], {"1": []}))
		self.assertEqual(result, {"created": 0, "skipped": 1, "errors": 0})
		self.assertEqual(folder.folder_name, "Original Vimeo title")
		self.assertTrue(folder.flags.from_remote_sync)
		folder.save.assert_called_once_with(ignore_permissions=True)
		folder.db_set.assert_called_with("last_synced_on", folder.last_synced_on, update_modified=False)
		enqueue.assert_not_called()
		client.assert_not_called()

	def test_stale_folder_push_does_not_write_to_vimeo(self) -> None:
		with (
			patch.object(tasks.frappe, "db", MagicMock()) as db,
			patch.object(tasks.frappe, "get_doc"),
			patch.object(tasks, "_folder_needs_metadata_sync", return_value=False),
			patch.object(tasks, "get_client") as client,
		):
			db.exists.return_value = True
			tasks.sync_vimeo_folder("folder-1")
		client.assert_not_called()

	def test_stale_video_push_does_not_write_to_vimeo(self) -> None:
		with (
			patch.object(tasks.frappe, "db", MagicMock()) as db,
			patch.object(tasks.frappe, "get_doc"),
			patch.object(tasks, "_video_needs_metadata_sync", return_value=False),
			patch.object(tasks, "get_client") as client,
		):
			db.exists.return_value = True
			tasks.sync_video_metadata("video-1")
		client.assert_not_called()
