# Package ships device mode (draft v0.5, step 97)

`build_appliance_package(..., expected=...)` writes when hash list is set `device/device-mode.json` and `device/HASHES.json` into the package. `provenance.json` carries `device_mode` (`appliance` or `unlisted`) and `hashes_file_hash` (hash of list or `null`). On device, `ConfigPlane.for_device` can use delivered files after permission fix.

Open: installation sets secure file permissions and owner (point 22); list still unsigned (point 23).

Status: implemented and tested on PR branch, not released for production.
