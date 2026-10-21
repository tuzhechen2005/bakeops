# 交接文档

> 写于 2026-10-21（周三）13:45 美国中部时间。交给本地 VS Code 里的 Claude 继续开发。
> 截止：**10-25 23:59 PDT = 10-26 01:59 CT**，目标周日 20:00 CT 前发布。剩下约 4 天半。
> 先读仓库根目录的 `CLAUDE.md`（规则），再读本文。

## 1. 现在做到哪了

| 部分 | 状态 |
|---|---|
| 产品与设计文档（`docs/`） | 完成。拷问过程中的决策全部在 `DECISIONS.md`，含来源 |
| T0 仓库 | 完成 |
| T2 排程风险验证 | **通过**。CP-SAT 模型、独立校验器、三种结果、最早时间都已实现并测试 |
| T1 模型风险验证 | **代码完成，还没在真模型上跑过**（云端没有 Ollama）。这是你的第一件事 |
| T4 之后 | 未开始 |

测试：65 个，全部通过，约 2 秒（`pytest`）。

## 2. 代码地图

```
backend/bakeops/
  scheduler/
    types.py      ScheduleInput / ScheduleResult 等，时间一律是"当天 0 点起的分钟数"
    builder.py    厨房配置 + 装盘布局 -> ScheduleInput；check_config()；unconfirmed()
    model.py      CP-SAT：solve()、earliest_ready()
    validator.py  独立复核，不 import OR-Tools：validate(inp, res, now=None)
  llm/
    schemas.py    模型必须返回的 JSON（ParseOrdersOutput、MessageInterpretation）
    interpreter.py OllamaInterpreter：schema 约束输出，temperature 0，失败重试 1 次
    checks.py     模型之后的程序把关：check_orders()、check_message()
    timeparse.py  "四点""下午4点半""8点"……按营业时间求解释，两种都可能 -> ambiguous
    qtyparse.py   "4个""四个""十二""两""一打"
    prompts/      两个中文系统提示词
backend/tests/    test_scheduler.py、test_llm.py、test_parsers.py
config/kitchen.example.yaml   测试参数（不是烘焙建议），数值写成 {value, confirmed}
eval/             dev.jsonl（25 条，全是虚构）、fixtures.json、run_eval.py
spikes/t2_scheduler.py        四个排程场景，可直接运行看时间线
```

### 关键接口

```python
from bakeops.scheduler import solve, earliest_ready, validate
from bakeops.scheduler.builder import (load_config, build_input,
    BatchLayout, TrayLayout, OrderLayout, hhmm_to_min, min_to_hhmm)

inp = build_input(cfg, batches, orders, now="10:30",
                  frozen={step_id: Frozen(start=..., end=...)},   # 已发生的步骤
                  previous_ready={order_id: minutes},              # 上一版正式计划
                  request=("order_id", "15:00"),                   # 可选
                  objective="initial" | "minimize_delay")
res = solve(inp)        # status: feasible / infeasible / not_found
                        # validator 不通过会抛 SchedulerBug（API 里返回 500）
e = earliest_ready(inp, "order_id")   # status ok / exceeds_day_end / impossible / not_found
                                      # e.proven=True 才能说"最早"
validate(inp, res, now=clock)         # 批准时用当前时间复核
```

步骤 id 的格式是 `"<batch或tray或order id>.<step id>"`，例如 `B1.mix`、`T3.proof`、`zhang.package`。**冻结、执行记录都按这个 id 对应，所以缸和盘的 id 在重排时必须保持不变**（见 T4）。

### 各场景怎么调用排程器

| 场景 | 调用方式 |
|---|---|
| 早上生成计划 | `objective="initial"`，不传 request |
| 问几点能好 | 不求解，读正式计划里的 `order_ready` |
| 要求某时间前 | `objective="minimize_delay"`，`request=(订单, 时间)`，`previous_ready=正式计划` |
| 加单，带时间 | 新订单加进布局，同上 |
| 加单，不带时间 | 同上，`request=(新订单, 收工时间)`，这样第二目标会让新单尽量早 |
| 取消 | 从布局中移除（按面团账规则），`objective="minimize_delay"`，不传 request |
| 做不到时的替代方案 | `earliest_ready()` 得到时间 T，再用 `request=(订单, T)` 求一次，得到可批准的方案 |

