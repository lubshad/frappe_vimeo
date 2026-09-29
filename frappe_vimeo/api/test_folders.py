import unittest
from types import SimpleNamespace
from unittest.mock import patch

from frappe_vimeo.api.folders import get_folder_contents, list_folder_records


def _folder(name: str, parent: str | None = None) -> SimpleNamespace:
	return SimpleNamespace(
		name=name,
		folder_name=name,
		parent_vimeo_folder=parent,
		vimeo_id=None,
		vimeo_uri=None,
		sync_status="synced",
		sync_error=None,
		last_synced_on=None,
		owner="Administrator",
		creation="2026-01-01",
		modified="2026-01-01",
	)


class TestGetFolderContents(unittest.TestCase):
	@patch("frappe_vimeo.api.folders._require_manager")
	@patch("frappe_vimeo.api.folders.frappe")
	@patch("frappe_vimeo.api._utils._serialize_folder_videos", return_value=[{"name": "video-1"}])
	def test_returns_synced_children_videos_and_breadcrumb(self, _videos, mock_frappe, _manager) -> None:
		folders = {
			"root": _folder("root"),
			"parent": _folder("parent", "root"),
			"leaf": _folder("leaf", "parent"),
		}
		mock_frappe.get_doc.side_effect = lambda _doctype, name: folders[name]
		mock_frappe.get_all.return_value = [_folder("child", "leaf")]

		result = get_folder_contents("leaf")

		self.assertEqual(result["folder"]["name"], "leaf")
		self.assertEqual([child["name"] for child in result["children"]], ["child"])
		self.assertEqual(result["videos"], [{"name": "video-1"}])
		self.assertEqual([folder["name"] for folder in result["breadcrumb"]], ["root", "parent", "leaf"])
		mock_frappe.get_all.assert_called_once()
		self.assertEqual(mock_frappe.get_all.call_args.kwargs["filters"], {"parent_vimeo_folder": "leaf"})

	@patch("frappe_vimeo.api.folders._require_manager", side_effect=PermissionError("denied"))
	@patch("frappe_vimeo.api.folders.frappe")
	def test_requires_manager(self, mock_frappe, _manager) -> None:
		with self.assertRaises(PermissionError):
			get_folder_contents("leaf")
		mock_frappe.get_doc.assert_not_called()

	@patch("frappe_vimeo.api.folders._require_manager")
	@patch("frappe_vimeo.api.folders.frappe")
	def test_list_folders_without_videos_uses_one_query(self, mock_frappe, _manager) -> None:
		mock_frappe.get_all.return_value = [_folder("child", "parent")]

		result = list_folder_records(parent_name="parent")

		self.assertEqual([folder["name"] for folder in result], ["child"])
		mock_frappe.get_doc.assert_not_called()
		self.assertEqual(mock_frappe.get_all.call_args.kwargs["filters"], {"parent_vimeo_folder": "parent"})

	@patch("frappe_vimeo.api.folders._require_manager")
	@patch("frappe_vimeo.api.folders._", side_effect=lambda message: message)
	@patch("frappe_vimeo.api.folders.frappe")
	def test_requires_name(self, mock_frappe, _translate, _manager) -> None:
		mock_frappe.throw.side_effect = ValueError("name is required")
		with self.assertRaisesRegex(ValueError, "name is required"):
			get_folder_contents("")
		mock_frappe.get_doc.assert_not_called()
