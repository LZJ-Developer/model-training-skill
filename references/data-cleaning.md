# 数据清理

> 训练数据清洗的失败大多不是"算法不够好"，而是**默认反射错了**：补零、按字符串判重、
> 全局阈值、均值聚合、删行不留痕。这份文件只写模型默认不会做、但会改变结果的部分。

## 0. 随包脚本

`scripts/sc_clean.py` 是本文件的可执行 SOP，三个子命令：

| 命令 | 做什么 | 保证 |
|---|---|---|
| `inspect` | 形状、X 类型/分布、layer、obs/var、分组大小 | 只读 |
| `qc` | counts 校验 → MT/ribo/HB → 逐批 MAD → 写 `pass_qc` / `qc_reason` | 不删行；不覆盖原对象；写 `qc_summary.json` |
| `pseudobulk` | 按 `--by` 求和原始 counts，`--min-cells` 过滤 | 元数据必须组内常量；不改源对象 |

依赖 anndata、numpy、scipy；只有 `qc --scrublet` 需要 scanpy。`--ledger` 每步追加一行。
整合自 K-Dense `scanpy`（BSD-3-Clause）与 Anthropic `single-cell-rna-qc`（Apache-2.0）；
改动点：只标记不删除、逐批 MAD、默认无 scanpy 依赖。HVG、聚类、注释、绘图不在脚本内。

## 1. 模型默认会做错的地方

| 默认反射 | 实际规则 |
|---|---|
| 缺失/未测补 0 | 掩码。**未测 ≠ 零效应**；任何聚合、损失、统计量先乘掩码并断言样本数 |
| 按字符串判重、按名字切分 | 先归一**生物学实体**：别名（`k562`/`k562_gwps`）、CVCL 编号 vs 名称、Ensembl 版本 |
| 全数据一套 QC 阈值 | 按批次/背景逐组算 MAD；组 <50 退回全局并记录，否则阈值被最大批次绑架 |
| 拿到矩阵就 normalize | 先查列和、分位数、`uns['log1p']`、`layers['counts']`；二次归一化不会报错，只会让结果偏 |
| pseudobulk 用均值 | 用 **raw counts 求和**；均值再取 log 会改变 DE 的均值-方差关系 |
| 把细胞当独立重复 | 重复单元是供体/文库/样本；细胞数只买精度，不买样本量 |
| 清洗后删行 | 先写 `keep`/`reason` 标记列，原对象只读；删行不可复查 |
| 全基因跑默认 HVG | 训练数据保留全转录组；原始 counts、完整对象、分析子集分开存 |

## 2. 工具路由

本包已把与训练清洗相关的部分整合进 `scripts/sc_clean.py`；下表用于需要 HVG、聚类、注释、
绘图或语料级处理时选工具。各 skill 已核对 `SKILL.md` 与脚本，**未安装、未运行**。

