import unittest
from types import SimpleNamespace
from unittest.mock import patch

from frappe_vimeo.api.search import search_contents


class TestSearchContents(unittest.TestCase):
	@patch("frappe_vimeo.api.search._require_manager", side_effect=PermissionError("denied"))
	@patch("frappe_vimeo.api.search.frappe")
	def test_requires_manager(self, mock_frappe, _manager) -> None:
		with self.assertRaises(PermissionError):
			search_contents("lesson")
		mock_frappe.db.sql.assert_not_called()

	@patch("frappe_vimeo.api.search._require_manager")
	@patch("frappe_vimeo.api.search._", side_effect=lambda text: text)
	@patch("frappe_vimeo.api.search.frappe")
	def test_rejects_empty_query_and_invalid_pagination(self, mock_frappe, _translate, _manager) -> None:
		mock_frappe.throw.side_effect = ValueError
		with self.assertRaises(ValueError):
			search_contents("  ")
		with self.assertRaises(ValueError):
			search_contents("lesson", limit=101)
		with self.assertRaises(ValueError):
			search_contents("lesson", offset=-1)
		mock_frappe.db.sql.assert_not_called()

	@patch("frappe_vimeo.api.search._require_manager")
	@patch("frappe_vimeo.api.search.frappe")
	def test_searches_both_kinds_with_pagination_and_app_scope(self, mock_frappe, _manager) -> None:
		mock_frappe.db.get_single_value.return_value = "Library"
		mock_frappe.db.get_value.return_value = SimpleNamespace(lft=2, rgt=30)
		row = {"kind": "video", "name": "video-1", "folder_name": "folder-2"}
		mock_frappe.db.sql.side_effect = [[(3,)], [row]]

		result = search_contents("Intro", limit=1, offset=2)

		self.assertEqual(result, {"items": [row], "total": 3})
		count_sql, count_params = mock_frappe.db.sql.call_args_list[0].args
		self.assertIn("tabVimeo Folder", count_sql)
		self.assertIn("tabVimeo Video", count_sql)
		self.assertIn("location.lft > %s", count_sql)
		self.assertEqual(count_params, ("%Intro%", 2, 30, "%Intro%", 2, 30))
		self.assertEqual(mock_frappe.db.sql.call_args_list[1].args[1], (*count_params, 1, 2))

	@patch("frappe_vimeo.api.search._require_manager")
	@patch("frappe_vimeo.api.search.frappe")
	def test_missing_app_root_returns_empty_without_querying_records(self, mock_frappe, _manager) -> None:
		mock_frappe.db.get_single_value.return_value = "Library"
		mock_frappe.db.get_value.return_value = None
		self.assertEqual(search_contents("video"), {"items": [], "total": 0})
		mock_frappe.db.sql.assert_not_called()