## 3. 已知空缺和与 spec 的偏差（不要当成 bug 重新发现）

1. **`max_trays_per_batch` 在配置里，但 builder 没用。** 分缸是 T4 的职责，builder 只展开步骤。
2. **所有产品共用 `tray_steps`。** 按产品单独设置时长还不支持；需要时在 builder 里按 `product_id` 覆盖。
3. **装炉、出炉不占用人手**，这是简化。如果用户说装炉要占几分钟，在 `tray_steps` 里加一个 `where: person` 的短步骤即可。
4. **原因说明（`explain.py`）和差异（`diff.py`）还没写**，属于 T6 剩余部分。规格见 `SCHEDULER_SPEC.md` 第 6、7 节。最短链长（下界）要从配置算，**包括预热时间**，测试 `test_single_tray_equals_hand_computed_chain` 锁定为 202 分钟。
5. **`DOMAIN.md` 第 1 节的 YAML 是早期草稿**，以 `config/kitchen.example.yaml` 为准。
6. **`API_SPEC.md` 还没实现**；FastAPI、uvicorn 还没加进 `pyproject.toml`。

## 4. T2 的重要发现（已写进 DECISIONS D24–D28）

- **等待窗口不能默认 0。** 单腔烤箱里，三发结束到烘烤至少隔预热时间。`check_config()` 会在"三发后最长等待 < 预热时间"时直接报错。
- **每缸最多约 3 盘**（测试参数下）。4 盘时第一盘发好、最后一盘还在发，烤箱切不到烘烤，第一盘发过头。测试 `test_four_trays_per_batch_cannot_fit_one_chamber` 锁定。
- **"做不到"只对当前分缸方式成立**，界面文字要写清楚。
- **性能**（测试参数，单线程）：2 缸 0.3 秒；4 缸 5–8 秒；4 缸×3 盘约 4 秒证明排不下。白天大部分步骤已冻结，会快得多。
- **一缸内的整形窗口是按整缸算的**（最后一盘整形结束 − 二发结束 ≤ 各盘整形时长之和 + `shape_slack`），不是按单盘。按单盘算会因为一个人只能一盘一盘整形而永远无解。

## 5. 下一步（按顺序）

每一步做完：`pytest` 全绿 → 提交 → 在 `TASKS.md` 打勾 → 如有决策变化写进 `DECISIONS.md`。

