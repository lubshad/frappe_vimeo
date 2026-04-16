// Copyright (c) 2026, CoreAxis Solutions and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vimeo Settings", {
	refresh(frm) {
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
	},
});