| 数据 / 任务 | 工具 | 边界 |
|---|---|---|
| 单细胞 `.h5ad` 检查、QC、pseudobulk | [scanpy（K-Dense）](https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/skills/scanpy/SKILL.md) | 全流程含 HVG/聚类；训练清洗只取所需步骤 |
| 轻量 QC / MAD 过滤 | [single-cell-rna-qc（Anthropic）](https://github.com/anthropics/knowledge-work-plugins/blob/main/bio-research/skills/single-cell-rna-qc/SKILL.md) | 双细胞、ambient RNA 只给建议，未内置执行 |
| 表格 / 元数据 / JSONL 审计 | [data-cleaning（magnus919）](https://github.com/magnus919/agent-skills/blob/main/data-cleaning/SKILL.md) | 不处理 `.h5ad`；负责数据契约、转换日志 |
| LLM / 多模态语料 | [Data-Juicer Harness](https://github.com/datajuicer/data-juicer-agents/blob/main/skills/data-juicer-agents-harness/SKILL.md) | 执行靠底层运行时；装 skill ≠ 装环境 |
| 标签审计 | [cleanlab（AREX）](https://github.com/VectorSpaceLab/AREX-Skill/blob/main/skills/repositories/repo-skills/cleanlab/SKILL.md) | 需要可信的样本外预测，不能把模型错误当脏数据 |

NeMo Curator 的示例仍用 `DocumentDataset`、Dask 等已移除接口（后端已迁 Ray），先不用。

## 3. 三个执行陷阱

1. **`--batch-key` 只影响 Scrublet**，不会自动分组常规 QC 阈值。多背景 h5ad 要在外层按技术批次分组，
   或用 `--group-by` 逐批 MAD；否则高深度批次的阈值会筛掉低深度批次的真实细胞。
2. **不要跑默认 HVG 子集化**。`sc.pp.highly_variable_genes` 之后若只保留 HVG，未选中的基因永久丢失；
   训练/渲染通常要全转录组。保留完整对象，分析子集单独落盘。
3. **小组 MAD 不可用**。组内 <50 个细胞时 MAD 接近 0，会把半数细胞判成离群；脚本会退回全局阈值
   并在 `qc_summary.json` 记录，下游必须知道这一批用的是全局口径。

## 4. 生物陷阱（按检测动作）

| 陷阱 | 检测动作 | 处理 |
|---|---|---|
| **环境 RNA / soup** | 空滴比例、低复杂度文库、外源基因广泛低表达 | SoupX/CellBender 估计比例；标记不默认删；高污染样本单独审 |
| **双细胞** | Scrublet；扰动筛选中查一个 barcode 的 guide 共现 | 双细胞会混合两个扰动；按 guide 唯一性优先过滤，再跑 Scrublet |
| **guide 分配歧义** | 每细胞 guide 数分布、无 guide 比例 | 只留单一/高置信 guide；记录丢弃比例；non-targeting 单独成组 |
| **on-target 效率差异** | self-KD（靶基因自身表达降幅）分布 | 低效率行不当"零效应"，不与高效率行等权；按效率分层 |
| **CRISPR 模态混淆** | KO / CRISPRi / CRISPRa 字段 | 模态独立；不跨模态混训；跨模态只借共享响应基 |
| **对照定义不一致** | non-targeting / untreated / DMSO 标签 | 每语料显式写 control；不同对照的 control 均值不可合并 |
| **伪重复** | DE 的重复单元是细胞还是供体/文库 | 以供体/文库为重复；细胞数只影响精度 |
| **批次×扰动混杂** | batch × pert 交叉表、批次与扰动的相关 | 按批次分层/配对；batch 当协变量；无法配对时报告混杂程度 |
| **归一化顺序** | 列和、分位数、`uns['log1p']`、X 与 `layers['counts']` 是否一致 | raw counts → size factor → CP10k/CPM → log1p，只留一条入口 |
| **注释/ID 漂移** | Ensembl 版本、symbol 别名、重复 symbol、PAR/MT | 固定注释版本；映射表带版本号；重复 symbol 显式聚合并记录 |
| **低细胞数行** | 每个 (背景, 扰动) 的 `n_cells` | 设 `min_cells`（常 30）；不足的行标记，下游按 `n_cells` 加权 |
| **效应量尺度不一致** | 每语料的平均 \|Δ\|、on-target 强度 | 记录定义（log2FC vs log1p 均值差）；对齐系数只在训练折估计 |
| **细胞周期/状态** | 处理组 vs 对照的周期分数分布 | 回归掉或作协变量；增殖变化会被读成转录响应 |
| **存活/选择偏倚** | 强 KD 组的细胞数与存活率 | 强效应基因单列；不要用存活细胞代表全群体 |
| **剂量/时间缺失** | 扰动键是否含 dose/time | 不同 dose 是不同条件；不能合并成一个扰动 |
| **供体泄漏** | split 判重键是否含 donor | 原代细胞按供体切分；同一供体不跨 split |

## 5. 清洗台账

任何改变行数、列数、分布的操作写一行：

```
step:    <drop low-quality cells / map Ensembl→symbol / dedupe constructs>
why:     <触发条件与依据>
before:  <行、细胞、基因、背景数>
after:   <同上>
config:  <阈值、映射表版本、脚本 commit>
artifact:<输出路径、校验和>
```

三条规则：**原始对象不覆盖**（只加标记列）；**阈值与映射表版本进配置**（不写死在脚本）；
**前后对照表与台账一起交付**。没有台账的清洗无法归因。

## 6. 扰动响应 / 虚拟细胞项目专用

- **重复测量**：同一 `(细胞系, 扰动)` 跨语料出现时，一边训练一边验证 = 在训练集上评测。
  只做字符串匹配会漏（如 `k562` vs `k562_gwps`）；判重要按规范化实体，并写成切分断言。
- **"未见"的定义**：必须是"在**所有**训练语料里都没有测量"。只对某一个语料未见，不足以支持
  "未见扰动"结论；donor 覆盖的扰动测的是检索，不是泛化。
- **bulk 与单细胞不可混比**：construct-level pseudobulk 的 `fdr` 常是 z 检验近似，不是逐细胞
  Wilcoxon；这类行的 DE 标签与单细胞语料不同源。
- **控制细胞复用**：同一批 control 被两个 split/context 重复计数会放大控制均值权重；
  按供体/批次切分或显式合并。
- **尺度漂移**：多语料平均 |Δ| 可差 3 倍、on-target 强度可差 16 倍；对齐系数只在训练折估计，
  测试背景参与就是尺度泄漏。
- **证据通道**：donor evidence 必须来自 train、且不得是 eval 行；这只拦同键，别名归一后要再查一次。

## 7. 验收

- [ ] 每个矩阵的表示（counts / CP10k / log1p）与单位（cell / sample / row）已写明
- [ ] 判重键是生物学实体；「未见」按全部语料定义
- [ ] 清洗前后对照：各背景/扰动的细胞数、基因覆盖、control 均值、响应分位数
- [ ] 台账每步有 `why / before / after / config / artifact`
- [ ] 差异能解释：哪些是预期过滤，哪些是意外偏移
