# Installation verification (draft v0.5, step 98)

`installcheck.verify_installation` compares an installed directory with the built appliance package: missing or changed files, unexpected files in documented top-level areas (`device/`, `distribution/`, `managed/`), optional `package_hash` check, and in appliance mode fail-closed check of delivered `device-mode.json`. Empty result list means file check passed.

Limits: no check of owners, accounts, or Hermes runtime (point 22); trust depends on executing account.

Status: implemented and tested on PR branch, not yet tried on device, not released for production.
