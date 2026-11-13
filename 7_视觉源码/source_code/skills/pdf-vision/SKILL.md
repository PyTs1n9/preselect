# PDF Vision Skill

## Purpose

Convert PDF pages into traceable structured data with a vision model, then validate
and repair the result without losing source evidence.

## Required flow

1. Read PDF metadata and renderable page references.
2. Send page images and extraction instructions to the configured vision provider.
3. Normalize the provider response into the document IR.
4. Attach page and region evidence to every extracted claim.
5. Validate against the JSON Schemas in `schemas/`.
6. Repair only validation failures, and record each repair attempt.
7. Write a run manifest and all intermediate artifacts under `result/runs/`.

## Quality rules

- Never invent text that is not supported by a page image or OCR result.
- Keep coordinates in source-page pixels and include the page number.
- Preserve the raw provider response for auditability.
- Fail closed when required evidence or schema validation is missing.
