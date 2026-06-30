# OSWorld-V2 — codex hybrid (GUI+CLI, gpt-5.5 xhigh) 结果分析

- Run 目录: `results/osworld_v2_inject/FULL_108_gpt55_codex_xhigh_20260628_125852/`
- 方案: codex CLI 注入 + GUI 通道 (`gui=True`, `--agent_harness codex`)
- 模型: gpt-5.5, reasoning effort = xhigh
- 单题超时墙: 5400s (90 分钟)
- 权威逐题分数: `pyautogui/screenshot/gpt-5.5/tasks/*/score.json` (108 题齐全)
  - 注意: run 目录下零散的 `summary_*.json` 只是分批补跑片段, 不要用来汇总。

## 总分(两种口径)

| 口径 | 题数 | 平均分(含部分分) | 严格通过率(score=1.0) |
|---|---|---|---|
| 全部原始 | 108 | 49.64% | 18.52% (20/108) |
| **去 infra bug(推荐)** | **104** | **51.39%** | **19.23% (20/104)** |

严格通过数始终是 20 — 被剔的 4 题本就没有满分, 剔除只缩小分母。

## 108 题按结束状态分三类

- `agent_done=True` : 98 题 — 正常结束(codex 自报完成, 含做对/做错/做歪)
- `agent_done=False`: 7 题 — 撞 90 分钟超时墙被强杀
- `agent_done=None` : 3 题 — 中途异常, 未走到收尾(023 / 064 / 082)

`agent_done` 维度 ≠ 是否剔除维度。是否剔除只看失败根因是不是 infra。

## 剔除的 4 个 infra bug(与模型能力无关)

| task | agent_done | 原因 | 类型 |
|---|---|---|---|
| 063 | False | VM ext4 journal error, 根分区被 remount 只读 | 磁盘故障 |
| 064 | None | Connection broken / IncompleteRead(执行阶段) | 网络中断 |
| 082 | None | task setup 阶段 Connection reset by peer | 网络中断 |
| 069 | True | multiphase_unsupported_in_inject_runner(框架不支持多阶段评分) | 评测框架限制 |

069 虽然 agent_done=True、481s 正常结束, 但评分阶段被框架限制判失败 → 算 infra, 剔除。
所以剔除的 4 个里 3 个来自异常/超时组, 1 个(069)来自 done=True 组 — 不能简单"把没 done 的都剔掉"。

## 保留在分母的边界情况

### 6 个超时题(agent_done=False, 撞墙也计分)
| task | score | 备注 |
|---|---|---|
| 030 | 0.20 | GNN 训练任务, 单 turn 55万 token, **实际跑完输出 Done**, 只是太慢撞墙 |
| 054 | 0.54 | GIMP 群照合成, **实际完成并导出图片**, 太慢撞墙 |
| 060 | 0.10 | pptx 排期, GUI 操作慢 |
| 048 | 0.00 | 打游戏"Standlone", 231万 token 空转 + MCP 反复重连, codex 无法收敛 |
| 058 | 0.00 | 笔记本开合动画, 早早卡住空等 |
| 061 | 0.00 | 图像风格迁移, 卡在 python 脚本不返回 |

判断: 030/054 是"慢但成功", 048/058/060/061 是模型自身收敛不了 → 都算真实表现, 保留。
只有 063 这一个超时是真·环境故障, 已归入 infra 剔除。

### 023(agent_done=None, 但是成功题, 计 0.7)
- agent 做完了, `env.evaluate()` 成功打分 0.7(5/10 检查点)。
- 故障发生在**评分之后**的产物下载阶段(IncompleteRead), 网络错误把 0.7 覆盖成了 0。
- 已按 EVAL 日志人工修正回 0.7(见 023/score.json 的 note 字段)。
- 与 064/082 的区别: 064/082 故障在执行/setup 阶段导致任务没跑完(真失败); 023 任务和打分都成功, 只是事后取产物失败 → 不剔, 计 0.7。

