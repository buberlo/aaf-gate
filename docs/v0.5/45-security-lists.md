# Security lists with minimum content (draft v0.5)

- Templates may declare array fields with `contains` (required elements), `unique`, and `item_type: domain`.
- The author may add required elements but not remove them. Removal requires a new template version. Weaker than `fixed` and allows project-specific additions.
- Domains are not normalized; invalid format is rejected so stored value matches input.
- `b2b-lead-research` 0.3.0 requires `linkedin.com` in `blocked_domains`. `ecommerce-product-sales` 0.3.0 requires a non-empty domain list without fixed required entries (open decision).
- Lists have no runtime effect in core yet. They apply via package build (Hermes website_blocklist) and connector gate.

Status: implemented and tested only on PR branch, not released for production.
