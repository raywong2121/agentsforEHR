# OHDSI Evidence Agent

面向中文临床自由文本的 **OHDSI 概念关联研究原型**。它保留原文与语境，在有限预算内执行信息拆解、词表检索、表达选择和逐绑定审校，并公开未完成的问题。本仓库保存 Dify 原生工作流及其 Code、Prompt、SQL 和离线工程测试，供核查与复现。

**研究证据截至 2026-10-25。公开归档日期为 2026-10-27。** 这是实验性实现，不是临床部署产品。工程合同通过、工作流执行成功、模型自审通过，都不能代替独立的医学语义验证。本仓库没有独立专家金标准，也没有证明跨病例自动学习。

仓库地址：<https://github.com/raywong2121/agentsforEHR>

## 从哪里开始

| 内容 | 路径 | 用途 |
|---|---|---|
| 实际导出 DSL 的公开衍生版 | [`dify/e91.public.yml`](dify/e91.public.yml) | 30 个节点、41 条边；不是手写的可导入蓝图 |
| 节点与实机配置摘要 | [`dify/configuration_summary.json`](dify/configuration_summary.json) | 核对模型、插件、Loop 与已知合同差异 |
| Code 节点入口 | [`dify/node_entrypoints.json`](dify/node_entrypoints.json) | 核对实际输入变量及 wrapper |
| 状态机与门禁 | [`src/term_runtime.py`](src/term_runtime.py) | Code 调度、状态更新、逐目标反馈及输出 |
| 有限 JSON 恢复 | [`src/rule_json.py`](src/rule_json.py) | 从模型文本提取受约束的 JSON，不调用外部服务 |
| 数据库回执规整 | [`src/receipt_normalizer.py`](src/receipt_normalizer.py) | 将查询结果交给运行时核验 |
| 四类模型角色提示词 | [`prompts/PROMPTS.md`](prompts/PROMPTS.md) | SourceAudit、Decomposer、Planner、Reviewer |
| 固定参数化只读查询 | [`sql/vocabulary_search.sql`](sql/vocabulary_search.sql) | 使用部署者已有的 OHDSI 词表数据库 |
| 纯合成输入 | [`examples/synthetic_cases.json`](examples/synthetic_cases.json) | 两个下游开发案例；不作为专家答案 |
| 离线工程控制 | [`tests/`](tests/) | 不访问模型或数据库 |
| 本次离线验收与范围 | [`docs/offline-tests.md`](docs/offline-tests.md)、[`artifacts/offline-tests-public-final.json`](artifacts/offline-tests-public-final.json) | 26 项 unittest：17 合同、8 JSON 恢复、1 个八子进程引用控制 |
| 机制与证据边界 | [`docs/architecture.md`](docs/architecture.md)、[`docs/research-status.md`](docs/research-status.md) | 区分实现、历史观察与未验证能力 |

## 本地离线验收

离线源码和测试只使用 Python 标准库，不需要安装 pip 依赖，也不会启动服务或访问数据库。使用已有 Python 3.9 或更高版本（实际验收版本见回执），在仓库根目录执行：

```sh
python3 -m unittest discover -s tests -v
```

若需保存带执行环境与逐项状态的回执：

```sh
python3 tests/run_offline_suite.py --output artifacts/offline-tests.json
```

回执记录的是本次离线工程控制；历史的 17 项合同控制另有明确范围，新增 JSON fixtures 或子进程控制不能计作新增医学验证。脚本拒绝覆盖已有回执，重复验收时应换一个输出文件名，保留首次失败记录。

公开归档时实际离线运行 **26/26 通过**；其中引用稳定性测试包含 8 个独立 Python 子进程。完整状态和环境见上述回执，范围说明见 `docs/offline-tests.md`。这不是 E91 的新模型或 Dify 全链验收。

准备节点也可单独检查，以下命令不会执行 LLM 或 SQL：

```sh
PYTHONPATH=src python3 - <<'PY'
import json
from pathlib import Path
from term_runtime import prepare

case = json.loads(Path("examples/synthetic_cases.json").read_text())["cases"][0]
result = prepare(case["case_text"], case["question"], case["strategy_memory_str"])
print("prepare outputs:", sorted(result))
PY
```

仓库没有可脱离 Dify 自动调用云模型和数据库的独立网络运行器。上述命令不能证明 Dify 导入成功或端到端模型链通过。

