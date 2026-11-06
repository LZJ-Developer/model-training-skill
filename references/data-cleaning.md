# 数据清理

> 清洗是**受控变换**，不是修饰。完成标准是"输出满足一个写明的契约 + 审阅者能复现"，
> 不是"还剩多少行"。下面只写模型默认假设会出错、且会改变结论的地方。

## 1. 表示无法从数值推断

- 整数样 ≠ raw counts；`.raw` 在多数流程里存的是 **log-normalized**，名字不保证来源。
- 二次归一化不报错，只改分布。判断依据：列和、分位数、`uns['log1p']`、是否有独立 counts 快照。
- 未测 / 缺失 / 不适用 / 未采集 / 被扣留是五种状态；合并成 0 或 NaN 会改变下游。
- 解析失败要计数并暴露；静默变 null 会把数据错误伪装成缺失。

## 2. 等价类决定泄漏

- 判重、留出、检索的键是**生物学实体**：别名、CVCL vs 名称、注释版本、symbol vs Ensembl。
- 字符串匹配"通过"不等于无泄漏；等价类错了，检查越严格越危险。
- 归一化、插补、去重规则只在允许的训练分区上拟合，否则本身就是泄漏。

## 3. 阈值是局部量

- 全局 QC 阈值会被最大批次绑架；MAD 在 <50 细胞的组里趋近 0，会把半数判成离群。
- MT 阈值随组织/细胞类型变（常用 8%，神经元可到 15%）；一套阈值套所有背景是错的。
- 双细胞会混合两个扰动；guide 唯一性通常比 Scrublet 更直接。
- 异常可能是真实事件，不是自动修复的许可；先取证，再决定。

## 4. 聚合与 DE

- pseudobulk 用 **raw counts 求和**；均值再 log 会改变均值-方差关系。
- 独立重复是供体/文库，不是细胞；细胞数只买精度。
- `rank_genes_groups` 的 p 值不是严格的组间 DE；条件 DE 需按样本聚合 + 独立重复 + 全秩设计。
- 聚合不能修复混杂、缺重复、双细胞或错误注释。

## 5. 变换改的对象不同

- Harmony 改 PCA 坐标、BBKNN 改图、ComBat 改 X；DE 必须回到原始 counts。
- HVG flavor 依赖表示：seurat/cell_ranger 用 log-normalized，seurat_v3 用 counts；混用会选到不同基因。
- 子集化会剪 layers，但通常不剪 `.raw`；聚合前保留全基因对象。
- 归一化要在子集化之前，否则 size factor 变了。

## 6. 扰动项目的特有泄漏

- 同一 `(细胞系, 扰动)` 跨语料重复 → 训练/验证必须互斥。
- "未见" = **所有**训练语料都未测；只对某一语料未见、但 donor 有测量，测的是检索，不是泛化。
- bulk 语料的 `fdr` 可能来自 z 近似（本项目的 GWPS 构建即是），不是逐细胞 Wilcoxon；两者不能混比。
- 控制细胞复用会放大 control 权重。
- 尺度漂移：多语料平均 |Δ| 可差 3 倍、on-target 可差 16 倍；系数只在训练折估计。
- donor evidence 必须来自 train 且不是 eval 行；别名归一后要再查一次。

## 7. 工具边界

| 需要 | 上游 skill | 边界 |
|---|---|---|
| 单细胞检查、QC、pseudobulk | [scanpy（K-Dense）](https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/skills/scanpy/SKILL.md) | 全流程含 HVG/聚类；训练清洗只取所需步骤 |
| 轻量 QC / MAD | [single-cell-rna-qc（Anthropic）](https://github.com/anthropics/knowledge-work-plugins/blob/main/bio-research/skills/single-cell-rna-qc/SKILL.md) | 双细胞、ambient RNA 只给建议，未内置 |
| 表格 / 元数据审计 | [data-cleaning（magnus919）](https://github.com/magnus919/agent-skills/blob/main/data-cleaning/SKILL.md) | 不处理 `.h5ad`；负责契约、日志、前后核对 |
| LLM / 多模态语料 | [Data-Juicer Harness](https://github.com/datajuicer/data-juicer-agents/blob/main/skills/data-juicer-agents-harness/SKILL.md) | 执行靠底层运行时；装 skill ≠ 装环境 |
| 标签审计 | [cleanlab（AREX）](https://github.com/VectorSpaceLab/AREX-Skill/blob/main/skills/repositories/repo-skills/cleanlab/SKILL.md) | 需要可信的样本外预测，不能把模型错误当脏数据 |

NeMo Curator 的示例仍用 `DocumentDataset`、Dask 等已移除接口（后端已迁 Ray），先不用。
选择工具前先查 API 生命周期与版本：skill 是入口，执行靠库。

## 8. 完成判据

- 输出 + 决策日志 + 前后对照 + 溯源 + 未决项；原始数据只读；审阅者能复现。
- 语义不确定时停在 quarantine / escalation，不发明值。
- "干净" = 通过写明的契约，不是没有行剩下。
