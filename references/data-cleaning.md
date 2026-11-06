# 数据清理

> 目标不是"把数据洗干净"，而是产出**可审计的清洗证据**：每一步做了什么、为什么、前后差多少。
> 没有台账的清洗 = 静默混杂；它不会让 loss 变难看，只会让所有结论失去归因。

## 0. 随包脚本

`scripts/sc_clean.py` 是本文件的可执行版本，三个子命令：

| 命令 | 做什么 | 保证 |
|---|---|---|
| `inspect` | 形状、X 类型/分布、layer、obs/var、分组大小 | 只读，不改对象 |
| `qc` | counts 校验 → MT/ribo/HB → 逐批 MAD → 写 `pass_qc` / `qc_reason` | 不删行；不覆盖原对象；写 `qc_summary.json` |
| `pseudobulk` | 按 `--by` 求和原始 counts，`--min-cells` 过滤 | 元数据必须组内常量；不改源对象 |

依赖 anndata、numpy、scipy；只有 `qc --scrublet` 需要 scanpy。每次运行用 `--ledger` 追加一行台账。

整合自 K-Dense `scanpy`（BSD-3-Clause）与 Anthropic `single-cell-rna-qc`（Apache-2.0）：
保留 counts 校验、MAD 离群与 pseudobulk 聚合；改为**只标记不删除**、**逐批 MAD**、
**默认不依赖 scanpy**。上游的 HVG、聚类、注释、绘图不在本脚本内，需要时用原 skill。

## 何时读这个文件

- 接入新语料，或在训练前走 G0 数据契约
- 指标异常，怀疑是数据而不是模型
- 要对外发布数据/模型，需要一份能复查的清洗记录
- 有人问「这批数据洗过吗」而答案只有「应该洗过」

---

## 1. 先定「单位」和「表示」

清洗的顺序错了会返工：先确认每个矩阵是什么，再谈过滤。

| 单位 | 是什么 | 常见误用 |
|---|---|---|
| **cell** | 一个细胞（单细胞计数矩阵的行/列） | 把 pseudobulk 行当成细胞 |
| **sample / construct** | 一个文库或一个 construct 的汇总 | 把 construct 数当成独立样本数 |
| **row** | 一个监督单元（如 context × perturbation） | 用细胞数算参数/样本比 |
| **gene** | 一个基因轴上的坐标 | 混用 symbol 与 Ensembl |

| 表示 | 判别 | 规则 |
|---|---|---|
| raw counts | 非负整数、总量差异大 | 唯一适合做 DE / 归一化的输入 |
| CP10k / CPM | 每列和约为 1e4 / 1e6 | 已归一化，**不能再 normalize 一次** |
| log1p | 有负值或小数值、max 远小于总量 | 已是变换空间，比较前确认底数（ln vs log2） |

三条硬规则：

1. **先记录、再转换。** 原始对象只读，派生对象单独落盘；转换只允许一个入口函数。
2. **禁止二次归一化。** 拿到别人预处理过的矩阵，先抽查列和/分位数，判断它是否已经归一化。
3. **未测 ≠ 零。** 「没有测量」与「测量为 0」必须用掩码区分；任何损失项、聚合统计都要乘掩码，并断言掩码内样本数。

---

## 2. 五层检查（按依赖顺序）

| 层 | 要回答 | 典型失败 |
|---|---|---|
| **L1 表示与单位** | 这是 counts 还是 log1p？单位是细胞还是行？ | 二次归一化；把 bulk 汇总当单细胞 |
| **L2 键对齐** | gene ID、guide、perturbation、context、batch、control 是否同一套键？ | symbol/Ensembl 混用；别名（K562 vs k562_gwps、CVCL vs 名称） |
| **L3 测量 QC** | 深度、检测基因数、线粒体比例、双细胞；每个细胞留下/剔除的理由 | 统一阈值套在多背景上；只留过滤后矩阵，无法复查 |
| **L4 切分完整性** | 同一测量是否跨 split？同一实体的别名是否绕过判重？ | 行级随机切分；按字符串判重，别名漏网 |
| **L5 前后验收** | 各背景/扰动的细胞数、基因覆盖、control 均值、响应分布变了多少？ | 只报"清洗完成"，没有 before/after 对照 |

L4 的判重键必须是**生物学实体**，不是数据集里的字符串：`(细胞系, 扰动)` 在规范化别名之后比较。
同理，「未见」要定义成「在**所有**训练语料里都没有测量」，而不是「不在某一个语料里」。

---

## 3. 工具路由

本包已把与训练数据清洗相关的部分整合为 `scripts/sc_clean.py`；下表保留完整工具的边界，
需要 HVG / 聚类 / 注释 / 绘图时再用上游 skill。各 skill 已核对 `SKILL.md`、配套脚本与依赖，
**未安装、未运行**。skill 是流程与入口，真正的
数据处理由底层库或脚本完成；安装 skill 不等于装好运行环境。