### 第 0 步：环境（15 分钟）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest && python spikes/t2_scheduler.py
```

### 第 1 步：T1 模型评测（和用户一起，1–2 小时）

1. 确认 Ollama 在运行，`ollama pull gemma4:e4b`。
2. `python eval/run_eval.py --model gemma4:e4b`，把结果表给用户看。
3. **请用户提供 5 段以上真实订单**（照他本子的写法，名字改掉），按 `dev.jsonl` 已有格式加进去，标 `"synthetic": false`。真实数据比虚构数据重要。
4. 看 `eval/results/*.jsonl` 里失败的条目，**只在 dev 集上**调 `prompts/` 里的提示词。
5. 达不到门槛（`TASKS.md` T1 的通过标准）就换 `gemma4:12b-it-qat`；仍不达标，订单导入改为手动表单，模型只做消息理解。
6. 把结论（用哪个模型、各项分数、耗时）写进 `DECISIONS.md` 的"风险验证结论"。

注意：Gemma 4 有可配置的思考模式。默认不传 `think` 参数；如果输出里混进思考内容或速度很慢，试 `--think off`。

### 第 2 步：T4 装盘分缸与面团账（2 小时）

新建 `backend/bakeops/domain/`。建议接口：

```python
class OrderItemIn(BaseModel): id: str; product_id: str; qty: int
class OrderIn(BaseModel):
    id: str; customer_id: str; items: list[OrderItemIn]
    promise_ready_by: int | None; created_seq: int

class TrayPlan(BaseModel):
    id: str; batch_id: str; product_id: str
    allocations: dict[str, int]   # order_item_id -> 个数
class BatchPlan(BaseModel): id: str; dough_g: int; tray_ids: list[str]
class Layout(BaseModel): batches: list[BatchPlan]; trays: list[TrayPlan]

def layout_day(cfg, orders: list[OrderIn], previous: Layout | None,
               locked_batches: set[str], locked_trays: set[str]) -> Layout
def to_builder(layout: Layout, orders) -> tuple[list[BatchLayout], list[OrderLayout]]
def cancel_items(layout, item_ids, started_batches, shaped_trays) -> tuple[Layout, list[Surplus]]
```

规则见 `DOMAIN.md` 第 4、5 节，外加：
- 每缸同时受 `mixer_capacity_g` 和 `max_trays_per_batch` 限制；先算缸数，再把盘**平均**分（3、3、2、2，不是 3、3、3、1），细节见 `DOMAIN.md` 第 5 节第 3 条。
- **id 稳定**：`previous` 里已有的缸和盘保留原 id；新的缸、盘用新 id（例如 `B5`、`T11`），不复用已删除的 id。
- 已开始打面的缸（`locked_batches`）和已整形的盘（`locked_trays`）不改动分配。

测试至少要有：
- `DEMO_SCRIPT.md` 的 13 单得到 10 盘、4 缸（3、3、2、2）、5,760 g 面团；
- 同一版订单跑两次得到完全相同的布局；
- 加单时优先放进未整形同款盘的空位（且面团余量够），其次未开始的缸，最后新开一缸；
- 取消三种情况各一个测试（缸未打面 / 已打面盘未整形 → 面团余量 / 已整形 → 成品余量），并断言没有任何东西"变回原料"。

### 第 3 步：T6 剩余部分（1.5 小时）

`scheduler/explain.py`：`binding_facts(inp, request, earliest)`，只列能从数据直接读出的事实（规格第 6 节）。`scheduler/diff.py`：按订单对比完成时间、承诺是否守住、收工时间。全部用确定性模板生成中英文句子，不调模型。

### 第 4 步：T8 SQLite + T9 API（周五，约 5 小时）

- 依赖：加 `fastapi`、`uvicorn` 到 `pyproject.toml`。
- 表结构按 `DOMAIN.md` 第 2 节。`PlanVersion` 只追加，`snapshot_json` 存 Layout + 每步时间 + order_ready。
- 写接口统一要求 `Idempotency-Key`；批准用 `BEGIN IMMEDIATE`。
- 批准时的 5 步重新检查严格按 `API_SPEC.md` 第 6 节，其中第 3 步就是 `validate(inp, res, now=clock.now())`。
- 执行事件 → 生成 `frozen`：已开始的步骤 `Frozen(start=实际开始, end=实际结束或 None)`。
- 先测重启恢复和双击批准（`API_SPEC.md` 第 10 节），这两个是演示里的可靠性镜头。

### 第 5 步之后

按 `TASKS.md`：T10 接入模型 → T11 补齐评测集并封存 holdout → T12 前端 → T13 调演示种子（**注意 `DEMO_SCRIPT.md` 里的提示：4 缸已经接近一天上限，"加单能帮忙"那一幕可能需要减少种子订单**）→ T14 可靠性 → T15 真实使用 → T16 录屏 → T17–T20 文章和发布。

落后时按 `TASKS.md` 第 4 节的顺序砍。**永远不砍**：校验器、批准时重新检查、承诺保护、冻结事实、holdout 评测、文章。

## 6. 需要问用户的问题（不阻塞，先用默认值做）

都在 `DECISIONS.md` 的"待你确认"里。最重要的三个：

1. **Q10b：烤箱有没有独立的发酵柜？** 如果有，烤箱冲突大部分消失，产品和演示的重点要换。**最先问。**
2. **Q-W：三发结束后，面团最多能等多久进炉？** 决定每缸能放几盘。
3. **Q-N：平时一缸面分几盘？**

另外请用户提供真实订单写法，以及他这周哪天能在真实订单上试用（T15，文章最重要的素材）。

## 7. 和用户合作时

- 用户是面包师本人，用中文交流，喜欢直接的推荐加理由；有歧义时一次只问一个问题。
- 他会用到自己的真实订单，**顾客姓名不能进仓库**。
- 他的痛点原话：订单多了做不完、不知道先做什么几点开工、顾客问几点能拿只能估。所有功能都要能回到这三点。
