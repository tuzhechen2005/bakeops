# 排程器规格

> 模块：`bakeops/scheduler/`。纯 Python，不依赖 Web 框架和数据库，输入输出都是 Pydantic 模型。
> 求解器：OR-Tools CP-SAT（开源）。校验器：独立实现，不调用求解器。
>
> **10/21 已实现第一版**（`types.py`、`model.py`、`validator.py`、`builder.py`），本文已按实现和 T2 的发现更新。代码与本文不一致时，先看 `DECISIONS.md` D24–D28。

## 1. 输入与输出

```python
class ScheduleInput(BaseModel):
    config: KitchenConfig
    now_min: int                      # 模拟时钟或真实时间
    batches: list[BatchSpec]          # 来自装盘与分缸规则
    trays: list[TraySpec]
    orders: list[OrderSpec]           # 含 promise_ready_by_min（可空）
    frozen: list[FrozenStep]          # 已开始或已完成的步骤及实际时间
    previous_ready: dict[str, int]    # 上一版正式计划每单的完成时间（重排时用）
    request: Request | None           # 本次要满足的请求，见第 4 节

class ScheduleResult(BaseModel):
    status: Literal["feasible", "infeasible", "not_found"]
    steps: list[ScheduledStep]        # status=feasible 时有
    order_ready: dict[str, int]
    earliest_for_request: int | None  # 做不到时，请求订单的最早可行完成时间
    reason: InfeasibleReason | None
    oven_mode_switches: int
    solver_stats: dict
```

## 2. 区间与资源

对每个步骤实例建一个 CP-SAT 区间变量（固定时长）。

| 步骤 | 拥有者 | 资源 |
|---|---|---|
| mix, divide | 缸次 | 人手 |
| bulk | 缸次 | 烤箱（发酵模式，占 `slots` 个位置） |
| rest | 缸次 | 室温（无限制） |
| shape | 盘 | 人手 |
| proof | 盘 | 烤箱（发酵模式，1 个位置） |
| bake | 盘 | 烤箱（烘烤模式，1 个容量） |
| cool | 盘 | 室温 |
| package | 订单 | 人手 |

`divide` 的时长 = `duration_min + per_tray_extra_min × 该缸盘数`。

## 3. 约束

**C1 时间范围**：所有步骤在 `[earliest_start, latest_end]` 内；未开始的步骤 `start ≥ now_min`。

**C2 前后顺序与等待窗口**：同一拥有者链上相邻步骤 `0 ≤ start(next) − end(prev) ≤ max_wait_after_min`。
- 缸次链：mix → bulk → divide → rest。
- 缸次 → 盘：每盘的 shape 在所属缸 rest 结束后开始；且该缸**最后一盘整形结束** − rest 结束 ≤ 该缸所有盘的整形时长之和 + `shape_slack`。也就是一缸面醒好后，所有盘要接连整形完，中间最多空出 `shape_slack` 分钟。（按单盘设窗口是错的：一个人只能一盘一盘整形，后面的盘天然要等。）
- 盘链：shape → proof → bake → cool。
- 订单：package 在该订单所有相关盘的 cool 结束后开始；`ready(order) = end(package)`。
- 两个步骤都已发生时，它们之间的约束跳过：事实不再被评判（例如你实际整形慢了，不会因此让后面的计划无解）。

> 原来写的"等待窗口默认 0"已作废（D24）：单腔烤箱里三发和烘烤之间至少隔预热时间，窗口为 0 时任何一天都排不出来。配置加载时检查这一点。

**C3 人手**：所有人手区间 `AddNoOverlap`。

**C4 烤箱容量**：
- 发酵模式区间：`AddCumulative(demand=slots, capacity=proof_capacity_slots)`。
- 烘烤模式区间：`AddCumulative(demand=1, capacity=bake_capacity_trays)`。

**C5 烤箱模式互斥与切换时间**：对每一对（发酵区间 p，烘烤区间 b），引入布尔 `x_pb`：
- `x_pb = 1` → `end(p) + switch_proof_to_bake ≤ start(b)`
- `x_pb = 0` → `end(b) + switch_bake_to_proof ≤ start(p)`

由工序链已经决定先后的一对（例如同一盘的三发和烘烤、一缸的一发和它所有盘的烘烤）直接写成固定先后，不建布尔变量。

实测规模（测试参数，D27）：每缸最多 3 盘时，4 缸约 12 盘；2 缸 0.3 秒，4 缸 5–8 秒。

**C6 冻结事实**：`frozen` 中的步骤，开始时间（和已完成的结束时间）固定为实际值，不允许求解器改。

**C7 承诺**：有承诺的订单 `ready(order) ≤ promise_ready_by_min`，硬约束。

**C8 请求**（见第 4 节）。

## 4. 请求类型与目标

所有求解都分两段：先求可行，再在可行范围内按字典序优化。用"固定上一级目标值再求下一级"的方式实现字典序。