## 横向对比(同 benchmark, gpt-5.5)

| 方案 | 平均分 | 严格通过率 |
|---|---|---|
| **codex hybrid (GUI+CLI, 去infra 104题)** | **51.39%** | **19.23%** |
| codex hybrid (GUI+CLI, 原始 108题) | 49.64% | 18.52% |
| codex 纯 CLI (gui=False) | 34.18% | 11.11% |
| openclaw GUI+CLI hybrid | 40.52% | 12.04% |
| claude CLI max | 40.93% | 12.04% |

codex 混合方案是各方案最高; GUI+CLI 比纯 CLI 高约 15 个点。

## 效率统计表(per-task, 全 108 题口径)

| Model | Binary (%) | Partial (%) | Cost/task | Tool calls/task | Out tok/task | Steps/task |
|---|---|---|---|---|---|---|
| codex hybrid (GUI+CLI) | 18.5 | 59.3 | — | 68 (med) | — | 1 turn (med) |
| codex 纯 CLI | 11.1 | 49.1 | — | — | — | 2 turn (med) |
| claude CLI max | 12.0 | 60.2 | $39.71 (med) | — | 11,657 (med) | 23 (med) |
| openclaw / cuaclaw hybrid | 12.0 | 62.0 | — | — | — | — |
| 官方纯 GUI (gpt-5.5, 500步) | 0.0 | — | — | — | — | 117.5 (med) |

> 注: Binary = score=1.0 占比; Partial = 0<score<1 占比 (二者相加 ≈ 有产出的题, 余下为 0 分)。
> 均为全 108 题口径; codex hybrid 去 infra(104 题)口径 Binary=19.2%。

### 字段口径与可得性(各 harness 采集不一致, 不能直接比绝对值)

- **codex (hybrid / 纯CLI)**: 来自 in-VM `codex` CLI 的 agent.log。
  - `Tool calls/task` = `hybrid_codex_action_mix.json` 的 CLI+GUI 动作总数 (median 68, mean 77.5)。
  - `Steps/task` 用 codex turn 数代替 (hybrid median 1, 纯CLI median 2) — codex 单 turn 内含多次工具调用, 与 claude 的 turn 不可比。
  - **Cost 无法计**: codex CLI 只在结尾打印 "tokens used" 总量(含 input+reasoning+output, median ~558k tok/task), 不拆 output, 也无单价 → Out tok/task、Cost/task 留空。
- **claude CLI max**: 来自 `claude_stream.jsonl` 的 result 事件, 字段最全。
  - Cost/task = `total_cost_usd` (median $39.71, mean $56.95)。
  - Out tok/task = `usage.output_tokens` (median 11,657, mean 15,529)。
  - Steps/task = `num_turns` (median 23, mean 26.7)。
  - 101/108 题有 usage(7 题异常无 result 事件)。
- **openclaw / cuaclaw hybrid**: 分数取自 `check/hybrid_*_time.json`; per-task token/cost/toolcall 未单独采集 → 留空。
- **官方纯 GUI**: 取自 xlangai 官方 trajectory 包(results_gpt5.5_500steps), 只有 step/timing, 无分数对齐到本表评测器 → Binary 显示 0 是因该来源未含本地 native 分, 仅 Steps(median 117.5)可用作 GUI 步数参考。

### 关键对比解读

- **codex hybrid 用极少的"轮次"达到最高分**: median 1 个 codex turn(单 turn 内自主多步), 而 claude 需 median 23 turn — codex 把多步操作压在一次长链路里。
- **codex token 消耗大**: median ~558k tok/task(含 input 累积), 反映 xhigh + 单 turn 长上下文; claude output 只有 ~12k(但 input 达 ~1.97M, cost $39.71/task)。
- Cost 维度只有 claude 有权威数字($39.71/task median); codex 需用 LiteLLM 侧用量日志另算, 当前 run 未落盘。
