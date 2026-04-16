// Copyright (c) 2026, CoreAxis Solutions and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vimeo Folder", {
	refresh(frm) {
		frm.add_custom_button(__("Queue Vimeo Sync"), () => queueSync(frm), __("Actions"));
	},
});

function queueSync(frm) {
	frappe.call({
		method: "frappe_vimeo.api.sync_folder_record",
		args: {
			name: frm.doc.name,
		},
		freeze: true,
		freeze_message: __("Queueing folder sync..."),
		callback: () => {
			frappe.show_alert({
				message: __("Folder sync queued"),
				indicator: "green",
			});
			frm.reload_doc();
		},
	});
}
