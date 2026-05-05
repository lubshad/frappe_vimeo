// Copyright (c) 2026, CoreAxis Solutions and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vimeo Settings", {
	refresh(frm) {
		frm.set_df_property("app_folder_name", "read_only", frm.doc.app_folder_vimeo_id ? 1 : 0);

		frm.add_custom_button(__("Test Connection"), () => {
			frm.call("test_connection").then((r) => {
				if (r && r.message && r.message.ok) {
					frappe.show_alert({
						message: __("Connected to Vimeo as {0}", [r.message.user]),
						indicator: "green",
					});
					frm.reload_doc();
				}
			});
		});

		frm.add_custom_button(__("Sync from Vimeo"), () => {
			frappe.confirm(
				__("This will sync all folders and videos from Vimeo in the background. Continue?"),
				() => {
					frm.call("sync_from_vimeo").then((r) => {
						if (r && r.message && r.message.status === "queued") {
							frappe.show_alert({
								message: __("Background sync started."),
								indicator: "blue",
							});
						}
					});
				},
			);
		});
	},
});
