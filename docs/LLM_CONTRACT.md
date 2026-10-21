# 模型契约

> 模型只负责"理解文字"，不负责"判断能不能做"。所有输出必须通过 Pydantic 校验和业务校验后才能进入排程。

## 1. 运行方式

- 本地推理：Ollama + Gemma 4 开放权重模型。
- 具体尺寸按评测集结果选定：先试 `gemma4:e4b`，不达标再试 `gemma4:12b-it-qat`，选**能达到第 5 节门槛的最小尺寸**。写进 `README` 和文章。
- 实现：`backend/bakeops/llm/`（`interpreter.py` 调模型，`checks.py` 做程序把关，`timeparse.py`、`qtyparse.py` 复核时间和数量，`prompts/` 是提示词）。
- 调用参数：`temperature=0`，使用 Ollama 的 JSON schema 结构化输出（`format` 传入 Pydantic 生成的 schema）。
- 模型接口抽象为 `Interpreter` 协议，便于替换模型或接入 mock（测试用）。

## 2. 任务 A：拆订单（`parse_orders`）

**输入**
- 粘贴的原文
- 产品目录（id、中文名、别名）
- 已知顾客列表（id、名字、别名）

**输出 schema**

```python
class ParsedItem(BaseModel):
    raw_product_text: str
    product_id: str | None        # 必须在目录里，否则为 None
    qty: int | None
    qty_raw: str                  # 原文写法，例如 "四个"
class ParsedOrder(BaseModel):
    raw_customer_text: str
    customer_id: str | None       # 对不上已知顾客 → None，界面让你新建或选择
    items: list[ParsedItem]
    note: str | None
    evidence: str                 # 这一单在原文中的原句，必须是原文的子串
class ParseOrdersOutput(BaseModel):
    orders: list[ParsedOrder]
    unparsed: list[str]           # 看不懂的原文片段
```

**业务校验（Python，模型之后）**
- `evidence` 必须是原文子串，否则整行标红。
- `product_id` 不在目录 → 标红。
- `qty` 由 Python 对 `qty_raw` 再解析一次（支持阿拉伯数字和中文数字），两者不一致 → 标红。
- 原文中任何没有被某个 `evidence` 覆盖、也没出现在 `unparsed` 里的非空白片段 → 作为"可能漏掉"提示。

**示例**（来自你的本子写法）

```
周一：张三 红豆包4个 奶酪包4个 李四 奶酪包四个
```
→ 两单：张三（红豆包 4、奶酪包 4），李四（奶酪包 4）。
难点：顾客之间没有换行或标点，模型要按"人名 + 品项"切分。

## 3. 任务 B：理解顾客消息（`interpret_message`）

**输入**
- 消息原文
- 发件人 `customer_id`（由你选择，必填）
- 该顾客当天的有效订单（id、品项、当前完成时间、承诺时间）
- 产品目录
- 当前时间、当天营业时间

**输出 schema**

```python
Intent = Literal[
  "ask_ready_time",    # 几点能好 / 能拿
  "request_ready_by",  # 几点前要 / 能不能 X 点拿
  "add_items",         # 加单或新下一单
  "cancel",            # 取消整单或某品项
  "change_quantity",   # 店规不允许
  "change_product",    # 店规不允许
  "other",
]
class MessageInterpretation(BaseModel):
    intents: list[Intent]                 # 允许组合，例如 add_items + request_ready_by
    target_order_id: str | None
    target_candidates: list[str]          # 有多个可能时列出，target_order_id 必须为 None
    requested_time_raw: str | None        # 例如 "四点"
    requested_time_min: int | None        # 模型给的解析结果，Python 会复核
    add_items: list[ParsedItem]
    cancel_items: list[str]               # order_item_id；空列表且有 cancel 意图 = 整单
    needs_clarification: bool
    clarification_question: str | None    # 给面包师看的中文问题
    evidence: list[str]                   # 每个字段依据的原文片段
```

**硬规则（写进提示词，并由 Python 强制）**
1. 该顾客当天有多笔有效订单、消息又没说清是哪笔 → `target_candidates` 列出全部，`needs_clarification = true`。**Python 再加一道**：只要该顾客当天有效订单多于 1 笔，无论模型选了哪笔，界面都要求你点选确认目标订单（模型的选择只作为默认高亮）。只有 1 笔时直接使用。这样系统层面不可能静默改错订单。
2. 时间：
   - Python 用规则解析 `requested_time_raw`，支持"16:00""4点""四点半""下午四点""4:30"。
   - "四点"这种没写上下午的，只保留落在营业时间内的解释；如果两种解释都在营业时间内，则需要澄清。
   - 模型的 `requested_time_min` 与 Python 结果不一致 → 以 Python 为准并记录，供评测统计。
3. "加两个"是增量；店规不允许改数量，所以对已有订单"再加两个"按 **add_items（新的一单）** 处理，并在澄清问题里写明"按新加一单处理，对吗？"。"总共两个"是绝对值，属于 change_quantity，按店规回复。
4. 模型不得输出任何关于发酵时长、温度、能否做到的判断；只输出理解结果。

## 4. 失败处理

- JSON 不合法或 schema 校验失败 → 带错误信息重试 1 次 → 仍失败则标为"需手动处理"，界面显示原文，不做任何改动。
- 模型超时（30 秒）→ 同上。
- 所有原始输出、校验后结果、Python 的修正都存进 `Interpretation`，可在执行记录中查看。

## 5. 评测集（`eval/`）

**先写评测集，再调提示词。** 评测集分两份：
- `eval/dev.jsonl`：调提示词时可以看。
- `eval/holdout.jsonl`：写好后封存，只在最后跑一次，结果写进文章。

每条格式：
```json
{"id": "m017", "task": "interpret_message",
 "input": {"text": "...", "customer_id": "c3", "orders": [...], "now": "11:05"},
 "expected": {"intents": ["request_ready_by"], "target_order_id": null,
              "needs_clarification": true, "requested_time_min": 960},
 "tags": ["ambiguous_order", "time_no_ampm"]}
```

**覆盖要求**（两份合计）：
- 拆订单 ≥ 15 条，至少 5 条模仿你本子的连写格式，含中文数字、别名、错别字。
- 消息 ≥ 30 条，覆盖每个意图；其中 ≥ 8 条歧义（多笔订单、时间无上下午、"再加"与"总共"）。

**指标与门槛**

| 指标 | 门槛 |
|---|---|
| 系统层面改错订单（经过 Python 强制规则之后） | **0** |
| 模型原始输出改错订单 | 如实报告，不设门槛 |
| 歧义时正确停下（`needs_clarification` 召回率） | ≥ 90% |
| 意图完全正确 | ≥ 85% |
| 拆订单：品项与数量完全正确的行占比 | ≥ 90% |

两行"改错订单"分开统计：原始输出那一行用来如实报告模型本身的能力；系统层面那一行证明"模型理解 + 程序把关"的设计确实挡住了错误。这个对比本身就是文章里的好素材。
