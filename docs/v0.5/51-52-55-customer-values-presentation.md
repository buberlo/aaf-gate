# Customer values in agent context (draft v0.5)

- Every value appears in SOUL.md as JSON on one line (`canon.render_value`). Newlines are escaped; customer values do not create headings, lists, or code blocks.
- Escaping does not make foreign text harmless for a model. It separates data from instructions in the document. Protection stays with policy floor, gate, and no send operation.
- Core floor for text: non-empty, no control/format characters except newline and tab, at most 4000 characters per text, 200 elements per list, 500 characters per element. These are assumptions, not measurements. Templates may only tighten.
- Identifiers follow a narrow pattern: `template_id` from a-z, 0-9, hyphen; field names from a-z, 0-9, underscore; up to 63 characters each.

Status: implemented and tested only on PR branch, not released for production.
