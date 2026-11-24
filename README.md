# PaperMentor

面向学生与科研人员的证据型论文阅读 Skill。输入文本型论文 PDF，输出可核验的精读笔记、对比报告、组会提纲和复习卡片。

核心约束只有一条：**每条关键结论都必须带物理页码和连续原文引用**。论文事实、作者观点、实验结果和模型推断分开标注，查不到的内容明确说明查不到，不靠模型记忆补写。

## 解决什么问题

通用大模型读论文时容易出现三种问题：伪造页码和引文、把作者推测说成实验结论、拿不同实验口径的数值直接排名。PaperMentor 把阅读过程拆成「解析 → 分章 → 带页码阅读 → 证据校验 → 结构校验 → 导出」，校验不通过就重读或删除该论断，不绕过。

## 支持的任务

| 任务 | 典型请求 | 产出 |
|---|---|---|
| 速读 | “这篇论文讲了什么，方法、结果和局限各是什么” | 四段式速读卡片 |
| 精读 | “精读这篇，关键结论要能追溯到原文” | 结构化笔记 + 证据索引 |
| 多篇对比 | “对比这三篇的方法和实验设置” | 统一维度矩阵、共识、分歧、研究空白 |
| 论文问答 | “这篇的 batch size 是多少” | 仅基于原文的回答，证据不足时明确说明 |
| 组会 / 复习 | “根据笔记生成组会提纲和复习卡片” | 汇报提纲、问答卡片 |

## 快速开始

### 环境要求

- Python 3.9+
- PyMuPDF 1.24 及以上（唯一运行时依赖）
- 宿主 Agent 支持 `SKILL.md` 形式的 Skill

### 1. 安装 Skill

仓库根目录就是 Skill 本体，克隆时必须把目标目录名指定为 `paper-mentor`（与 `SKILL.md` 里的 `name` 保持一致）。

用户级安装（对所有项目生效）：

```bash
# 码道 Agent
cd ~/.codeartsdoer/skills
git clone https://github.com/kingdomlight/evidence-paper-mentor-skill.git paper-mentor

# Claude Code
cd ~/.claude/skills
git clone https://github.com/kingdomlight/evidence-paper-mentor-skill.git paper-mentor
```

项目级安装（只对当前项目生效）：

```bash
mkdir -p .codeartsdoer/skills
git clone https://github.com/kingdomlight/evidence-paper-mentor-skill.git .codeartsdoer/skills/paper-mentor
```

### 2. 安装解析依赖

```bash
cd paper-mentor
python -m pip install -r scripts/requirements.txt
```

### 3. 验证解析可用

用自带的合成论文检查一次解析状态：

```bash
python scripts/paper_tools.py inspect assets/examples/papers/paper01.pdf --output /tmp/inspect.json
```

输出中的 `status` 为 `ok` 说明环境正常。

## 在 Agent 里怎么用

安装完成后直接用自然语言提出请求，不需要记命令：

```text
速读这篇论文：/path/to/paper.pdf

精读这篇论文，输出带页码和原文引用的笔记，保存为 reading-note.md

对比这三篇论文的方法、数据集和评价指标，口径不同的地方标注不可直接比较

这篇论文里训练用了多少张 GPU？只根据原文回答
```

## 底层命令

Skill 内部的确定性工具，负责解析和校验，不依赖模型判断：

```bash
python scripts/paper_tools.py inspect    INPUT.pdf --output OUT/inspect.json
python scripts/paper_tools.py extract    INPUT.pdf --output OUT/paper.pages.json
python scripts/paper_tools.py sectionize OUT/paper.pages.json --output OUT/paper.sections.json
python scripts/paper_tools.py context    OUT/paper.sections.json --task read --output OUT/paper.context.json
python scripts/paper_tools.py verify     OUT/note.json --sections OUT/paper.sections.json --output OUT/evidence-report.json
python scripts/paper_tools.py validate   OUT/note.json --schema assets/contracts.json --evidence-report OUT/evidence-report.json --output OUT/validation-report.json
python scripts/paper_tools.py export     OUT/note.json --format markdown --output OUT/reading-note.md
```

可用 `--format markdown|json|csv` 导出，`plan` 子命令用于生成子 Agent 任务计划。

## 目录结构

```text
paper-mentor/
├── SKILL.md                        Skill 定义：任务、流程、执行原则、完成标准
├── README.md
├── scripts/
│   ├── paper_tools.py              解析、证据校验、结构校验、导出
│   ├── test_skill.py               39 项自动化测试
│   ├── evaluate.py                 八篇样本离线评估
│   └── requirements.txt            PyMuPDF 依赖
├── references/
│   ├── reading-guide.md            单篇阅读、多篇对比、问答、故障处理规范
│   ├── quality-and-agents.md       证据规则、质量检查、子 Agent 编排
│   └── test-report.md              测试与评估结果记录
└── assets/
    ├── contracts.json              输出 Schema、默认配置、评分配置
    ├── templates.md                速读、精读、对比、组会、卡片模板
    └── examples/                   八篇原创合成论文与评估样例
```

`assets/examples/` 下全部为原创合成论文，仅用于验证解析、证据追溯和异常停止流程，不代表对真实论文的效果。

## 设计原则

1. **不虚构**：不生成论文中不存在的页码、引文、实验、数据集或参考文献。
2. **信息分层**：`paper_fact`、`author_claim`、`experiment_result`、`agent_inference` 四类分开标注。
3. **口径优先**：数据集、指标或实验设置不同的结果标记为不可直接比较，不做纯数值排名。
4. **边界披露**：解析失败、正文不足、扫描件无文本层时停止生成完整笔记，并说明原因。
5. **不污染 Skill**：中间文件写入用户指定目录，不修改 Skill 自身文件。

## 测试

```bash
python scripts/test_skill.py
python scripts/evaluate.py --output-dir /tmp/paper-mentor-eval
```

2026-11-17 的离线结果为：自动化测试 39 项全部通过；八篇合成样本中 6 篇需要精读笔记的样本，证据校验与联合结构校验均 6/6 通过，72 条页级引文全部匹配。样本为合成数据，不构成真实论文上的效果声明。

## 已知限制

- 只支持带文本层的 PDF；扫描件需要先做 OCR，Skill 会明确拒绝并提示。
- 证据校验中的字符串匹配只证明引文存在于原文，语义是否支持论断仍需模型复核。
- 多篇对比能标注口径差异，但无法消除不同实验设置带来的不可比性。
- 长论文依赖宿主提供子 Agent 能力，否则串行执行，耗时更长。

## 许可

MIT License。