| 场景 | 硬约束 | 优化顺序 |
|---|---|---|
| 初始计划 | C1–C7 | ① 收工时间最早 ② 各单完成时间之和最小（D28） |
| 问时间 | 不求解，读正式计划 | — |
| 要求某时间前 `ready(o) ≤ T` | C1–C7 + 请求 | ① 其他订单完成时间被推迟的总分钟数最少 ② 收工时间最早 |
| 加单 | C1–C7 + 新订单在 `latest_end` 前完成 | ① 其他订单被推迟的总分钟数最少 ② 新订单完成时间最早 ③ 收工时间最早 |
| 取消 | C1–C7 | ① 其他订单被推迟的总分钟数最少 ② 收工时间最早 |

"被推迟的总分钟数" = `Σ max(0, ready_new(o) − previous_ready(o))`，只对未取消的旧订单计算。提前不计惩罚。

## 5. 三种结果

1. **feasible**：CP-SAT 返回 OPTIMAL 或 FEASIBLE，且**校验器通过**。校验器不通过视为程序错误，返回 500 并记日志，绝不展示给用户。
2. **infeasible**：CP-SAT 返回 INFEASIBLE。这表示"按**现在的分缸方式**、你录入的参数和已发生的事实，做不到"（D26：分缸装盘是固定规则，不在求解范围内）。随后：
   - 去掉请求中的时间约束，改为最小化请求订单的完成时间，得到 `earliest_for_request`。
   - 如果连这样都不可行（超过收工时间），再把 `latest_end` 放宽到 23:59 求一次，得到"最早要做到几点"，原因标为 `exceeds_day_end`。
   - 两次都是同样的硬约束（含所有承诺），所以给出的最早时间不会牺牲已答应的顾客。
3. **not_found**：CP-SAT 返回 UNKNOWN（超时）。界面文字固定为"在 X 秒内没有找到方案"，**绝不**写成"做不到"。

求解时间上限：每次 10 秒（多个目标时平分），`num_workers=1`，固定随机种子。单线程保证同样输入得到同样计划（D27）。

最早时间只在 CP-SAT 返回 OPTIMAL 时才叫"最早"（`proven=True`）；否则界面写"可以做到 X 点"，不说最早。

## 6. 原因说明（确定性模板，不用模型）

`InfeasibleReason` 至少包含：
- `kind`：`request_too_early` | `exceeds_day_end` | `no_dough_capacity`
- `earliest_min`
- `binding_facts`：只列出可从数据直接读出的事实，例如：
  - "烤箱在 11:20–12:05 被已开始发酵的第 2 缸占用（已发生，不能动）"
  - "已答应王五 13:40，不能推迟"
  - "需要新开一缸：打面 15 + 一发 50 + 分割 12 + 二发 20 + 整形 12 + 三发 40 + 预热 15 + 烘烤 15 + 冷却 20 + 包装 3 = 202 分钟"（单腔烤箱，预热不能省；这个数由代码从配置算出，测试 `test_single_tray_equals_hand_computed_chain` 锁定）
  - "参数'一发后最长等待'未确认，按 0 分钟处理"

`binding_facts` 由规则生成：冻结步骤中占用烤箱或人手、且时间晚于 `now_min` 的；所有承诺；新开一缸时的最短链长（各步时长相加，是严格下界）；值为 0 的未确认窗口。不声称这些就是"唯一原因"，界面标题写"相关事实"。

## 7. 差异（diff）

对比上一版正式计划与候选方案，按订单输出：
- 完成时间变化（提前 / 推迟 / 不变，分钟数）
- 是否有承诺、是否仍守住
- 新增、取消的订单
- 烤箱模式切换次数变化
- 收工时间变化

界面的变化说明用模板生成，例如："赵六 新增，14:55 完成；李四 推迟 20 分钟到 15:30（无承诺）；王五 不变，13:40（已承诺，守住）。"

## 8. 校验器（`scheduler/validator.py`）

不依赖 OR-Tools。输入 `ScheduleInput` + `ScheduleResult`，逐条检查 C1–C7 和以下几点，返回违规列表：
- 每盘个数 = 其分配之和；每缸克数 ≥ 其盘需要的面团之和；
- 冻结步骤的时间与实际值完全一致；
- 烤箱任一时刻只有一种模式，且相邻异模式区间之间满足切换时间。

用途：
1. 每次求解后立即校验；
2. 批准时用"当前时间"重新校验（见 `API_SPEC.md`）；
3. 测试中对随机生成的输入做性质测试。

## 9. 测试清单（pytest）

- 单缸单盘：结果等于各步时长之和（手算对照）。
- 烤箱模式：发酵与烘烤从不重叠；切换间隔 ≥ 配置值。
- 人手：无重叠。
- 冻结：已开始步骤的开始时间在重排后不变。
- 承诺：加单后，所有承诺仍满足；做不到时返回 infeasible 而不是挪承诺。
- 三种结果：构造确定不可行的输入 → infeasible；构造极短超时 → not_found 且文案不含"做不到"。
- 最早时间：infeasible 时给出的 `earliest_for_request` 本身再求一次必须 feasible。
- 取消：面团账按 `DOMAIN.md` 第 4 节处理。
- 性质测试（hypothesis）：随机 1–4 缸、1–16 盘，所有 feasible 结果都通过校验器。
- 演示数据：`DEMO_SCRIPT.md` 每一幕的预期状态作为回归测试。
