# Device mode fail-closed (draft v0.5, step 96)

`device-mode.json` sets `appliance` or `development`. `ConfigPlane.for_device(store, mode_path)` loads the file via `devicemode.load_mode`: broken or missing config throws `DEVICE_MODE_INVALID` (no silent fallback). In appliance mode, `expected` loads from referenced `HASHES.json`; group/other writability of mode and hash files is rejected. Development mode sets `expected=None`.

The owner/account check (point 22) was open at this step.

[Step 97](97-package-device-mode.md) added the package mode to `provenance.json`.

At this step, the implementation was tested locally on the PR branch. See the [candidate status](../v1/STATUS.md) for release and device acceptance.
