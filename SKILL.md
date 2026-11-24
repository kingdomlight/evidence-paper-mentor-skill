---
name: paper-mentor
description: PaperMentor 是面向学生与科研人员的证据型论文阅读助手。上传文本型论文 PDF，即可生成带物理页码和原文引用的精读笔记，系统拆解研究问题、核心方法、实验结果、创新点与局限，并区分论文事实、作者观点和分析推断。支持单篇速读与精读、多篇文献对比、论文追问、组会汇报提纲和复习卡片，让关键结论可回溯、文献差异可核对。遇到证据不足、扫描件无法识别或实验口径不可比时，会明确提示边界，减少凭记忆补写和过度外推，适合高效读论文、整理文献、准备组会与复习。
---

# PaperMentor

## 目标

把论文转换为可核验的阅读成果，而不是只改写摘要。关键事实、实验数值和作者结论必须能追溯到所提供论文；无法确认的信息必须明确标记。

## 支持任务

- 速读：研究问题、方法、主要结果和局限的简短卡片。
- 精读：方法、实验、创新、局限和证据索引完整的结构化笔记。
- 组会与复习卡片：从已核验笔记生成汇报提纲和复习材料。
- 多篇对比：统一维度的矩阵、共识、分歧和研究空白。
- 论文问答：只根据当前论文证据回答，证据不足时明确说明。

## 执行原则

1. 不凭模型记忆补写论文事实，不虚构页码、引文、实验、数据集或参考文献。
2. 区分 `paper_fact`、`author_claim`、`experiment_result` 和 `agent_inference`。
3. 解析失败、正文不足或扫描件无法识别时，说明限制并停止生成完整精读笔记。
4. 按需读取 `references/reading-guide.md` 和 `references/quality-and-agents.md` 的相应章节，不加载示例 PDF、复核图和演示 GIF。
5. 中间文件写入用户指定输出目录或当前工作目录的临时目录，不修改 Skill 自身文件。
6. Skill 维护目标为约 25 个文件；提交包最多 30 个文件、全部文件内容总大小不超过 40MiB，不生成冗余中间产物到 Skill 目录。

## 主流程

1. 判断任务类型、阅读深度、用户关注点和输出形式；默认配置见 `assets/contracts.json` 的 `settings`。
2. 在 Skill 根目录执行确定性解析，输出目录由用户指定：

   ```bash
   python scripts/paper_tools.py inspect INPUT.pdf --output OUTPUT/inspect.json
   python scripts/paper_tools.py extract INPUT.pdf --output OUTPUT/paper.pages.json
   python scripts/paper_tools.py sectionize OUTPUT/paper.pages.json --output OUTPUT/paper.sections.json
   python scripts/paper_tools.py context OUTPUT/paper.sections.json --task read --output OUTPUT/paper.context.json
   ```

3. 检查 `status`、页数和正文字符数。状态不是 `ok` 时，按阅读指南的故障处理章节执行，不生成虚假完整笔记。
4. 按任务读取规范：
   - 单篇、组会和卡片：`references/reading-guide.md` 的单篇阅读章节。
   - 多篇对比：同文件的对比章节。
   - 论文追问：同文件的问答章节。
   - 证据、质量检查和子 Agent：`references/quality-and-agents.md`。
5. 阅读带页码的上下文，按 `assets/contracts.json` 的 `reading_note_schema` 生成结构化结果。每条关键论断携带物理页码和连续短原文。
6. 执行证据与结构校验：

   ```bash
   python scripts/paper_tools.py verify OUTPUT/note.json --sections OUTPUT/paper.sections.json --output OUTPUT/evidence-report.json
   python scripts/paper_tools.py validate OUTPUT/note.json --schema assets/contracts.json --evidence-report OUTPUT/evidence-report.json --output OUTPUT/validation-report.json
   ```

7. 校验失败时回到对应章节修正；无法修正的论断删除或缩窄，不得绕过校验。字符串匹配不代替语义支持检查。
8. 使用 `assets/templates.md` 中对应模板输出。需要文件时执行：

   ```bash
   python scripts/paper_tools.py export OUTPUT/note.json --format markdown --output OUTPUT/reading-note.md
   ```

9. 输出前按质量指南检查来源串线、过度外推、不可比实验和未披露的覆盖边界。

## 可选子 Agent 编排

仅当宿主明确提供子 Agent 调用能力时启用，不得调用不存在的工具。

- 深度精读可拆分方法、实验和批判审阅；多篇对比可按论文拆分，首批最多 3 个任务。
- 速读、短论文、简单追问和格式转换默认由主 Agent 串行完成。
- 子 Agent 只读取分配章节，不重复解析 PDF，不修改 Skill 文件，不联网补充论文事实。
- 子 Agent 返回格式遵循 `assets/contracts.json` 的 `subagent_result_schema`。
- 主 Agent 负责合并、冲突核查、论文 ID 检查、证据校验和最终输出。
- 不支持子 Agent 时使用相同规范串行执行，输出字段不得减少；子 Agent 失败时由主 Agent 补读，不盲目重试。
- 可用 `python scripts/paper_tools.py plan --output OUTPUT/plan.json` 生成确定性任务计划，该脚本不调用宿主工具。

## 多篇任务

逐篇生成带独立 `paper_id` 的论文卡片并完成证据校验，然后再对比。不同数据集、指标、实验设置或任务定义的结果必须标记为不可直接比较，不得只按数值排名。

## 测试与提交检查

```bash
python scripts/test_skill.py
python scripts/evaluate.py --output-dir /path/to/paper-mentor-test-results
```

评估读取 `assets/examples/test-cases.jsonl`、`benchmark.json` 和 `outputs/notes.json`，只生成汇总的 `summary.json` 与 `package-report.json`，不为每篇论文保留中间文件。输出目录必须位于 Skill 包外；省略 `--output-dir` 时使用系统临时目录。提交检查统计全部实际文件，不排除缓存。

## 完成标准

- 输出符合任务类型，关键论断有页码和原文证据，推断与作者观点分开。
- 不可回答问题明确说明证据边界，解析失败和覆盖不全得到披露。
- 文件导出成功时告知位置，失败时说明原因和下一步。
- 测试和提交检查通过，文件数不超过 30，内容总大小不超过 40MiB。
