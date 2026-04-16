# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe_vimeo.patches.v1_0.create_vimeo_manager_role import execute as create_vimeo_manager_role


def after_install() -> None:
	create_vimeo_manager_role()
