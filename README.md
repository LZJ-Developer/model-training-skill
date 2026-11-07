# model-training

数据受限的生物医学模型训练与调试 skill，覆盖任务定义、数据审计、训练验证、实验比较、恢复与报告。当前重点场景是虚拟细胞扰动预测，特别是**未见细胞背景、未见扰动，以及两者同时未见**时的泛化。

流程围绕可验证的问题组织：部署时究竟能看到什么、评测是否模拟了该条件、模型是否学到了超过简单基线的信号。小模型和冻结主干适配器是可选策略，不由监督行数自动决定。未来 AI + 医疗任务需先定义自己的任务契约和验证方案；扰动预测中的经验不自动构成其他医疗任务的证据。

这是中文指令包，安装本身不安装 Python 库、不启动训练，也不修改全局环境。实际执行仍受宿主工具、数据权限和任务预算约束。

## 内容与按需读取

仓库根目录就是 skill 目录，`SKILL.md` 与 `references/` 必须一起保留。

```text
model-training/                 # 安装后的目录名
├── SKILL.md                    # 范围、核心流程与路由
└── references/
    ├── data-cleaning.md        # 表示、QC、聚合、切分与泄漏
    ├── diagnostics.md          # 数值、梯度、指标与复现排障
    ├── recipes.md              # 有条件的超参、容量与预算建议
    ├── run-artifacts.md        # 任务卡、溯源、比较与恢复记录
    ├── perturbation-evaluation.md # 未见背景、未见扰动与双未见协议
    ├── htca-bboc.md            # 可选研究视角及其边界
    └── case-study-perturbation.md # 脱敏案例与证据限制
```

入口只提供共享约束；清洗、排障或实验报告时再读取对应 reference。脱敏案例用于说明推理过程，其中维护者报告的结果与可公开核验的证据分别标注，不能将案例数值视为新任务的性能保证。

## 安装

以下是手动安装说明，默认不执行。安装者应先审阅拟使用的提交。**每个宿主选择一个作用域**，避免项目级和用户级同时安装同名 skill。

| 宿主与作用域 | 目标目录 |
|---|---|
| Codex 项目级（优先用于具体训练项目） | `<训练项目>/.agents/skills/model-training` |
| Codex 用户级（可选，跨项目） | `~/.agents/skills/model-training` |
| Claude Code 项目级 | `<训练项目>/.claude/skills/model-training` |
| Claude Code 用户级（可选） | `~/.claude/skills/model-training` |

上述 Codex 路径来自[官方本地 skill 文档](https://learn.chatgpt.com/docs/build-skills)，Claude Code 路径来自[官方 skills 文档](https://code.claude.com/docs/en/skills)。历史版本或部分工具使用 `~/.codex/skills`，应按实际宿主版本核验，不应在两个用户目录重复安装。

### Windows PowerShell：Codex 项目级

从**训练项目根目录**运行。先把 `REVIEWED_COMMIT_SHA` 替换为已经审阅的完整提交 SHA；`--no-checkout` 避免先把浮动分支内容装成可发现的 skill。

```powershell
$skillTarget = '.agents/skills/model-training'
$skillCommit = 'REVIEWED_COMMIT_SHA'
if ($skillCommit -notmatch '^[0-9a-fA-F]{40}$') { throw '先填写已审阅的完整提交 SHA' }
if (Test-Path -LiteralPath $skillTarget) { throw '目标已存在，先检查已有安装' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $skillTarget) | Out-Null
git clone --no-checkout https://github.com/LZJ-Developer/model-training-skill.git $skillTarget
if ($LASTEXITCODE -ne 0) { throw '克隆失败，停止安装' }
git -C $skillTarget checkout --detach $skillCommit
if ($LASTEXITCODE -ne 0) { throw '指定版本载入失败，停止安装' }
git -C $skillTarget rev-parse HEAD
$skillEntry = Join-Path $skillTarget 'SKILL.md'
if (-not (Test-Path -LiteralPath $skillEntry -PathType Leaf)) { throw '缺少 SKILL.md' }
$skillLinks = [regex]::Matches((Get-Content -LiteralPath $skillEntry -Raw -Encoding UTF8), '\]\((references/[^)#]+)(?:#[^)]*)?\)')
foreach ($skillLink in $skillLinks) {
    $referencePath = Join-Path $skillTarget $skillLink.Groups[1].Value
    if (-not (Test-Path -LiteralPath $referencePath -PathType Leaf)) { throw "缺少引用：$referencePath" }
}
```

目标目录已存在时，应停下检查已有安装，不覆盖或另叠一层目录。用户级可将 `$skillTarget` 改为 `Join-Path $HOME '.agents/skills/model-training'`；Claude Code 则将 `.agents/skills` 换为 `.claude/skills`。这些设置是替代选项，无需逐一执行。

### 已有本地克隆

应复制**包含根 `SKILL.md` 的完整目录**到所选目标，而非寻找仓库中不存在的 `model-training/` 子目录。例如源目录可名为 `model-training-skill`，复制后的目标目录名为 `model-training`。先确认源提交和本地差异；含 `.git` 的副本若需提交进训练项目，应明确选择普通文件快照、submodule 等版本管理方式，避免无意嵌套 Git 仓库。

### 验证发现与调用

先确认目标目录中存在 `SKILL.md` 与所有相对引用。在宿主的 skill 列表/选择器中确认 `model-training` 可见；Codex 可显式使用 `$model-training`，Claude Code 可使用 `/model-training`。若未出现，按对应宿主文档检查作用域、版本与配置，必要时重启会话。[Codex 调用与发现](https://learn.chatgpt.com/docs/build-skills)、[Claude Code 调用与发现](https://code.claude.com/docs/en/skills)。

首次可只要求审查一张任务卡，确认其选择了相关 reference，并且没有自动安装依赖或启动训练。安装成功只证明指令被发现，不证明训练环境可用或科学判断正确。

### 更新与回退

记录安装路径、完整提交 SHA，以及保留的本地修改。更新前先检查 `git status`，保留旧 SHA 和本地修改，再获取候选提交，比较 `SKILL.md` 与 `references/` 的差异；审阅通过后才切换到新的固定 SHA，并重新验证发现与一个真实用例。不要对脏工作区强制 checkout、reset 或覆盖复制。

回退时，在确认没有待保留修改后切回旧 SHA；若使用文件快照，则从放在 skill 搜索目录之外的备份恢复。停用时可将该目录移到搜索路径之外，保留文件以便恢复；不需要修改全局环境或删除训练数据。

## 来源与维护

流程设计参考 [wassname/ml-debug](https://github.com/wassname/ml-debug)、[K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills) 与 [Orchestra-Research/AI-Research-SKILLs](https://github.com/Orchestra-Research/AI-Research-SKILLs)；清洗部分还参考 [Anthropic bio-research skills](https://github.com/anthropics/knowledge-work-plugins) 与 [magnus919/agent-skills](https://github.com/magnus919/agent-skills)。这些链接用于追溯思路，不代表其当前 API、默认值或规则适用于所有任务。

扰动案例来自维护者使用 [PIE](https://github.com/ArcInstitute/pie) 式模型的脱敏工作记录。上游仓库不能替代本地实验的证据；引用与复用需保留案例中的适用条件、来源状态和未决项。后续修改应由真实使用中的失败或行为验证驱动，优先收窄不成立的规则。

## License

MIT，见 [LICENSE](LICENSE)。上游项目采用各自许可证；本仓库未附带其运行时代码。
