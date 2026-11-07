# 数据清理

清理是受控变换：输出满足写明的数据契约，并保留原始数据、决策日志和前后对照。扰动项目先读 [任务与评测契约](perturbation-evaluation.md)，再决定哪些变换有意义。

## 1. 先确认表示与来源

- 整数样数值不能证明是 raw counts；`.raw` 只是赋值时 `X` 与 `var` 的快照，可以是 counts，也可以是变换后的数据。记录来源、矩阵层、单位、基因注释版本、归一化和 log 顺序，不能根据名称猜来源。[AnnData 官方定义](https://anndata.readthedocs.io/en/stable/generated/anndata.AnnData.raw.html)
- 列和、分位数、`uns['log1p']` 和 counts 快照可帮助检查，不能单独证明完整变换历史。来源不明时保留不确定性，避免二次归一化。
- 未测、缺失、不适用、未采集、被扣留不等于零效应；解析失败计数并暴露。`tested` 掩码覆盖损失和相关统计，全掩码样本在求均值前处理。
- 保留原始对象及有序 gene/cell ID；AnnData 按细胞切片会切 `.raw`，按基因切片通常不会，不能假定各层自动同序同维。

## 2. 预处理服从使用时的信息边界

- 别名、CVCL/细胞系名称、symbol/Ensembl、化合物标识和注释版本先映射到可审计的等价类，再判重与留出。不同字符串可能指向同一实体；同一名称也可能来自不同实验。
- HVG、缩放、插补、批次整合、目标分箱等表示变换，写清 `fit` 使用的分区和 `transform` 对象。严格归纳评测不使用验证/最终测试的响应拟合这些表示。校准参数另按任务契约的开发校准分区估计，并将拟合分数与独立验证分开，见任务与评测契约 §3。若任务允许目标背景 control 或无标签适配，单列其可见范围并向所有比较方法开放。
- 每细胞固定总量归一化可独立作用于允许输入，不等于在全体数据上拟合共享参数；但 `target_sum=None`、高表达基因排除等设置可能依赖集合统计。保存 size factor 的基因范围、参考总量与计算范围。[Scanpy normalize_total](https://scanpy.readthedocs.io/en/stable/generated/scanpy.pp.normalize_total.html)
- 先按全基因总量归一化再选 HVG，适合保留原库大小定义的流程；其他顺序需明确目标，不能把“必须先归一化再子集”当普遍定律。HVG flavor 的输入要求不同：`seurat`/`cell_ranger` 接收 log 数据，`seurat_v3` 系列接收 counts。[Scanpy HVG](https://scanpy.readthedocs.io/en/stable/generated/scanpy.pp.highly_variable_genes.html)

## 3. QC 是判断，不是固定阈值清单

- 按实验/批次并结合细胞类型查看 counts、检测基因数、线粒体比例等联合分布；阈值需要组织、建库技术与实验目的依据。小组的 MAD 可能不稳定，离散值或大量并列值可使 MAD 为零，但不存在“少于 50 个细胞必失效”的定律。遇到退化分布时检查原始值、合并有依据的参考组或人工复核，并记录处理方式。
- 不固定通用 MT 百分比，也不把所有高 MT/低 RNA 细胞直接删掉。扰动可能真实改变细胞存活、大小或转录活性；报告各条件过滤比例及敏感性，避免把效应清掉。[单细胞 QC 方法说明](https://www.sc-best-practices.org/preprocessing-visualization/quality-control/)
- guide assignment 解决扰动身份，多 guide 既可能是设计的组合也可能提示混样；单 guide 不能排除同 guide 双细胞、未捕获 guide 或其他 multiplet。结合建库信息、表达双细胞检测和需要时的 ambient RNA 审计；guide 唯一性不能替代完整 doublet 检查。
- 纠正批次效应前确认批次是否与条件混杂；混杂不能靠算法自动恢复成实验对照。QC 差异和异常标签先取证，不能因模型预测不准就改标签。

## 4. 聚合、DE 与独立单位

- 若使用 count-based pseudobulk DE，通常在生物学重复内按 cell type/condition 对 raw counts 求和，再按所用模型处理库大小和设计矩阵。预测目标若是平均表达，均值聚合可以正当；它回答的问题与 count-based DE 不同。
- 独立单位由取样与干预设计决定，可能是 donor、动物或独立培养重复；技术文库及监督行不自动成为独立重复。多行可能共享 donor、control 或同一实验，需保留这些依赖关系。聚合不能修复缺重复、混杂或错误注释。
- DE 输入由方法决定，不是一律回到 raw counts。`rank_genes_groups` 期待 log 数据，主要用于组标记排序；不能忽略生物学重复后把逐细胞检验的 p 值当跨 donor 条件效应证据。整合坐标/校正矩阵也不能未经模型检查就替代 DE 输入。[Scanpy 文档与警告](https://scanpy.readthedocs.io/en/stable/generated/scanpy.tl.rank_genes_groups.html)
- 记录 DE 的输入层、重复单位、设计、检验、效应量与多重校正；不同语料的 z 近似、Wilcoxon、count-based DE 的 FDR 不能只因列名相同就混为一种证据。[生物学重复与假阳性实证](https://www.nature.com/articles/s41467-021-25960-2)

## 5. 扰动语料的额外检查

- 对跨语料重复实验、背景/扰动等价类、响应派生 evidence 和预训练覆盖做审计；“重复实体”和“重复测量”分别记录。合法外部 PPI、序列或已知靶点不自动构成泄漏，关键是其来源及部署时是否可得，详见任务契约。
- 多个 perturbation 复用同一 control 会产生相关效应估计；保存 control pool ID、匹配规则及权重，不能按复制次数增加独立样本量。
- 混训前检查各语料单位、效应幅度、敲低效率和 tested 范围。尺度校准只能使用契约允许的信息，并保留未校准对照，避免把真实生物效应强行拉齐。

## 6. 工具选择与验收

按任务选库：单细胞对象/QC 用 Scanpy 等经验证工具；表格/元数据用可追溯的校验与映射；大语料清理另选相应运行时。外部 skill 只提供操作指导，不能代替依赖安装、版本核对或数据审计；不默认下载或安装其执行环境。

交付清理后的数据引用、schema/表示说明、有序 ID 与 hash、分区清单、各条件过滤前后数量、拟合范围、决策日志和未决项。原始数据只读；关键语义无法确定时隔离受影响部分，不能发明数值补齐。
