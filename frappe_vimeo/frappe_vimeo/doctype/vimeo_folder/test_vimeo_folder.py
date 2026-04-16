# Copyright (c) 2026, CoreAxis Solutions and contributors
# See license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from frappe_vimeo.api._utils import (
	_apply_folder_to_doc,
	_get_membership_deltas,
	_normalize_folder,
	_serialize_folder_doc,
)
from frappe_vimeo.frappe_vimeo.doctype.vimeo_folder.vimeo_folder import MAX_FOLDER_DEPTH, VimeoFolder


class DummyFolderDoc:
	def __init__(self) -> None:
		self.name = "Root Folder"
		self.owner = "Administrator"
		self.creation = "2026-04-16 12:30:00"
		self.modified = "2026-04-16 12:30:00"
		self.last_synced_on = None
		self.folder_name = "Root Folder"
		self.parent_vimeo_folder = None
		self.vimeo_id = None
		self.vimeo_uri = None
		self.sync_status = "queued"
		self.sync_error = None
		self.videos = []

	def get(self, fieldname: str):
		return getattr(self, fieldname)


class DummyMembershipRow:
	def __init__(self, video: str) -> None:
		self.video = video


class TestVimeoFolderHelpers(unittest.TestCase):
	def test_apply_folder_to_doc_maps_fields(self) -> None:
		doc = DummyFolderDoc()
		normalized = {
			"id": "123",
			"uri": "/users/1/projects/123",
			"name": "Marketing",
		}

		with patch("frappe_vimeo.api._utils.frappe.utils.now_datetime", return_value="2026-04-16 12:45:00"):
			_apply_folder_to_doc(doc, normalized)

		self.assertEqual(doc.vimeo_id, "123")
		self.assertEqual(doc.vimeo_uri, "/users/1/projects/123")
		self.assertEqual(doc.folder_name, "Marketing")
		self.assertEqual(doc.sync_status, "synced")

	def test_normalize_folder_extracts_id(self) -> None:
		out = _normalize_folder({"uri": "/users/1/projects/987", "name": "Child"})
		self.assertEqual(out["id"], "987")
		self.assertEqual(out["name"], "Child")

	def test_serialize_folder_doc_returns_expected_shape(self) -> None:
		doc = DummyFolderDoc()
		doc.vimeo_id = "123"
		payload = _serialize_folder_doc(doc)
		self.assertEqual(payload["name"], "Root Folder")
		self.assertEqual(payload["folder_name"], "Root Folder")
		self.assertEqual(payload["vimeo_id"], "123")

	def test_get_membership_deltas_returns_added_and_removed(self) -> None:
		before = DummyFolderDoc()
		before.videos = [DummyMembershipRow("VID-1"), DummyMembershipRow("VID-2")]
		after = DummyFolderDoc()
		after.videos = [DummyMembershipRow("VID-2"), DummyMembershipRow("VID-3")]

		added, removed = _get_membership_deltas(before, after)

		self.assertEqual(added, ["VID-3"])
		self.assertEqual(removed, ["VID-1"])


class TestVimeoFolderValidation(unittest.TestCase):
	@patch("frappe_vimeo.frappe_vimeo.doctype.vimeo_folder.vimeo_folder.frappe")
	@patch("frappe_vimeo.frappe_vimeo.doctype.vimeo_folder.vimeo_folder._require_manager")
	def test_validate_depth_rejects_more_than_ten_levels(self, _require_manager, mock_frappe) -> None:
		chain = [f"Folder {index}" for index in range(MAX_FOLDER_DEPTH)]

		def fake_get_value(_doctype: str, name: str, fieldname: str):
			if fieldname == "parent_vimeo_folder":
				try:
					index = chain.index(name)
				except ValueError:
					return None
				return chain[index + 1] if index + 1 < len(chain) else None
			return "/videos/123"

		mock_frappe.db.get_value.side_effect = fake_get_value
		mock_frappe.throw.side_effect = Exception("depth exceeded")
		doc = VimeoFolder.__new__(VimeoFolder)
		doc.name = "Too Deep"
		doc.flags = SimpleNamespace()
		doc.folder_name = "Too Deep"
		doc.parent_vimeo_folder = chain[0]
		doc.videos = []
		doc.get = lambda fieldname: getattr(doc, fieldname)

		with self.assertRaises(Exception):
			doc.validate()
