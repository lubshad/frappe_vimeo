# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt

from __future__ import annotations

import os

import frappe
from frappe.modules.import_file import import_file_by_path


def execute() -> None:
	# Force-reimport the Vimeo Workspace Sidebar JSON so existing sites pick up
	# the newly added "Vimeo Folder" link even when the DB record has a newer
	# modified timestamp than the fixture.
	app_path = frappe.get_app_path("frappe_vimeo")
	json_path = os.path.join(app_path, "workspace_sidebar", "vimeo.json")
	if os.path.exists(json_path):
		import_file_by_path(json_path, force=True, ignore_version=True)
