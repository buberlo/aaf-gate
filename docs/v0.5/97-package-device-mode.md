# Package ships device mode (draft v0.5, step 97)

`build_appliance_package(..., expected=...)` writes when hash list is set `device/device-mode.json` and `device/HASHES.json` into the package. `provenance.json` carries `device_mode` (`appliance` or `unlisted`) and `hashes_file_hash` (hash of list or `null`). On device, `ConfigPlane.for_device` can use delivered files after permission fix.

Secure permissions and ownership at installation (point 22) and signing the list (point 23) were open at this step.

At this step, the implementation was tested locally on the PR branch. See the [candidate status](../v1/STATUS.md) for release and device acceptance.
