# 详细工作流参考

> SKILL.md 的执行层。按需查阅，不必全读。

## 数据分析工作流

### 简单分析（单维度、快速查数）

```python
# Excel
import pandas as pd
df = pd.read_excel('数据.xlsx', sheet_name='Sheet1')
print(f"维度：{df.shape[0]}行 × {df.shape[1]}列")
print(df.describe())

# CSV
df = pd.read_csv('数据.csv', encoding='utf-8')  # 中文Windows试 encoding='gbk'
```

也可用内置脚本：
```bash
SKILL_DIR="$(dirname "$(find ~/.claude/skills -name 'SKILL.md' -path '*/data-office-pro/*' 2>/dev/null | head -1)")"
python3 "$SKILL_DIR/scripts/read_excel.py" data.xlsx --summary
```

输出格式：
```
核心结论（1-3句）
→ 数据支撑（具体数字、对比、趋势）
→ 异常/风险
→ 可执行建议（3-5条，按优先级）
→ 下一步（多想一步）
```

---

### 多专家深度分析工作流（核心方法论）

面对有深度分析价值的数据集时，使用此工作流。详见SKILL.md「核心方法论」。

#### Phase 1: 数据理解

读取所有数据文件，输出概览：

```python
import pandas as pd
df = pd.read_csv('data.csv', parse_dates=['Date'])
print(f"维度: {df.shape}")
print(f"时间范围: {df['Date'].min()} — {df['Date'].max()}")
print(f"字段: {list(df.columns)}")
print(df.describe())
```

输出清单：
1. 数据维度（行×列）、时间跨度
2. 字段清单（名称、类型、缺失率）
3. 基础统计（均值/中位数/极值）
4. 数据质量问题
5. 初步洞察（1-2个趋势或异常）

#### Phase 2: 专家选角

基于数据特征，选取3-5个专家角色。**写入md文件供用户确认。**

选角维度参考（按数据类型选取最匹配的组合）：

| 数据类型 | 候选角色方向 |
|---------|------------|
| 金融/股票 | 量化分析师、估值专家、行业分析师、宏观经济学家、行为金融学家 |
| 电商/投放 | 投放优化师、用户增长专家、供应链分析师、品牌策略师、财务分析师 |
| 用户行为 | 用户研究员、产品经理、数据科学家、心理学家、增长黑客 |
| 运营数据 | 运营管理专家、流程优化师、质量管理专家、成本分析师、行业顾问 |
| 市场调研 | 行业分析师、竞争策略专家、消费者洞察专家、趋势研究员、定价专家 |

每个角色的陈述（300-500字）需包含：
- **我是谁** — 一句话定义角色和专业背景
- **我关注什么** — 这份数据中最吸引我的2-3个维度
- **我的分析方法** — 会用什么框架/模型/指标
- **我的分析目标** — 最终要回答什么问题

#### Phase 3: 并行深度分析（subagent执行）

**每个专家角色启动一个独立subagent，并行执行。**

subagent启动方式：
```
Task工具调用:
  subagent_type: "general-purpose"
  run_in_background: true
  prompt: [包含角色定义+数据路径+分析任务+输出格式]
```

**subagent prompt模板**：

```
你是[角色名称]，[一句话背景]。

## 你的任务

分析以下数据文件：
- [文件路径1]
- [文件路径2]（如有）

## 数据概览

[粘贴Phase 1的数据概览摘要，帮助subagent快速理解数据]

## 分析方向

[粘贴该角色在Phase 2中陈述的分析方向]

## 具体分析任务

1. [任务1：如"计算年度收益率分布的偏度和峰度"]
2. [任务2：如"回测MA20/MA50交叉策略"]
3. [任务3：如"量化极端事件频率vs正态预期"]

## 输出要求

将分析结果写入Markdown文件：[指定输出路径]

文件结构：
1. 核心发现（3-5条，每条1-2句，带具体数字）
2. 详细分析（每个任务一节，含计算过程和结论）
3. 关键数据表（供最终报告引用的数字汇总）
4. 图表建议（建议在最终HTML报告中呈现的图表类型和数据）

要求：
- 所有结论必须有数据支撑
- 标注异常值和需要注意的风险
- 不需要考虑排版美化，专注分析质量
```

