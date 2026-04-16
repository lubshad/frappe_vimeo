// Copyright (c) 2026, CoreAxis Solutions and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vimeo Video", {
	refresh(frm) {
		if (frm.is_new()) {
			frm.set_intro(
				__("To upload: Fill in details, save, and then click 'Upload from Computer'. This will upload directly to Vimeo without saving in Frappe."),
				"blue",
			);
			return;
		}

		const status = (frm.doc.status || "").toLowerCase();
		const is_ready = frm.doc.vimeo_url || status === "available" || status === "transcoding" || status === "complete";

		if (is_ready) {
			if (frm.doc.vimeo_url) {
				frm.add_custom_button(__("Open on Vimeo"), () => {
					window.open(frm.doc.vimeo_url, "_blank");
				}, __("Actions"));
			}
			frm.add_custom_button(__("Sync from Vimeo"), () => syncFromVimeo(frm), __("Actions"));
		} else {
			const label = frm.doc.vimeo_id ? __("Resume Upload") : __("Upload from Computer");
			frm.add_custom_button(label, () => uploadToVimeo(frm), __("Actions"));
		}
		
		// Listen for background sync updates
		frappe.realtime.on("vimeo_video_synced", (data) => {
			if (data.video_name === frm.doc.name) {
				frm.reload_doc();
			}
		});
	},
});

function syncFromVimeo(frm) {
	frappe.call({
		method: "frappe_vimeo.api.sync_video_record",
		args: {
			name: frm.doc.name,
		},
		freeze: true,
		freeze_message: __("Syncing from Vimeo..."),
		callback: () => {
			frappe.show_alert({
				message: __("Video synced from Vimeo"),
				indicator: "green",
			});
			frm.reload_doc();
		},
	});
}

function uploadToVimeo(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Please save the record first."));
		return;
	}

	const input = $('<input type="file" accept="video/*">');
	input.on("change", (e) => {
		const file = e.target.files[0];
		if (!file) return;

		// We need tus-js-client for chunked uploads
		frappe.require("https://cdn.jsdelivr.net/npm/tus-js-client@latest/dist/tus.min.js").then(() => {
			if (typeof tus === "undefined") {
				frappe.msgprint(__("Could not load upload library. Please check your internet connection."));
				return;
			}
			startTusUpload(frm, file);
		});
	});
	input.click();
}

function startTusUpload(frm, file) {
	const dialog = new frappe.ui.Dialog({
		title: __("Uploading Video: {0}", [file.name]),
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "progress_html",
				options: `
					<div class="progress">
						<div class="progress-bar" role="progressbar" style="width: 0%;" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100">0%</div>
					</div>
					<div class="margin-top text-muted small status-text">${__("Requesting upload ticket...")}</div>
				`
			}
		],
		primary_action_label: __("Cancel"),
		primary_action: () => {
			if (frm.tus_upload) {
				frm.tus_upload.abort();
			}
			dialog.hide();
		}
	});

	dialog.show();

	const updateProgress = (percentage, statusText) => {
		const $bar = dialog.$wrapper.find(".progress-bar");
		percentage = Math.round(percentage);
		$bar.css("width", percentage + "%").text(percentage + "%").attr("aria-valuenow", percentage);
		if (statusText) {
			dialog.$wrapper.find(".status-text").text(statusText);
		}
	};

	frappe.call({
		method: "frappe_vimeo.api.get_vimeo_upload_ticket",
		args: {
			name: frm.doc.name,
			video_title: frm.doc.video_title,
			description: frm.doc.description,
			privacy: frm.doc.privacy,
			size: file.size
		},
		callback: (r) => {
			if (r.exc) {
				dialog.hide();
				return;
			}

			const ticket = r.message;
			const initialText = ticket.resuming ? __("Resuming upload to Vimeo...") : __("Uploading chunks to Vimeo...");
			updateProgress(0, initialText);

			const upload = new tus.Upload(file, {
				endpoint: "https://vimeo.com/upload",
				uploadUrl: ticket.upload_link,
				retryDelays: [0, 1000, 3000, 5000],
				metadata: {
					filename: file.name,
					filetype: file.type
				},
				onError: (error) => {
					console.error("Vimeo Upload Error:", error);
					frappe.msgprint(__("Upload failed: {0}", [error]));
					dialog.hide();
				},
				onProgress: (bytesUploaded, bytesTotal) => {
					const percentage = (bytesUploaded / bytesTotal) * 100;
					updateProgress(percentage);
				},
				onSuccess: () => {
					updateProgress(100, __("Finalizing upload..."));
					frappe.call({
						method: "frappe_vimeo.api.finalize_vimeo_upload",
						args: {
							name: frm.doc.name,
							vimeo_id: ticket.vimeo_id
						},
						callback: () => {
							dialog.hide();
							frappe.show_alert({
								message: __("Video uploaded successfully! Vimeo is now processing it."),
								indicator: "green"
							});
							frm.reload_doc();
						}
					});
				}
			});

			frm.tus_upload = upload;

			// Force a check and log before starting to ensure resuming works
			upload.findPreviousUploads().then(function (previousUploads) {
				if (ticket.resuming) {
					console.log("Resuming upload for:", file.name, "at URL:", ticket.upload_link);
				}
				upload.start();
			});
		}
	});
}