| 数据 / 任务 | 首选 | 提供什么 | 边界 |
|---|---|---|---|
| 单细胞 `.h5ad` 的检查、QC、pseudobulk | [scanpy（K-Dense）](https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/skills/scanpy/SKILL.md) | counts 合法性检查、QC、可选 Scrublet、预处理、pseudobulk 脚本 | 全套流程含 HVG、聚类等分析步骤；训练清洗按需调用，不要整条跑 |
| 轻量单细胞 QC | [single-cell-rna-qc（Anthropic）](https://github.com/anthropics/knowledge-work-plugins/blob/main/bio-research/skills/single-cell-rna-qc/SKILL.md) | MAD 异常值过滤、线粒体比例、过滤前后图、保留 QC 标记 | 双细胞检测与 ambient RNA 去污染只是建议，未内置执行 |
| 表格 / 元数据 / JSONL 的画像与审计 | [data-cleaning（magnus919）](https://github.com/magnus919/agent-skills/blob/main/data-cleaning/SKILL.md) | 缺失/重复/类型检查、清洗前后核对、转换日志、数据契约与报告模板 | 不直接处理 `.h5ad`；单细胞清洗仍需领域工具 |
| LLM / 多模态训练语料 | [Data-Juicer Agents Harness](https://github.com/datajuicer/data-juicer-agents/blob/main/skills/data-juicer-agents-harness/SKILL.md) | `djx tool` 查数据、查算子、生成并验证处理计划、执行过滤/去重/转换 | 执行能力来自 `data-juicer-agents[harness]`；装 skill ≠ 装好运行时 |
| 监督标签审计 | [cleanlab（AREX-Skill）](https://github.com/VectorSpaceLab/AREX-Skill/blob/main/skills/repositories/repo-skills/cleanlab/SKILL.md) | 标签质量、异常样本、近重复、多标注者工作流 | 第三方封装；标签错误检查需要可信的样本外预测，不能把模型预测错误当脏数据 |

**不直接采用**：[NeMo Curator skill](https://github.com/Orchestra-Research/AI-Research-SKILLs/blob/main/05-data-processing/nemo-curator/SKILL.md)
的示例仍用 `DocumentDataset`、Dask 等旧接口，而 NVIDIA 当前文档已移除这些 API、后端迁到 Ray；
要配合最新版运行需先更新 skill 或固定匹配的旧版本。语料清洗现在优先 Data-Juicer。

**最小组合**：单细胞项目用 `scanpy` + `data-cleaning`（记录与验收），MAD 过滤补 Anthropic 的
`single-cell-rna-qc`；LLM 语料用 Data-Juicer；标签审计用 cleanlab。三组任务不要互相替代。

---

## 4. 两个会改变执行方式的陷阱

1. **多个背景混在同一个 `.h5ad`。** `--batch-key` 在 scanpy skill 里用于 Scrublet，**不会**自动按
   背景分组选择常规 QC 阈值；常规 QC 仍是统一阈值。需要按技术批次在外层分组，或改用 MAD 自适应过滤。
2. **要保留全转录组输出。** 不要直接跑默认的 HVG 子集化流程。保留完整基因对象，把分析用的特征子集
   单独保存；否则下游再也拿不回未选中的基因。原始 counts、完整基因快照、子集化对象是三份东西。

---

## 5. 清洗台账（每一步一行）

任何会改变行数、列数、取值分布的操作，都写一行。格式固定，便于机器汇总：

```
step:    <操作，如 drop low-quality cells / map Ensembl→symbol / dedupe constructs>
why:     <触发条件与依据>
before:  <行数、细胞数、基因数、背景数>
after:   <同上>
config:  <阈值、映射表版本、脚本 commit>
artifact:<输出路径、校验和>
```

三条规则：

- **可逆性**：原始对象不被覆盖；过滤只写标记列（`keep`、`reason`），不直接删行。
- **可复现**：阈值与映射表版本写进配置，不写死在脚本里。
- **可验收**：L5 的前后对照表与台账一起交付，而不是二选一。

---

## 6. 扰动响应 / 虚拟细胞项目的专用规则

这些是单细胞 QC skill 不覆盖、但会直接改分数的部分。

- **重复测量**：同一 `(细胞系, 扰动)` 在两个语料里都出现时，一边训练一边验证 = 在训练集上评测。
  审计只做字符串匹配会漏；必须按规范化实体比较，并把结果写进切分断言。
- **别名**：`k562` 与 `k562_gwps`、`CVCL_xxxx` 与细胞系名，是同实体的不同键。判重、留出、证据检索
  都要先做别名归一，否则检查会"通过"而泄漏仍在。
- **bulk 与单细胞不可混比**：construct-level pseudobulk 的 `fdr` 常用 z 检验近似，不是逐细胞
  Wilcoxon；这类行的 DE 标签与单细胞语料不同源，不能直接合并统计。
- **控制细胞复用**：同一批 control 被两个 split/context 重复计数时，控制均值与方差的权重会被放大；
  按供体/批次切分，或显式合并控制。
- **尺度漂移**：多语料的平均 |Δ| 与 on-target 强度可能差一个数量级。对齐系数只在训练折上估计，
  测试背景不参与；否则这一步本身就是尺度泄漏。
- **证据通道**：如果模型使用跨背景 donor evidence，检查 donor 必须来自 train、且不得是 eval 行；
  这只拦住"同一键"，拦不住别名——别名归一后要再查一次。

### 6.1 常见生物数据陷阱

这些是"看起来洗过了、其实没洗"的高频项。**检测**指能写成断言或统计量，**预防**指流程上的动作。

| 陷阱 | 典型表现 | 检测 / 预防 | 所在层 |
|---|---|---|---|
| **环境 RNA（soup）** | 低复杂度文库、空滴、外源基因广泛低表达 | 用 SoupX/CellBender 估计污染比例；标记而非默认删除；污染比例高的样本单独审 | L3 |
| **双细胞 / 多细胞** | 一个 barcode 两个 guide、表达谱介于两类之间 | Scrublet 或 guide 共现检测；扰动筛选中双细胞会混合两个扰动，优先按 guide 唯一性过滤 | L3 |
| **guide 分配歧义** | 一个细胞多个 guide / 无 guide | 只保留单一 guide 或高置信分配；记录被丢弃的比例；non-targeting 单独成组 | L2/L3 |
| **on-target 效率差异** | 同一基因的 KD 效率从 20% 到 90% | 用 self-KD（靶基因自身表达下降）分层；低效率行不当成"零效应"，也不与高效率行等权 | L2/L5 |
| **CRISPR 模态混淆** | KO / CRISPRi / CRISPRa 的效应方向与幅度不同 | 模态作为独立字段；不跨模态混训；跨模态只用共享响应基 | L2 |
| **对照定义不一致** | non-targeting / untreated / DMSO 混用 | 每个语料显式写 control label；对照不同源的语料不能直接合并 control 均值 | L2 |
| **伪重复（pseudoreplication）** | 把每个细胞当独立重复算 p 值 | DE 的重复单元是供体/文库/样本，不是细胞；细胞数只影响精度，不增加独立样本量 | L4 |
| **批次与技术混杂** | donor、lane、化学版本与扰动相关 | 按批次分层或配对；把 batch 当协变量而不是当噪声；检查 batch×pert 的交叉表 | L4 |
| **归一化顺序** | 先 log 再 CP10k、或对已归一化矩阵再 normalize | 只允许一条入口：raw counts → size factor → CP10k/CPM → log1p；抽查列和与分位数确认输入状态 | L1 |
| **基因 ID / 版本漂移** | Ensembl 版本不同、symbol 别名、重复 symbol、PAR/MT 基因 | 固定注释版本；symbol→ID 映射表带版本号；重复 symbol 显式聚合并记录规则 | L2 |
| **低细胞数行** | 每个 (背景, 扰动) 只有 2–30 个细胞 | 设 `min_cells`（如 30）；不足的行标记而不是静默保留；下游按 `n_cells` 加权 | L3/L5 |
| **效应量尺度不一致** | log2FC 与 log1p 均值差混用；语料间差 3–16 倍 | 每个语料记录效应量定义；对齐系数只在训练折估计；不同定义不直接比大小 | L1/L5 |
| **细胞周期 / 状态混淆** | 扰动改变增殖，被读成转录响应 | 回归掉 cell-cycle score 或作为协变量；检查处理组与对照的周期分布 | L3/L4 |
| **选择 / 存活偏倚** | 强 KD 致死，只测到存活细胞 | 报告每组细胞数与存活率；强效应基因单列；不要用"测到的细胞"代表全群体 | L3/L5 |
| **剂量 / 时间缺失** | 同一药物不同 dose 被当成同一条件 | 扰动键必须含 dose 与 time；不同 dose 是不同条件，不能合并 | L2 |
| **供体 / 个体泄漏** | 同一供体的细胞跨 train/val；原代细胞跨个体 | 按供体切分；split 的判重键包含 donor | L4 |

强制与建议分开：**L1 的归一化顺序、L2 的键对齐、L4 的判重键、L5 的前后对照**应写成断言；
其余项至少写进数据契约，并在台账里记录是否检查过。

---

## 7. 验收清单

- [ ] 每个矩阵的表示（counts / CP10k / log1p）与单位（cell / sample / row）已写明
- [ ] 原始对象只读；过滤只写标记列；派生对象有校验和
- [ ] 键对齐表存在（基因轴、ID 体系、别名映射版本）
- [ ] 每个 split 的判重键是生物学实体；「未见」按所有语料定义
- [ ] 清洗前后对照表：各背景/扰动的细胞数、基因覆盖、control 均值、响应分位数
- [ ] 台账每一步都有 `why / before / after / config / artifact`
- [ ] L5 的差异能解释：哪些是预期过滤，哪些是意外偏移
