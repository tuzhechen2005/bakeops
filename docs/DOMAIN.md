# 领域模型

> 所有时间以"当天 00:00 起的分钟数"存储（整数），界面显示为 HH:MM。只排一天。

## 1. 配置（`config/kitchen.yaml`，由面包师录入）

> **以 `config/kitchen.example.yaml` 为准**，下面是最初的草稿，已过时的地方见 `DECISIONS.md` D24–D25：
> 时间参数写成 `{value, confirmed}`；新增 `max_trays_per_batch`、`shape_slack_min`；四款产品共用 `tray_steps`。

```yaml
day:
  earliest_start: "06:00"      # 最早愿意开工
  latest_end: "20:00"          # 最晚收工（硬约束）
oven:
  proof_capacity_slots: 4      # 发酵模式下的位置数
  bake_capacity_trays: 2       # 烘烤模式下能同时烤几盘
  switch_bake_to_proof_min: 25 # 烤后降温到可发酵
  switch_proof_to_bake_min: 15 # 发酵后预热到烘烤
dough:
  base_dough_id: base
  mixer_capacity_g: 4000
  surplus_ratio: 0.0           # 未确认 → 0
  steps:                       # 每缸面团共有的前段
    - {id: mix,    where: person,     duration_min: 15}
    - {id: bulk,   where: oven_proof, duration_min: 50, slots: 2, max_wait_after_min: 0}
    - {id: divide, where: person,     duration_min: 10, per_tray_extra_min: 2}
    - {id: rest,   where: room,       duration_min: 20, max_wait_after_min: 0}
products:
  - id: red_bean
    name_zh: 红豆包
    aliases: [红豆, 红豆面包]
    piece_dough_g: 60
    pieces_per_tray: 12
    steps:                     # 每盘的后段
      - {id: shape, where: person,     duration_min: 12}
      - {id: proof, where: oven_proof, duration_min: 40, slots: 1, max_wait_after_min: 0}
      - {id: bake,  where: oven_bake,  duration_min: 15}
      - {id: cool,  where: room,       duration_min: 20}
  # cheese / pork_floss / milk_roll 同理
packaging:
  per_order_min: 3             # 每单包装，占用人手
policy:
  locked_after_order: [change_quantity, change_product]
```

**规则**：
- `where` 取值：`person`（占用你）、`oven_proof`、`oven_bake`、`room`（不占任何人或设备，容量不限）。
- `max_wait_after` 表示这一步结束到下一步开始最多能等多久。**必须有数值**，不能默认为 0（D24）；没试过的填估计值并标 `confirmed: false`。
- 演示数值全部是软件测试值，不是烘焙建议。真实数值由面包师录入。

## 2. 实体

### Customer
`id, display_name, aliases[]`。演示与仓库数据用化名。

### Order
`id, day, customer_id, status(active|cancelled), source_text_id, created_at`

### OrderItem
`id, order_id, product_id, qty, status(active|cancelled)`

### Promise
`id, order_id, ready_by_min, reply_id, created_at`
- 只能由"回复已发送"创建（D12）。
- 一笔订单最多一个有效承诺；新承诺替换旧承诺需经批准。

### DoughBatch（缸次）
`id, day, seq, planned_g, status(planned|mixing|mixed|consumed)`
- 一缸属于一个 plan version 的计划，但一旦开始打面，其事实（实际开始时间、实际克数）冻结。

### Tray（烤盘）
`id, batch_id, product_id, pieces, status(planned|shaped|proofing|baking|cooling|done|discarded)`

### TrayAllocation
`tray_id, order_item_id, pieces` —— 一盘可服务多位顾客的同款品项（D15）。

### Step（工序实例）
`id, owner_type(batch|tray|order), owner_id, step_def_id, where, duration_min, planned_start, actual_start?, actual_end?, status(planned|started|done)`
- 包装步骤 owner 为 order。

### Surplus（余量）
`id, kind(dough|product), batch_id?, tray_id?, product_id?, amount (g 或 个), reason, status(open|resolved)`

### SourceText
`id, kind(order_list|message), raw_text, customer_id?, received_at, content_hash`

### Interpretation
`id, source_text_id, model, model_output_json, validated_json, status(needs_review|confirmed|rejected), errors[]`

### PlanVersion
`id, day, version (递增), snapshot_json, created_from_proposal_id, created_at`
- 正式计划永远是最新的 PlanVersion。只追加，不修改。

### Proposal（候选方案）
`id, base_version, kind(initial|message), interpretation_id?, solver_status, candidate_snapshot_json, diff_json, reply_draft_id?, computed_at_min, status(draft|approved|rejected|stale)`

### ReplyDraft
`id, proposal_id, text_zh, text_en_gloss?, ready_by_min?, status(draft|sent)`

### ExecutionEvent
`id, step_id, type(start|finish), at_min, idempotency_key`

### IdempotencyRecord
`key, endpoint, response_json, created_at`

## 3. 状态机

```
Proposal:  draft ──approve(校验通过)──▶ approved
             │ └──approve(版本变了/时间过了)──▶ stale
             └──reject──▶ rejected

Step:      planned ──start──▶ started ──finish──▶ done
           (started / done 之后 planned_start 不再被求解器修改，实际时间为准)

ReplyDraft: draft ──mark_sent──▶ sent (若含时间 → 创建 Promise)

Order:     active ──cancel(经批准)──▶ cancelled
```

## 4. 取消时的面团账（替代原料库存，D19）

| 取消时这一份品项的状态 | 处理 |
|---|---|
| 所在缸还没开始打面 | 从计划中移除，缸次克数重算，不产生余量 |
| 已打面，所在盘还没整形 | 对应面团克数记为 `Surplus(kind=dough)` 挂在这一缸；盘的个数减少，个数为 0 的盘删除 |
| 已整形或之后 | 对应个数记为 `Surplus(kind=product)`；盘照常做完，不改动 |

**绝不发生**：取消后把面团"变回"原料，或减少一个已开始步骤的实际数量。

## 5. 装盘与分缸规则（确定性，D16）

1. 把所有有效 OrderItem 按（是否有承诺、承诺时间、订单创建时间）排序。
2. 按品项分组，同款依次装满 `pieces_per_tray`，最后一盘可不满。
3. 盘按"第一个被装入的品项的顺序"排序。缸数 `n = max(⌈盘数 / max_trays_per_batch⌉, ⌈总面团 / (mixer_capacity_g / (1 + surplus_ratio))⌉)`（D25）。再把排好序的盘**尽量平均**地依次分给 n 缸，多出来的盘给前面的缸（10 盘、每缸最多 3 盘 → 4 缸：3、3、2、2，而不是 3、3、3、1）。分完后逐缸检查面团不超过搅拌机容量，超了就 n+1 重分。平均分是为了让每缸的烤箱占用接近，避免最后一缸只有一盘却要单独打面。
4. 已开始打面的缸次与已整形的盘保持原分配不变。新加的品项按以下顺序找位置，找到即停：
   1. 还没整形的同款盘的空位，**且**所在缸的面团余量（`Surplus(kind=dough)`）够用；
   2. 还没开始打面的缸次（重算克数，不超过搅拌机容量）；
   3. 新开一缸。

这个规则不追求最优。它的输出是求解器的输入；求解器只决定每一步几点开始。