**并行管理**：
- 所有subagent同时启动（run_in_background=true）
- 等待所有subagent完成后，收集各自的输出文件
- 如某个subagent失败，记录错误并用主进程补充该角色的分析

#### Phase 4: 统一综合呈现

**核心原则：最终报告中不出现任何专家角色名字。**

以「管理型高级分析师」视角整合所有分析结论：

**整合步骤**：
1. 读取所有subagent的分析结果文件
2. 提取关键发现，按主题重新组织（而非按角色）
3. 交叉验证：不同角色对同一现象的分析是否一致？矛盾处需重点讨论
4. 筛选最有说服力的数据和图表，构建叙事线
5. 生成最终HTML报告

**报告结构**：
```
1. 标题（结论式中文，如「6400亿美元之问」「CapEx翻倍，净现金首次转负」）
2. 核心摘要（5条核心发现）
3. 关键指标面板（4-6个大数字）
4. 分主题深度分析（每个主题一节，融合多个角色视角）
5. 综合结论与建议
6. 页脚（中文）
```

**标题风格**：
- 每节标题必须是结论句式（如「CapEx翻倍，净现金首次转负」）
- 而非描述句式（如「资本支出分析」）
- **所有标题、图表标题、图例、页脚用中文**（参见SKILL.md语言规则）

**图表选取**：
- 从各subagent建议的图表中选取最有说服力的5-8个
- 可用CDN库（ECharts、D3.js）或纯SVG，按需选择
- 每个图表必须有标题（结论式中文）、副标题（上下文）、数据来源

---

## Excel操作工作流

### 公式生成

始终包含错误处理（IFERROR）。输出格式：
```
公式：=IFERROR(D2/C2, 0)
含义：计算ROI（GMV÷消耗），除零时返回0
适用范围：H2:H[最后一行]
```

### 模板设计

```
Sheet 1: 原始数据 — 字段定义、数据验证规则
Sheet 2: 计算层 — 衍生指标公式、条件格式
Sheet 3: 汇总视图 — 透视表、关键指标看板
Sheet 4: 图表 — 数据可视化
```

### 数据处理脚本

当公式无法满足时（大数据量、复杂逻辑），用Python处理。
原则：优先用Excel公式（用户可维护），复杂场景才用脚本。

---

## 报告生成工作流

### 管理层汇报（1页纸原则）
1. 核心结论（3句以内）
2. 关键指标汇总（表格）
3. 问题诊断（数据支撑）
4. 优化建议（按优先级）
5. 风险提示

### 详细分析报告
1. 摘要（半页）
2. 数据概览（关键指标面板）
3. 分维度分析（每个维度一节）
4. 异常值与风险
5. 建议与行动计划
6. 附录：数据来源与方法论

---

## HTML交互报告工作流

默认输出格式。使用ECharts/D3.js或纯SVG + 分析文字。

### 关键约束
- 图表用SVG/Canvas绑定数据，不用CSS absolute定位模拟
- 可使用CDN库（ECharts、D3.js等），按图表复杂度自行选择
- 风格参数 → `visual-design-system.md`
- 模板 → `html-templates.md`

---

## PPT制作工作流（HTML→PPTX）

### 流程
```
确认受众与风格 → 生成大纲 → 逐页创建HTML → 转PPTX → 预览确认
```

### 必须问清楚的3个问题
1. 受众是谁？（管理层/客户/内部团队）
2. 用途是什么？（汇报/比稿/培训/记录）
3. 时间多长？（约1分钟/页）

### 断言式标题

