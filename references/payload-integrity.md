# 有效载荷、索引与训练启动检查

这是 [silent-failures.md](silent-failures.md) 通用协议的文件/输入适配器，不能替代 optimizer、loss、checkpoint 或 evaluator 的相应边界检查。

用于外部 embedding、预计算特征、检索库或派生缓存进入训练，以及“加这个通道没用”的结论形成之前。文件能打开、大小/shape/dtype 正确、没有 NaN，只证明容器可读，不能证明内容完整、实体对应正确或模型实际读到了它。

维护者的故障复盘报告：文本 embedding 大部分为全零，而常规元数据检查与训练 loss 均正常。本仓库未取得原文件或下载日志；“预分配后下载中断”是待核验的候选原因，不能写成已证实根因。也不能从输入全零直接推出所有 attention 或参数梯度为零。

## 1. 四层证据与失败动作

| 层 | 检查什么 | 仍不能证明什么 |
|---|---|---|
| 产物身份与传输 | 来源版本、预期大小/shape/dtype、可信发布方 checksum 或分块完成记录 | 自算 hash 只能标识当前字节；正确复制了坏源也可能通过可信 checksum |
| 内容 | 全轴分层或全量分块检查非有限值、零行、范数、方差/常量退化及覆盖 | 非零随机数也可通过；抽样没发现异常不代表每块完整 |
| 语义与实际消费 | 有序实体 ID、lookup 映射、有效 mask、已知锚点和真实 loader 输出 | 文件全局统计不能证明 row-ID 对齐、缓存更新或任务有用 |
| 模型与贡献 | 控制随机性的有效输入干预、实际通路/更新、同协议信息消融 | 小输出变化不等于路径不存在；同样分数也可能来自冗余或统计力不足 |

**必需通道不满足契约、未完成必需检查、检查程序失败或报告无法写出时，停止依赖它的训练启动。** 可选通道仅在任务已允许的情况下显式降级，记录缺失 mask、原因与新配置；不能把失败静默变成全零输入并继续称为完整模型。

## 2. 先定义契约，不用万能稀疏度阈值

每个通道记录以下决定性信息，复用既有 schema/config：

- required/optional、产物路径与版本、生成器/encoder/预处理版本、矩阵单位、shape/dtype；
- 有序 row-ID 与模型实体映射的身份/hash，重复与缺失规则，训练实际选择的实体集合；
- 哪些零行是合法 padding/缺失，哪些行预期有观测；lookup miss、fallback、有效覆盖与 mask 的判据；
- 经来源或健康参照支持的零行比例、范数/方差范围，以及按来源/分片/seen-unseen 分组的检查；
- 审计范围、采样 seed/行数/分层、全量或可信 checksum 的要求、检查实现与 policy 版本。

稠密文本 embedding 的未写零行、稀疏计数的生物学零、padding 与有意丢弃的通道不是同一种状态。阈值由任务决定；健康范围不足时先核对来源与小规模锚点，再形成可检查条件，不编造精确“健康值”。已知故障注入应确实违反该条件。

## 3. 文件内容探针：可执行但范围明确

[scripts/audit_embedding_payload.py](../scripts/audit_embedding_payload.py) 使用 NumPy 只读 mmap 检查二维 `.npy`，支持 float16/32/64 与不超过 64 位的实整数。不自动安装依赖，不加载 pickle；其他格式需要调用项目原生 reader 的等价探针，不能改扩展名冒充 `.npy`。

示例中 `0` **仅适用于该契约明确要求每行都有有效稠密 embedding** 的情况；有合法零行时替换为有依据的上限。阈值同时应用于整体观测和每个分层，因此不是只限制全局平均：

```bash
python scripts/audit_embedding_payload.py /data/versioned/text.npy --report /runs/run-id/text-preflight.json --max-zero-row-fraction 0 --mode sampled
```

当前 CLI 不读取合法零行 mask，也不支持各组不同上限。若 10% 合法 padding 集中在尾层，不能把整体 10% 直接用作每层上限；把上限调到 100% 又会关闭对本应有观测行的零填充检查。此类任务须在宿主真实 reader 中按有效行 mask/分组契约做等价检查，并记录其身份与范围；整文件统计只能作补充，不能单独证明合法缺失与异常缺失已分开。不为迁就工具改变科学契约。

- sampled 分层覆盖全轴，记录采样设置与范围；不只读前缀。边界/随机采样也仍可能漏掉稀小损坏。
- full 使用有界分块扫描全部行；首次接入、大规模重用前或发生损坏后，按预算执行全量和来源核验。full 内容统计仍不证明内容语义正确。
- 需要检查非零常量退化时，按契约指定 `--min-feature-std`：它是各 feature 跨被检行标准差的 RMS 下限，应用于整体及至少有两条有限行的分层；单行分层不单独判方差失败，整体不足两行则无法完成该判据。不指定就只记录统计，不能声称此项已通过。
- `--hash-source` 计算本地全文件 SHA-256 用于身份/溯源；`--expected-sha256` 仅在摘要来自可信、版本匹配的来源时提供传输一致性证据。可信全文件摘要真的相符时，应检查源产物、索引或后续消费，不再把传输丢块当既定原因。
- 报告父目录须已存在，并使用唯一 run 路径。报告和退出码同时检查；失败不是警告。脚本的 `inconclusive` 对应通用协议的 unknown，不是 pass。无有效报告、检查未完成或范围不足不能记成“全文件验证通过”。脚本不读取训练 row-ID 或调用真实 loader，不代替下一节。

