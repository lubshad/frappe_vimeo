# Copyright (c) 2026, CoreAxis Solutions and contributors
# For license information, please see license.txt
"""Public API package for frappe_vimeo.

Re-exports every whitelisted function so callers can keep using
`frappe_vimeo.api.<fn>` dotted paths without caring which submodule
a function lives in.
"""

from frappe_vimeo.api.videos import (
	delete_video_record,
	finalize_vimeo_upload,
	get_upload_chunk_size_bytes,
	get_video_record,
	get_vimeo_upload_ticket,
	pull_videos_from_vimeo,
	sync_video_record,
	update_video_record,
	upload_video_thumbnail,
)
from frappe_vimeo.api.folders import (
	create_folder_record,
	delete_folder_record,
	get_folder_record,
	list_folder_records,
	pull_folders_from_vimeo,
	sync_folder_record,
	update_folder_record,
)

__all__ = [
	"create_folder_record",
	"delete_folder_record",
	"delete_video_record",
	"finalize_vimeo_upload",
	"get_folder_record",
	"get_upload_chunk_size_bytes",
	"get_video_record",
	"get_vimeo_upload_ticket",
	"list_folder_records",
	"pull_folders_from_vimeo",
	"pull_videos_from_vimeo",
	"sync_folder_record",
	"sync_video_record",
	"update_folder_record",
	"update_video_record",
	"upload_video_thumbnail",
]
