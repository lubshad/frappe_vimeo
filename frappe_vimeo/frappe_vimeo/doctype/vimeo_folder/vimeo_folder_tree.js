// Copyright (c) 2026, CoreAxis Solutions and contributors
// For license information, please see license.txt

frappe.treeview_settings["Vimeo Folder"] = {
	ignore_fields: ["parent_vimeo_folder"],
	menu_items: [
		{
			label: __("Pull from Vimeo"),
			action() {
				frappe.confirm(
					__("Import every folder from your Vimeo account that isn't already here? This runs in the background."),
					() => {
						frappe.call({
							method: "frappe_vimeo.api.pull_folders_from_vimeo",
							freeze: true,
							freeze_message: __("Queuing pull from Vimeo..."),
							callback: () => {
								frappe.show_alert({
									message: __("Vimeo folder pull started. Refresh in a moment to see new records."),
									indicator: "blue",
								});
							},
						});
					},
				);
			},
		},
	],
};
