# Device mode fail-closed (draft v0.5, step 96)

`device-mode.json` sets `appliance` or `development`. `ConfigPlane.for_device(store, mode_path)` loads the file via `devicemode.load_mode`: broken or missing config throws `DEVICE_MODE_INVALID` (no silent fallback). In appliance mode, `expected` loads from referenced `HASHES.json`; group/other writability of mode and hash files is rejected. Development mode sets `expected=None`.

Open: owner/account check (point 22), binding mode in `provenance.json` (point 97).

Status: implemented and tested on PR branch, not released for production.
