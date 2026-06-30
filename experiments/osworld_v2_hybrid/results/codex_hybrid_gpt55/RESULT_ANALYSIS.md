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

## 两种口径(codex hybrid)

| 口径 | 平均分(Partial) | 严格通过率(Binary) |
|---|---|---|
| **去 infra bug(推荐, 104 题)** | **51.39%** | **19.23%** |
| 原始 108 题 | 49.64% | 18.52% |

## 对比 paper Table 3(同 backbone gpt-5.5)

我们的 codex hybrid 与 paper Table 3 里官方 GPT-5.5(batched)行直接对比(同评测器、同 108 题):

| Model / harness | Binary (%) | Partial (%) | Tool calls/task |
|---|---|---|---|
| **GPT-5.5 + codex hybrid(本工作)** | **18.5** | 49.6 | 77.5 |
| GPT-5.5 batched(paper Table 3) | 13.0 | 49.5 | 149.8 |

> 口径对齐 paper Table 3:**Binary = score=1.0 的题占比;Partial = 全 108 题的平均部分分(partial credit / 平均分),不是"拿到部分分的题占比"**。
> codex hybrid 去 infra(104 题)口径:Binary 19.2% / Partial 51.4%。

**结论**:同一 GPT-5.5 backbone,把官方 batched loop 换成 codex hybrid harness,Binary **+5.5 pt(13.0 → 18.5%)**,而 Partial 几乎不变(49.5 → 49.6)且 tool calls/task 砍半(149.8 → 77.5)。增益来自把"接近完成"的任务推过满分线,而非普遍提升部分进度。

字段口径说明:
- `Tool calls/task` = codex agent.log 的 CLI+GUI 动作总数(mean 77.5 / median 68)。
- `Cost/task`、`Out tok/task` 留空(`—`):codex CLI 只打印 "tokens used" 总量(含 input+reasoning+output, median ~558k tok/task),不拆 output、无单价,无法与 paper 的纯 output token / cost 对齐。
- paper 的 `Steps/task`(一次 observe→act 回合)对应我们的 tool-call 数(codex 单动作执行 ≈ single-action),不是 1–2 次 `codex exec`。

> 注:其余 harness(纯 CLI / claude / cuaclaw / 官方纯 GUI)的对照后续再补。
