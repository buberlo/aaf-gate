# Template registration against HASHES.json (draft v0.5, step 95)

`ConfigPlane(..., expected=...)` takes the map from `templates/HASHES.json`. When `expected` is set (customer/appliance mode), `register_template` after validation and hash build but before storage checks whether the version is listed and hash matches (`TEMPLATE_UNLISTED`, `TEMPLATE_HASH_MISMATCH`). Rejections are audited like `INVALID_CONFIG`. `expected=None` keeps prior behavior for provider development.

At this step, callers still set the mode. [Step 96](96-device-mode.md) added loading the mode from device configuration.

Status: implemented and tested on PR branch, not released for production.