## 在已有 Dify 中复现

1. 在独立、未发布草稿导入 `dify/e91.public.yml`。该文件来自 E91 实际导出，仅进行了公开归档所需的名称、描述和租户图标 URL 清理；原始导出与公开文件的来源信息见仓库内 provenance 清单。
2. 核对导入环境支持 Workflow、Loop、LLM、Code、变量赋值及错误分支。这里的 YAML 格式版本是 `0.7.0`，不是对任何 Dify 版本均兼容的承诺。
3. 在使用者自己的 Dify 工作区配置模型服务与凭据。归档四个 LLM 节点均为 `qwen3.8-max-0902`，温度 `0.2`、输出预算 `8192`、`reasoning_effort=low`；这些是归档配置别名及参数，不证明当前上游权重身份或服务可用性。换模型或参数时应建立新实验批次。
4. 核对归档插件：`langgenius/openai_api_compatible` **0.0.69**，`spance/db_client_node` **0.1.47**。完整插件标识保留在 DSL 和配置摘要中；不猜替代 ID。
5. 为数据库工具重新绑定使用者已有的只读词表连接。`postgres_node_5` 是原工作区工具名称，不是通用内置连接。本仓库没有主机、账号、密码、模型 key 或词表数据库整库；固定 SQL 见 `sql/vocabulary_search.sql`。
6. 先使用 `examples/synthetic_cases.json` 的合成文本预览，逐节点核对最新 Loop 状态、真实查询回执、绑定修订和未决目标。不要直接公开发布草稿。

导入、重新授权和重新绑定仍是必要部署工作，离线测试不代替它们。云模型会接收到绑定给 LLM 节点的文本；Dify 在自有服务器运行不意味着文本不离开服务器。首次复现应继续使用合成文本。

**已知合同差异未在本次公开归档中偷偷修复：** runtime 接受的 `max_terms` 上限是 8，SQL 的 typed search 路线至多 6 词，legacy array 路线至多 8 词。默认值 6 在此交集内。复现实验应保留该差异，调整合同后另立版本并测试。

## 能力与限制

- Code 决定全局任务与预算；模型在当前任务内生成拆解、查询、表达比较或审校结果，不是自由调用任意外部工具。
- 原文、主体、否定、时间、数值和限定的附着对象需要共同保留。仅给文本挂上 `concept_id` 不等于完成编码；模型标签关联也不等于严格医学等价或完成 OMOP 表级落库。
- 检索未命中只说明本次查询未返回候选，不能证明概念不存在。未决、关系降级、格式恢复和提速分别报告，不能计作医学语义修复。
- 已实现有限的单次运行反馈：补检索、局部重选、事实修订和再审校。`action_priority`、`focus_fields` 目前仅做输入校验；只有 `max_terms` 影响实际预算。输出候选策略没有自动晋升、共享持久化或跨病例学习。
- 历史 E91 的工程合同控制通过，但新 Reviewer 调用受提供方拒绝访问影响，相关 MAIN 对照没有运行。公开源码不是该闭环已获新模型验收的证明；详见[研究状态](docs/research-status.md)。

## 许可、引用与数据

本仓库暂未附加开源许可证。公开可见不等于已授予任意再分发、修改或商业使用许可，参见 [`NOTICE.md`](NOTICE.md)。OHDSI 词表中各来源词汇的许可条件独立适用，仓库不分发数据库整库或受限词表内容。

仓库引用元数据见 [`CITATION.cff`](CITATION.cff)。它标识软件归档，不虚构论文 DOI、期刊发表或研究作者名单。实验数据与失败边界以实际回执及研究文章为准，不能从测试数量推导临床准确率。

## English summary

This repository archives a Dify-native research workflow for evidence-aware association of Chinese clinical free text with OHDSI concepts. Code owns task dispatch and budgets; LLM roles perform source inventory, decomposition, scoped retrieval planning, selection and review. Local source and tests use the Python standard library only. Live reproduction requires the user's existing Dify model authorization, query plugin and read-only vocabulary database configuration.

The research evidence cutoff is **25 October 2026**. Contract checks are engineering evidence, not clinical validation. The archive does not establish strict medical equivalence, complete information capture, expert-level accuracy, OMOP table loading or cross-case autonomous learning. No credentials, real patient records or vocabulary database dump are included. No open-source license has yet been selected.
