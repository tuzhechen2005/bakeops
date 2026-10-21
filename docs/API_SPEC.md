# API 规格

> FastAPI + Pydantic + SQLite，单进程。所有写接口都要求 `Idempotency-Key` 请求头。
> 时间字段在 API 中用 `"HH:MM"` 字符串，内部转换为分钟数。

## 1. 通用规则

- **幂等**：写接口收到已处理过的 `Idempotency-Key` 时，直接返回第一次的响应（存在 `IdempotencyRecord`），不重复执行。前端每次用户动作生成一个 UUID，双击发送同一个 key。
- **事务**：每个写接口在一个 SQLite 事务内完成；批准接口使用 `BEGIN IMMEDIATE`，避免两个页面同时批准。
- **版本**：正式计划版本号 `plan_version` 单调递增。凡是基于计划的写操作都要带 `expected_plan_version`，不一致返回 `409 plan_changed`。
- **时钟**：所有"现在"取自 `Clock` 服务。演示模式可设置模拟时间；生产模式用系统时间。
- **错误格式**：`{"error": "<code>", "message_zh": "...", "message_en": "...", "details": {...}}`

## 2. 配置与参考数据

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/config` | 当前厨房配置（只读，来自 YAML） |
| GET | `/api/customers` | 顾客列表 |
| POST | `/api/customers` | 新建顾客 `{display_name, aliases}` |

## 3. 订单导入

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/intake/orders:parse` | `{day, text}` → `{draft_id, rows[], unparsed[], possibly_missed[]}`，每行含 `evidence` 和 `errors[]` |
| POST | `/api/intake/orders/{draft_id}:commit` | `{rows[]}`（面包师修改后）→ 校验全部通过才写入；返回 `{order_ids[]}` |

`commit` 后自动创建一个 `kind=initial` 的 Proposal（若当天还没有正式计划）。

## 4. 计划

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/plan/current?day=` | 正式计划：`{plan_version, steps[], order_ready{}, promises{}, day_end}` |
| GET | `/api/plan/versions?day=` | 历史版本列表 |
| POST | `/api/proposals:initial` | `{day}` → 生成初始计划候选 |

## 5. 消息

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/messages:interpret` | `{day, customer_id, text}` → `{message_id, interpretation, needs_clarification, candidates[], duplicate_of?}` |
| POST | `/api/messages/{id}:confirm` | `{interpretation(面包师修改后), target_order_id}` → 创建 Proposal 或直接返回答案 |

**重复消息**：同一顾客、同一天、规范化后文本哈希相同 → 返回 `duplicate_of`，界面提示"这条消息 11:02 已处理过"，由你决定是否仍要处理。

**确认后的分支**：

| 意图 | 返回 |
|---|---|
| ask_ready_time | `{answer: {ready_at}, reply_draft}`，不创建 Proposal |
| request_ready_by / add_items / cancel | `{proposal}` |
| change_quantity / change_product | `{reply_draft}`（店规模板） |
| other | `{manual: true}` |

## 6. 候选方案

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/proposals/{id}` | `{status, solver_status, diff, reason?, earliest?, reply_draft, base_version, computed_at}` |
| POST | `/api/proposals/{id}:approve` | `{expected_plan_version}` |
| POST | `/api/proposals/{id}:reject` | — |

**批准时的重新检查（顺序固定）**：
1. Proposal 状态必须是 `draft`。已是 `approved` → 返回原结果（幂等）。
2. `expected_plan_version == base_version == 当前正式版本`，否则 → 状态改为 `stale`，返回 `409 plan_changed`。
3. 用**当前时钟**跑校验器：候选里任何尚未开始的步骤 `planned_start < now` → `stale`，返回 `409 proposal_expired`，提示"这个方案要求 11:05 开始整形，现在已经 11:12，请重新计算"。
4. 从提出方案到现在之间若有新的执行事件（开始 / 完成）→ `stale`，`409 facts_changed`。
5. 全部通过 → 写入新 PlanVersion，更新订单（加单 / 取消），处理面团账，返回新版本号。

只有 `solver_status = feasible` 的 Proposal 才有"批准"按钮。`infeasible` 和 `not_found` 只能"拒绝"。

**做不到时的替代方案**：如果请求时间做不到、但这一单今天还能做（有 `earliest_for_request` 且不超过收工时间），系统同时生成一个 `alternative_proposal_id`：同样的改动，但不加时间要求，按最早时间排。界面显示为"可以做到 15:50，要按这个时间处理吗？"。你批准替代方案后，它的回复草稿写的是 15:50，标为已发送才变成承诺。

## 7. 回复与承诺

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/replies/{id}` | `{text_zh, text_en_gloss?, ready_by?}` |
| POST | `/api/replies/{id}:mark_sent` | 你自己发出后点击。若含时间 → 创建 Promise |

**创建承诺前的检查**：承诺的时间必须 ≥ 当前正式计划中该订单的完成时间。否则返回 `409 promise_unsafe`（例如回复是基于一个被拒绝的方案生成的）。

## 8. 执行记录

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/execution/events` | `{step_id, type: start\|finish, at?}`，`at` 缺省为当前时钟 |
| GET | `/api/log?day=` | 合并时间线：执行事件、批准、拒绝、承诺、消息 |

规则：
- `start` 只能用于 `planned` 步骤；同一缸或同一盘的链上，前一步未开始时不能开始后一步。
- `finish` 只能用于 `started` 步骤。
- 记录执行事件不会自动重排计划；界面提示"实际进度与计划有偏差"，由你决定是否重新计算。

## 9. 演示专用

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/demo/clock` | `{now: "11:05"}`，仅当 `BAKEOPS_DEMO=1` 时可用 |
| POST | `/api/demo/reset` | 清空当天数据并载入 `demo/seed.yaml` |
| POST | `/api/demo/advance` | 把正式计划中、在当前时钟之前应开始 / 完成的步骤，按计划时间记为开始 / 完成（生成正常的执行事件） |

## 10. 重启恢复

所有状态都在 SQLite 里。启动时不做任何自动计算。测试：
1. 批准一个方案后杀掉进程、重启 → 正式计划版本、承诺、执行事件完全一致。
2. 重启后用同一 `Idempotency-Key` 再发一次批准 → 返回原结果，版本号不变，面团账不重复扣。
