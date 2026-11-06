# model-training

> A training-methodology skill for Claude Code, Codex and other agents that load `SKILL.md`.
> It answers three questions in order: **is this change worth training at all**, **what must be
> checked before the run**, and **what to do when the numbers look wrong**.
>
> 一套用于「冻结主干 + 小适配器 / 小模型」场景的训练方法论 skill：训练前判断改动值不值得训，训练前定好检查与预测，出问题时按症状排障。

All instructions in `SKILL.md` and `references/` are written in Chinese. The structure, the diagnostic
procedures and the failure playbook are domain-agnostic; only one reference file is a worked case study.

---

## Why this exists

Most training guides tell you how to train. This one is organised around **when to stop and check**,
because the expensive mistakes are not bad hyperparameters — they are:

- training a model that cannot, in principle, use the information you added;
- training on data that already contains the answer;
- improving a composite score whose entire gain comes from one component you do not care about;
- comparing two arms whose difference is inside the seed noise.

Every gate below is cheap (minutes to zero GPU hours) and has an explicit stop condition.

---

## Structure

```
model-training/
├── SKILL.md                                 # loaded on every invocation — keep it cheap
└── references/                              # loaded on demand, routed by symptom
    ├── diagnostics.md                       # NaN / spike / plateau / leakage / null controls
    ├── recipes.md                           # hyperparameters, budgets, param/sample ratio
    ├── run-artifacts.md                     # run.md, MENTAL_MODEL.md, option & prediction tables
    ├── htca-bboc.md                         # the two judgement frameworks, with their limits
    ├── data-cleaning.md                     # cleaning ladder, tool routing, cleaning ledger
    └── case-study-perturbation.md           # a full worked case with measured numbers
```

`SKILL.md` is 215 lines. Everything heavier lives in `references/` and is routed by a symptom table,
so the skill stays cheap to load and deep when you need it.

---

## What is in `SKILL.md`

**Before training — two judgement frameworks**

- **HTCA** (four axes): *H* what relations can be expressed · *T* whether training rewards the
  behaviour you deploy · *C* the compute/implementation choice · *A* whether the model can decide to
  acquire information. Most "architectural innovations" are C-axis changes described in H-axis language.
- **BBOC** (three questions): can information enter at all (`∂ŷ/∂x ≡ 0` means the edge does not
  exist, and no amount of compute creates it) · can it flow internally (bandwidth, not just edges) ·
  is the training task isomorphic to deployment.

**Four checks, ordered by cost**

| gate | check | pass condition |
|---|---|---|
| G0 | data contract: masks, split unit, scale, bit-exact rerun | same seed twice → identical metrics |
| G1 | overfit one batch, all regularisation off | train loss → ~0, and random labels fail |
| G2 | beat the trivial baseline (mean + closed-form ridge) | otherwise stop tuning |
| G3 | only now escalate compute | — |

**Data cleaning (the execution layer of G0)**

- Five layers: representation & unit → key alignment → measurement QC → split integrity →
  before/after verification. Duplicate keys are biological entities, not dataset strings.
- Tool routing, with boundaries: `scanpy` (single-cell), `single-cell-rna-qc` (MAD QC),
  `data-cleaning` (tabular audit trail), Data-Juicer (LLM corpora), cleanlab (labels).
  NeMo Curator is listed as not adopted — its example APIs are stale.
- Every cleaning step writes a ledger row (`step / why / before / after / config / artifact`);
  cleaning without a ledger is a silent confound.

**Then**: run-record format, training-loop fixed items, tuning order, anti-patterns, routing.

---

## Install

### Claude Code — project level

```bash
mkdir -p .claude/skills
cp -R model-training .claude/skills/
```

### Claude Code — user level

```bash
mkdir -p ~/.claude/skills
cp -R model-training ~/.claude/skills/
```

Restart the session (or run `/reload-skills`). The skill appears as `model-training`; invoke it with
`/model-training`.

Copy the **whole folder**, not just `SKILL.md` — the references are part of the skill, and
`SKILL.md` routes to them by relative path.

### Codex

```bash
mkdir -p ~/.codex/skills
cp -R model-training ~/.codex/skills/
```

### Other agents

Use the folder as a portable instruction bundle: `SKILL.md` is the governing workflow, `references/`
are read on demand by the routing table at the bottom of `SKILL.md`.

---

## When it triggers

- Starting, resuming, or comparing training runs
- Deciding whether an architecture or loss change is worth the compute
- A run "looks fine" but nobody has checked it
- Loss is NaN, spiking, flat, or a metric will not move
- About to claim A beats B

---

## Prior art

This skill's structure — `SKILL.md` plus a routed `references/` tree, and a form filled per run —
follows [wassname/ml-debug](https://github.com/wassname/ml-debug). The NaN-poisoning leakage tracer,
the backprop-to-input dependency check, the prediction table and the "no side-car probe script" rule
come from that project. Framework conventions follow
[K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills).
The generic hyperparameter defaults are cross-checked against
[Orchestra-Research/AI-Research-SKILLs](https://github.com/Orchestra-Research/AI-Research-SKILLs).

The gate ladder, the composite-metric decomposition rule, the null-control lesson, and every measured
number in the case study come from work on PIE-style perturbation-response models
([ArcInstitute/pie](https://github.com/ArcInstitute/pie)).

Framework content is reused under its own licence; see each project.

---

## License

MIT. See [LICENSE](LICENSE).
