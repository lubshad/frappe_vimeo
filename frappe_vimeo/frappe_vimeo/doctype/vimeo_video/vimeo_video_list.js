// Copyright (c) 2026, CoreAxis Solutions and contributors
// For license information, please see license.txt

frappe.listview_settings["Vimeo Video"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Pull from Vimeo"), () => {
			frappe.confirm(
				__("Import every video from your Vimeo account that isn't already here? This runs in the background."),
				() => {
					frappe.call({
						method: "frappe_vimeo.api.pull_videos_from_vimeo",
						freeze: true,
						freeze_message: __("Queuing pull from Vimeo..."),
						callback: () => {
							frappe.show_alert({
								message: __("Vimeo video pull started. Refresh in a moment to see new records."),
								indicator: "blue",
							});
						},
					});
				},
			);
		});
	},
};
