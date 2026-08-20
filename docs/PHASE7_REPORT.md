# Phase 7 完成报告

**日期:** 2026-08-20  
**状态:** ✅ 完成（**未进入 Cleaner**）

## 四个关键验收数字

| 项 | 结果 |
|----|------|
| PAGE marker | **8** |
| 未解决 FIGURE marker | **0** |
| 无效图片路径 | **0** |
| 第二次 Assemble | **CACHED** |
| markdown_pages / resolved_pages / figures SHA256 | **全部不变** |

## 1. 新增/修改文件

**新增**

- `core/assemble_models.py`
- `services/page_source_resolver.py`
- `services/assemble_readiness_service.py`
- `services/continuity_analyzer.py`
- `services/markdown_assembler.py`
- `services/assembled_markdown_validator.py`
- `workers/assembly_worker.py`
- `gui/widgets/assemble_panel.py`
- `gui/widgets/continuity_review.py`
- `tests/test_assemble_phase7.py`
- `scripts/phase7_live.py`
- `docs/PHASE7_REPORT.md`

**修改**

- `storage/database.py` (v7)
- `storage/repository.py`
- `config/default.yaml`
- `gui/main_window.py`
- `services/figure_review_service.py`（accept 保留 placement）
- `tests/test_database.py` / `test_transcription.py` / `test_gui_smoke.py`
- `docs/ROADMAP.md`

## 2. schema v7

安全迁移新增表：

- `continuity_patches`
- `assemble_runs`
- `document_artifacts`

旧数据保留。

## 3. PageSourceResolver

优先级：

1. 有 Figure → 必须 `resolved_pages`
2. 无 Figure 且 figures SUCCESS → 优先 resolved copy
3. 否则 `markdown_pages`
4. 按 DB `page_number` 排序，禁止 glob 字典序

## 4. AssembleReadiness

需同时：Transcription Ready + Figures Ready + Sources Complete + 无 RUNNING。  
默认禁止 unresolved figures；高级 Override 可开。

## 5. Manifest

`intermediate/assemble_manifest.json`：每页 source / source_type / sha256。

## 6. Assembly hash

含：ordered page source hashes、patch digests、assembler version、preserve_page_markers、allow_unresolved。

## 7. Cache

hash 相同且 raw 通过校验 → `CACHED`，不重写。

## 8. raw.md 生成

字符串级拼接，页间 `\n\n`，原子写 `raw.md.tmp` → validate → replace。失败保留旧 raw。

## 9. PAGE marker

保留；缺失则安全插入；重复则保留一个并 warning。

## 10–11. Figure / unresolved

默认 raw 中不得残留 `<!-- FIGURE`；图片 `figures/...` 必须存在。Override 时显式 warning。

## 12–15. Continuity

规则分析（flags + 轻量 heuristic），**不调 AI**。Patch：NO_ACTION / JOIN_WITH_SPACE / WITHOUT / NEWLINE / CUSTOM。Stale hash → 不应用。

## 16–17. GUI

Continuity Review + Assemble Panel（Readiness 明细、生成 raw.md）。

## 18. 8 页 Pilot

| 项 | 值 |
|----|-----|
| 总页数 | 8 |
| resolved sources | 8 |
| canonical sources | 0 |
| figure links | 4 |
| continuity candidates | 0 |
| patches | 0 |
| warnings | [] |
| raw size | 15430 bytes |

## 19–27. 验证摘要

- PAGE markers: 8（1→8）
- unresolved FIGURE: 0
- figure paths valid: 4/4
- 新增 `---`: 0
- 第二次: CACHED
- 上游 SHA256: 不变

## 28. Typora smoke

`not_performed`（未本机自动打开）

## 29–30. 测试

- pytest: **94 passed**
- GUI smoke: assemble_panel + continuity_review 存在

## 31. 已知问题

- Pilot 曾需 promote 页 1/8（原先 needs_review）
- Phase 6.5 accept 曾覆盖 placement；已修复并在 Pilot 中重建 resolved
- Continuity 本样本 0 candidates（正常）

## 32. READY FOR CLEANER?

**是（结构上）** — raw.md 已形成完整文档派生层。  
Cleaner 应作为**新派生层**，不得改 markdown_pages / resolved_pages / figures。

## 33. Phase 8 建议

- 删除 PAGE marker（仅 clean/final）
- 跨页句、公式、表格规范化
- 不要与 Assemble 混做
- 若连续性候选变多，再考虑独立 AI Continuity Repair

## 运行

```bash
python scripts/phase7_live.py
pytest tests/test_assemble_phase7.py -q
```

**Phase 7 停止。不进入 Cleaner。**
