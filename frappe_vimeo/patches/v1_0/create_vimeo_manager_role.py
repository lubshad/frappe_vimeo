# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe


def execute() -> None:
	# Ensure the app permission model has a dedicated Desk role.
	if frappe.db.exists("Role", "Vimeo Manager"):
		return

	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": "Vimeo Manager",
		}
	).insert(ignore_permissions=True)