| 差标题 | 好标题 |
|--------|--------|
| Q1投放数据 | Q1整体ROI达3.2，超目标7% |
| 问题分析 | 服饰板块退货率45%是亏损主因 |

### HTML幻灯片规则

画布：`width: 720pt; height: 405pt`（16:9）

关键：
- 文字必须在 `<p>`/`<h1>`-`<h6>`/`<ul>`/`<ol>` 内 — `<div>` 裸文字不进PPT
- 背景/边框/阴影只用在 `<div>` 上
- 禁止CSS渐变（需渐变先用Sharp渲染PNG）
- 只用web安全字体：Arial, Helvetica, Georgia, Verdana, Tahoma
- 列表用 `<ul>`/`<ol>`，禁止手动 •/-/* 符号
- 图表颜色hex**不带#前缀**（PptxGenJS规则）

### 构建命令

```bash
SKILL_DIR="$(dirname "$(find ~/.claude/skills -name 'SKILL.md' -path '*/data-office-pro/*' 2>/dev/null | head -1)")"

# 从多个HTML构建
node "$SKILL_DIR/scripts/build_pptx.js" --slides slide1.html slide2.html --output report.pptx

# 从目录加载
node "$SKILL_DIR/scripts/build_pptx.js" --dir ./slides/ --output report.pptx

# 带图表
node "$SKILL_DIR/scripts/build_pptx.js" --slides slide1.html --output report.pptx --chart 0:col:chart_data.json
```

### 图表数据JSON格式
```json
{
  "title": "各板块ROI对比",
  "catAxisTitle": "板块",
  "valAxisTitle": "ROI",
  "colors": ["E17055", "45B7AA", "5B8C5A", "FFD700"],
  "series": [{"name": "ROI", "labels": ["美妆","食品","服饰"], "values": [3.8, 2.5, 1.1]}]
}
```

---

## 数据可视化工作流

| 场景 | 方式 |
|------|------|
| 快速看趋势 | Python matplotlib |
| 放进报告 | HTML内嵌图表（SVG/ECharts/D3.js） |
| PPT内原生图表 | PptxGenJS |

模板 → `html-templates.md`，风格参数 → `visual-design-system.md`

---

## PPTX/Excel读取

```bash
SKILL_DIR="$(dirname "$(find ~/.claude/skills -name 'SKILL.md' -path '*/data-office-pro/*' 2>/dev/null | head -1)")"

# 读取PPT
python3 "$SKILL_DIR/scripts/read_pptx.py" presentation.pptx --format markdown
python3 "$SKILL_DIR/scripts/read_pptx.py" presentation.pptx --inventory  # 仅结构

# 读取Excel
python3 "$SKILL_DIR/scripts/read_excel.py" data.xlsx --summary
python3 "$SKILL_DIR/scripts/read_excel.py" data.xlsx --sheet "Sheet1" --head 20
python3 "$SKILL_DIR/scripts/read_excel.py" data.xlsx --format csv
```

---

## 依赖管理

缺失时自动安装，不让用户手动处理。

```bash
# Node.js（PPT制作）
npm install pptxgenjs playwright sharp
npx playwright install chromium

# Python（Excel/PPT读取）
pip install openpyxl pandas python-pptx Pillow
```

遇到版本冲突时用uv隔离：
```python
# /// script
# requires-python = ">=3.10"
# dependencies = ["pandas>=2.0", "openpyxl>=3.1"]
# ///
```
执行：`uv run script.py`

---

## 文件输出约定

| 类型 | 命名规范 |
|------|---------|
| 分析报告 | `分析报告-[主题]-[YYYYMMDD].md` |
| 可视化图片 | `chart-[描述].png` |
| PPT文件 | `[主题]-[YYYYMMDD].pptx` |
| 处理后的Excel | `[原文件名]-processed.xlsx` |
| HTML临时文件 | `/tmp/`，自动清理 |
