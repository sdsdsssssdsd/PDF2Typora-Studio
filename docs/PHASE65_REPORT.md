# Phase 6.5 完成报告

**日期:** 2026-08-20  
**状态:** ✅ 完成（未进入 Phase 7）

## 目标达成

| 指标 | Phase 6 | Phase 6.5 Pilot |
|------|---------|-----------------|
| 页 3/4/7 Figure 总数 | 4 | 4 |
| AUTO_RESOLVED | 0 | 0（batch 仍进 Review） |
| 经 Review 闭环 RESOLVED | 0 | **4** |
| FAILED | 0 | 0 |
| Figure Readiness | Not Ready | **Ready** |
| canonical SHA256 | — | **不变** |

## 交付清单（30 项）

1. ✅ `services/figure_marker_normalizer.py`
2. ✅ 严格 / loose marker 识别
3. ✅ `FigureMarkerValidator` 集成 normalizer
4. ✅ SAFE_MARKER_REPAIR（仅 syntax_only）
5. ✅ 禁止 missing_marker 自动插入
6. ✅ 禁止 marker_index_conflict 自动改 index
7. ✅ schema v6 安全 ALTER
8. ✅ marker_original / marker_normalized / marker_repair_type
9. ✅ manual_bbox_1000 / resolved_bbox_1000
10. ✅ review_status / review_action / reviewed_at
11. ✅ selected_candidate_id / manually_adjusted
12. ✅ manual marker placement 字段
13. ✅ Marker repair 仅影响 resolved_pages
14. ✅ canonical markdown_pages 不修改
15. ✅ `ResolvedPageBuilder` 确定性构建
16. ✅ resolved hash 含 repair/placement/skip digest
17. ✅ `FigureReviewService` preview / accept / skip
18. ✅ Preview 写入 `.cache/figure_preview/`
19. ✅ Accept 原子写入 figures/
20. ✅ Figure Review GUI 完整化（缩放/拖框/候选/Preview）
21. ✅ Marker Placement 工作副本编辑器
22. ✅ 快捷键 A/R/P/N
23. ✅ 上一问题 / 下一问题导航
24. ✅ Review Queue Transcription + Figures Tabs
25. ✅ `FigureReadinessService.is_ready_for_assemble()`
26. ✅ GUI Figure Readiness 状态显示
27. ✅ 未实现 Assemble 按钮
28. ✅ config `figures.marker_normalization` 等
29. ✅ pytest 78 passed（含 phase65 测试）
30. ✅ 真实 Pilot：`phase65_live_report.json`

## Pilot 详情

- **Page 3:** fig02, fig03 — missing_marker → 手工 placement + PDF clip → RESOLVED
- **Page 4:** fig04 — missing_marker（Gemma index=4 无 HTML marker）→ RESOLVED
- **Page 7:** fig05 — missing_marker → RESOLVED

## 已知限制 / 下一步建议

- 4/4 均依赖 **missing_marker 人工 placement**（Pilot 用中点 offset 模拟 GUI）
- 若 loose marker 语法修复可覆盖更多页，batch 阶段 AUTO 率会上升
- 若仍大量需纯手工框 → 考虑 Phase 6.6 Locator
- **当前 Readiness=Ready，可评估进入 Phase 7 Markdown Assemble**

## 运行

```bash
python scripts/phase65_live.py
pytest tests/test_figure_phase65.py -q
```