启动路径的最小接法是调用同一工具并传播非零退出码，而非另写一套近似检查。以下是宿主训练项目内的接入形状，`channel` 来自该项目契约，路径和其他参数按项目解析：

```python
import subprocess
import sys

command = [sys.executable, str(skill_dir / "scripts/audit_embedding_payload.py"),
           str(channel["path"]), "--report", str(run_dir / "text-preflight.json"),
           "--max-zero-row-fraction", str(channel["max_zero_row_fraction"]),
           "--mode", channel["audit_mode"]]
if channel.get("min_feature_std") is not None:
    command += ["--min-feature-std", str(channel["min_feature_std"])]
subprocess.run(command, check=True)  # 不捕获后继续，也不用 shell 分号串起训练
# 随后核验同一版本的实际 loader 与 mask，再调用项目原训练入口。
```

本 skill 仓库没有用户项目的训练器；工具已可返回阻断信号，**只有调用方真正接入并传播失败，才能阻断其训练**。故障注入测试使用假训练 marker 验证这条执行关系，不冒称真实训练器已接入。

## 4. 让“查过的文件”成为“训练实际吃到的数据”

1. 用项目真实 reader、实体映射与 transform 构造小批，覆盖常见/未见实体、各来源和合法缺失分支；记录实际 resolved path、产物版本、row-ID/映射哈希与所选范围。
2. 在变换前后分别核对几个已知锚点的 ID→row→向量关系，与已核验源比对；检查 lookup miss/fallback、全零/近零、有效 mask、通道 gate 的计数和分组覆盖。原值可受控保存或只留摘要，不把私有向量写进公共日志。
3. 检查 cache 键包含源内容版本、索引与生成/变换配置；换文件不等于旧进程、worker、内存映射或派生缓存已更新。版本不符要重建受影响缓存并重新启动相应 reader。
4. 记录真实 batch 的轻量覆盖统计，长训练按任务风险复查；任务本来允许的 dropout/稀疏度与异常缺失分开解释。分布变化先检查数据/采样，再作模型解释。

报告缓存仅在数据、row map、policy、探针实现、采样范围和 loader 配置仍匹配时复用。仅按文件名或“曾经 pass”命中缓存不够；stat 只能检测部分改动。

只读 mmap 并非不可变快照，检查之后文件仍可能被其他进程写入。使用版本化、发布后不再修改的数据，审计到训练期间无并发写者；有写入/身份变化就使检查失效。前后 stat 检查和 hash 都不能替代这一运行约束，也不能保证发现刻意保留 stat 的并发修改。

## 5. 新产物发布与事故处置

下载/生成先写临时版本，验证各块 offset/长度/完成状态、来源 checksum 和内容后，在同一文件系统按受支持的原子方式发布为新版本；不要把预分配大小或下载退出码当作完整性证明。本 skill 不实现下载器，不默认重下或覆盖已有数据。

发现静默退化后：

1. 保留坏件的身份、报告和相关日志，隔离其后续使用；修复件新版本发布，原始坏件不覆盖。
2. 沿 `artifact → row map/cache → consumed rows → run/checkpoint → claim` 追踪影响；区分已确认、可能影响和未见证据。训练 loss 正常不能洗清故障，也不能仅凭单个坏源宣布两周所有实验全部无效。
3. 修复后重验源、索引、派生产物和实际 loader。固定模型代码、配置、切分、seed 与可比预算，做数据修复前后对照；数据改变后更新轨迹自然可能变化，不能要求逐步权重一致。
4. 先在验证协议下复核与该通道相关的结论，再决定重跑其他实验。一次修复对照不测出“真实性能上限”，也不自动证明或否定此前所有架构方法；最终测试边界仍服从任务契约。

## 6. 故障注入与证据边界

本仓库提供的自动测试使用合成数据，重点验证：健康矩阵可过；shape/dtype/大小相同但前缀正常、尾部/局部成块为零可被拒绝；非有限值、契约禁止的零行、配置错误或检查失败不触发假训练。全非零常量由相应显式方差判据检查。

生产项目还应增加“索引置换、旧 cache、全 lookup miss、mask 全关闭”的真实 loader 故障注入。它们可能保留文件所有统计，**本文件探针不会自动检测**。参见 [diagnostics.md](diagnostics.md) 和 [htca-bboc.md](htca-bboc.md) 的通路与归因检查。

## 7. 可复用的设计来源

- [NumPy load](https://numpy.org/doc/stable/reference/generated/numpy.load.html) 与 [open_memmap](https://numpy.org/doc/stable/reference/generated/numpy.lib.format.open_memmap.html)：有界读取与显式只读模式；不承诺文件成为快照。
- [TensorFlow Data Validation](https://www.tensorflow.org/tfx/data_validation/get_started)：将 schema 期待与内容统计/异常分开；这里借用设计，不要求安装 TensorFlow。
- [ML Metadata](https://www.tensorflow.org/tfx/guide/mlmd)：记录产物、执行与依赖谱系；本仓库以普通配置和 JSON 实现最小溯源，不引入元数据库。
