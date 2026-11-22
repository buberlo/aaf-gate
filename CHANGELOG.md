# CHANGELOG

## English-only documentation migration

**Breaking changes for documentation consumers:** Markdown paths and filenames are English (`EVIDENCE-I1.md` replaces `NACHWEIS-I1.md`, `docs/v1/INSTALLATION-AND-OPERATIONS.md` replaces `INSTALLATION-UND-BETRIEB.md`, v0.4/v0.5 doc slugs renamed, template directory ids and profile client subdirectory `client/` replace German names). Update bookmarks and internal links.

**Breaking changes for integrators:** Template ids renamed (`auto-service-appointment-intake`, `b2b-lead-research`, `b2b-lead-research-client`, `ecommerce-product-sales`); `templates/HASHES.json` body hashes regenerated for enum/value edits. I1 prototype run states and block/resume reason codes are English (`CAPTURED`, `VALIDATED`, `DRAFT`, `APPROVED`, `STORED`, and reason codes such as `manually_blocked`). Example Hermes customer profile slug in device tests is `client` (was `kunde`). CLI error messages and Hermes plugin responses are English.

## 1.0.0rc1 (v1 candidate, not released for production)

- Kill switch: core 41575e0, CLI gate-principal-add/gate-stop/gate-resume/gate-status 17831ee, device checklist and docs 5484531.
- Call-limit gate per time window and class: 58dccbe. Start values as an **Assumption** in the Pilot 1 policy (window 3600 s, read 160, draft 40, internal_write 80; **reset** after a **pilot week** from `gate_calls`); this commit.
- Device acceptance pack: probe script and verify-hermes-probe 8c8da50, ACCEPTANCE-RECORD.md c30faf0.
- Backup and restore with audit hash-chain verification: gate-backup/gate-restore 813fe28, backup probe and rebuild guide 575bef9.
- Version and release materials: 79dd30b; acceptance record with core_version, [docs/v1/STATUS.md](docs/v1/STATUS.md) and [docs/v1/INSTALLATION-AND-OPERATIONS.md](docs/v1/INSTALLATION-AND-OPERATIONS.md): this commit.

Not included / open: acceptance on Mac mini, Google **Developer Preview** approval for **Gmail** MCP, legal and organizational prerequisites before real customer data, no sending, no Computer Use. No compliance commitments, no prices.