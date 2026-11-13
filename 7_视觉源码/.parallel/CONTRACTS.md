# Shared kernel contracts

本文件冻结 W0 共享内核的跨层契约。业务阶段只能填充实现，不得增删以下签名或 dataclass 字段。

## Stage signatures

```python
pdf_input.load_pdf(path: Path) -> PdfInfo
codex_adapter.CodexAdapter.generate(request: ModelRequest) -> ModelResult
validate.validate_run(run_dir: Path, settings: Settings) -> ValidationReport
prompt_loader.load_prompt(name: str, base_dir: Path) -> PromptAsset
prompt_loader.load_skill(path: Path) -> SkillAsset
writer.write_draft(evidence_path: Path, ctx: RunContext) -> StageResult
repair.repair_draft(draft_path: Path, validation_path: Path, ctx: RunContext) -> StageResult
packaging.build_package(materials: list[str], out_path: Path, settings: Settings) -> PackageReport
```

## Contract dataclasses

```python
ModelRequest(stage, prompt_name, prompt_text, payload, json_schema=None,
             images=None, timeout_s=None, model=None)
ModelResult(text, parsed, raw, usage, truncated, latency_ms, attempts,
            request_hash, model, finish_reason)
PromptAsset(text, sha256, path, version)
SkillAsset(text, sha256, path)
RunContext(run_id, material_id, run_dir, settings, artifacts)
StageResult(name, status, duration_ms, artifacts, issues)
PdfInfo(sha256, page_count, file_size, encrypted, pages)
PageInfo(page_number, width_pt, height_pt, rotation, has_text_layer, render_path)
ValidationReport(valid, schema_version, checked_at, stats, issues)
PackageReport(entries, excluded, verified, built_at)
```

## SHA-256 (relative to `source_code/`)

| File | SHA-256 |
|---|---|
| `pipeline/errors.py` | `767176ebec7bb239f4d9a775c5945a818b817df5a52f66c6b7f8e4d79bfa2455` |
| `pipeline/contracts.py` | `da75039c97bd0f4488c17fda18b1a534672c58fc82a6cb274e2a1648e7b73df5` |
| `pipeline/settings.py` | `ceac3ac761bf719d5747358408c06c48973828be0092e5bb5a5e8a8845b271da` |
| `pipeline/schema_loader.py` | `fc99d48ad1f525b25bb288d4129f580ec521af6de29ea19dd77627e6a656fb2e` |
| `pipeline/orchestrator.py` | `6ea6a3112b0e512cdb9c65453383a279b8b43eb10d20b767e6cea51972dbc798` |
| `pipeline/manifest.py` | `98f36eba02bd309affa1ea85d369f4e9e2f888b7f0bec8ebd22714f7354d1d3b` |
| `pipeline/pdf_input.py` | `ac4a20724d6c16fdd72cff7df2c1283ca084ceecb1082fb22a25541c709db07d` |
| `pipeline/codex_adapter.py` | `0af921635e0bf6a5ab198efd43b195ba9476075d89a36a9aac59f45cc1b8b41d` |
| `pipeline/validate.py` | `7e2b0c407ec3d7564da2f5285cb22056d5af4a39b3b07ee635a760e46be91d83` |
| `pipeline/prompt_loader.py` | `50d5dabd5599eb9db1303dc015b1c11e0f2543e1d1de23d08e83f64b311a4aef` |
| `pipeline/writer.py` | `725662955255fd6583f5adfc9cbf95e6f63af9a88f6a9f06322c3fd1cad91b60` |
| `pipeline/repair.py` | `ef9bbe3cc521338965f306d317f6ab925fb50c7b1ccf1a337da982811c9abfb0` |
| `pipeline/packaging.py` | `39a8654fcd16ae564b000f9b452c4c0ab1762392689eb2f11dc58ec62bb6a64c` |
| `pipeline/stages/__init__.py` | `8f16f590786772ab66ff282c2ab620c7bdafe011a926a098ae73da703af0fcac` |
| `main.py` | `084cf8e22831b04ca7f282f28c39f82957c90c153ed138a06da5273e84020d40` |
| `config.json` | `05404b03ce17da426b4fe0d35eb630e4da7390d9aa509ca2221080cf3b37273b` |
| `requirements.txt` | `b55e8b2c383c542e63a7f3879e7e4299dd1962869f70a814d9adec911d8facfb` |
| `requirements-lock.txt` | `b55e8b2c383c542e63a7f3879e7e4299dd1962869f70a814d9adec911d8facfb` |
| `.env.example` | `cdd4a9835d1f3d0cd997c8d33df9523f2be89c1e125552536e66ba2b945d8aa9` |
| `schemas/validation.schema.json` | `98801c68b596aac7cb407ee4e8b60a8342ac2c4daeda6a7b5dfc4be7ee55758e` |



