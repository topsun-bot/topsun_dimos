# 2026-05-19 — `按 dimos 模板生成` 技能

## 什么是这个技能

一份给 AI 看的**写作 + 出图 + 出 PDF**模板。以后跟 AI 说：

> "按 dimos 模板生成一份 \<主题> 文档"

AI 会按统一骨架（通俗→总览→分模块详解→实战→扩展）写一份带 15+ 张 mermaid 图、
5+ 张表格的 markdown，然后**自动**用 chrome headless + mermaid.ink 渲染成 25–40
页的 PDF，存到 `docs/cursor/<topic>.md` 和同名 `.pdf`。

参考成品：dimos 仓库的 `docs/cursor/dimos-navigation-mapping-tutorial.md`（957 行 / 17 张
mermaid / 31 页 PDF）。

## 添加了哪些文件

| 路径 | 说明 |
|------|------|
| `.cursor/rules/dimos-document-template.mdc` | Cursor rule（`alwaysApply: true`），定义骨架、写作 13 条铁律、mermaid 用法、PDF 流程 |
| `.cursor/scripts/md_to_pdf.py` | markdown → PDF 转换脚本（mermaid 用 `mermaid.ink` 在线预渲 SVG，再用 chrome headless 出 PDF，绕开 chrome 不等 JS 异步的坑） |

> `.cursor/` 已加入 `.gitignore`，是个人配置，不会推到远程。

## 使用流程

```bash
# 1. 让 AI 按模板写
#    "按 dimos 模板生成一份 perception 教程"
#    AI 输出: docs/cursor/dimos-perception-tutorial.md

# 2. 一行出 PDF（AI 也会自动做）
python3 .cursor/scripts/md_to_pdf.py docs/cursor/dimos-perception-tutorial.md
# → docs/cursor/dimos-perception-tutorial.pdf

# 3. 抽检
pdfinfo docs/cursor/dimos-perception-tutorial.pdf | head -5
pdftoppm -f 1 -l 2 -r 90 docs/cursor/dimos-perception-tutorial.pdf /tmp/check
```

## 骨架要点（节选）

1. **第一章绝对通俗**：0 dimos 类名、0 Python 代码，只讲领域概念 + 类比 + ASCII 图
2. **从第二章开始**先画总览 mermaid 大图，再分模块讲；每节"问题 → 答案 → 代码片段 → 参数表"
3. **章节用罗马数字**（一、二、三…），子节阿拉伯（3.1、3.2）+ 带通俗副标题"X — 让 Y 做 Z"
4. **mermaid 图密度** 15–20 张/30 页，混用 `flowchart TB/LR` + `sequenceDiagram` + `stateDiagram-v2`
5. **表格** 5–10 个：算法对比、参数表、cheatsheet
6. 末尾必有"基于 commit `<sha>`"版本注

## PDF 转换细节（脚本干了什么）

- 用 `mermaid.ink` 在线服务**预渲染**每张 mermaid 为 SVG（同步、不依赖 chrome 跑 JS）
- 把 SVG 内嵌进 HTML，CSS 限制 `max-height: 600pt` 防止单图溢出页
- chrome headless `--print-to-pdf`，A4 + 18mm 边距
- 中文字体 fallback: `Noto Sans CJK SC` → `PingFang SC` → `Microsoft YaHei`

## 触发词

跟 AI 说**任何一个**都会走这个模板：

- "按 dimos 模板生成"
- "用 dimos 文档模板"
- "按模板写"

## 文件清单

```
.cursor/rules/dimos-document-template.mdc            # rule 主体
.cursor/scripts/md_to_pdf.py                         # markdown→PDF 工具
.gitignore                                           # +.cursor/ (个人配置)
```
