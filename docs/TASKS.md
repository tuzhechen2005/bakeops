# 任务清单

> 截止：**10 月 25 日 23:59 PDT = 你那边 10 月 26 日（周一）01:59**。目标是周日 20:00 前发布，留出缓冲。
> 工时为估计，总计约 40 小时。请按你实际能用的时间调整；落后时按第 4 节砍。
> 规则提醒：仓库必须在挑战窗口内创建（10 月 19 日之后），所以今天才建仓库是合规的。

## 1. 仓库结构

```
bakeops/
  README.md               英文，评审会看
  LICENSE                 Apache-2.0
  docs/                   本套文档
  config/kitchen.yaml     你的真实参数（不进仓库，见 .gitignore）
  config/kitchen.example.yaml
  demo/seed.yaml          演示用虚构数据
  eval/dev.jsonl  eval/holdout.jsonl  eval/run_eval.py
  backend/bakeops/
    domain/      实体、装盘分缸规则、面团账
    scheduler/   model.py（CP-SAT）、validator.py、explain.py、diff.py
    llm/         interpreter.py、prompts/、timeparse.py、qtyparse.py
    db/          schema.sql、repo.py
    api/         FastAPI 路由、幂等中间件、clock.py
  backend/tests/
  frontend/      React + TypeScript + Vite
```

## 2. 按天任务

### 周二 10/20 晚（1.5 h）

- [x] **T0 建仓库**（10/21 完成）：`docs/`、LICENSE、README、`pyproject.toml`、`.gitignore`、`CLAUDE.md`、`docs/HANDOFF.md`。
- [ ] **T0b 拉模型**：`ollama pull gemma4:e4b`，备选 `gemma4:12b-it-qat`。
- [ ] **T0c 回答 `DECISIONS.md` 里的 Q10b、Q-W、Q-N**。

### 周三 10/21：两个风险验证（5 h）＋ 文章提纲

- [ ] **T1 模型风险验证（2 h）**：代码已写好，**你在自己电脑上跑**。
  - 已有：`eval/dev.jsonl` 里 6 条拆订单、19 条消息（其中 6 条有歧义），全部是虚构的起步数据。
  - 你要做：从本子里抄至少 5 段真实订单（改名字），写进 `eval/dev.jsonl`，标 `"synthetic": false`。格式照抄已有的几行。
  - 跑：`python eval/run_eval.py --model gemma4:e4b`
  - **通过标准**：订单行 ≥ 80% 拆对；歧义消息（单靠模型）≥ 4/6 停下；JSON 失败为 0；加了程序把关后改错订单为 0。
  - **不通过**：换 `gemma4:12b-it-qat` 再试；仍不通过 → 订单导入退回手动表单，模型只用于消息理解。
- [x] **T2 排程风险验证**（10/21 完成，通过）：见 `DECISIONS.md` 的"风险验证结论"和 D24–D28。`python spikes/t2_scheduler.py` 可以看到四个场景。
  - 附带完成了 T5 的核心（约束、三种结果、最早时间）、T6 的校验器、T7 的大部分测试。
- [ ] **T3 文章提纲（0.5 h）**：按 `ARTICLE_OUTLINE.md` 写下开头一段和"Why Open Innovation Matters"的要点。
- [ ] **周三晚检查点**：T1、T2 都有结论。把结论写进 `DECISIONS.md`。

### 周四 10/22：排程核心（7 h）

- [ ] **T4 领域与装盘分缸（1.5 h）**：Pydantic 实体；`DOMAIN.md` 第 5 节规则（含每缸最多几盘）；面团账（第 4 节）。测试：演示数据得到 10 盘、4 缸（3、3、2、2）。
- [ ] **T5 完整排程（3 h）**：`SCHEDULER_SPEC.md` C1–C8、四种场景的字典序目标、三种结果、最早时间、替代方案。
- [ ] **T6 校验器 + 原因说明 + 差异（1.5 h）**。
- [ ] **T7 排程测试（1 h）**：`SCHEDULER_SPEC.md` 第 9 节清单，含一个 hypothesis 性质测试。
- [ ] **晚上**：写文章的"How I Built It"排程部分（趁记忆新鲜，0.5 h）。

### 周五 10/23：持久化、API、模型接入（8 h）

- [ ] **T8 SQLite 与仓储（1.5 h）**：建表、PlanVersion 追加写、幂等记录、模拟时钟。
- [ ] **T9 API（3 h）**：`API_SPEC.md` 全部接口；批准时的 5 步重新检查；承诺创建检查。
- [ ] **T10 模型接入（2 h）**：`LLM_CONTRACT.md` 两个任务、时间与数量的 Python 复核、重试、多订单强制确认。
- [ ] **T11 完整评测集（1.5 h）**：补齐 dev 集，**写好 holdout 集并封存**（之后不看、不调）。

### 周六 10/24：界面、演示、真实使用、录屏（9 h）

- [ ] **T12 前端（4 h）**：三个区域
  - 今日计划：人手、烤箱（发酵 / 烘烤两色）、室温三条泳道；已开始步骤实心带锁；承诺带标记；候选方案用另一种样式叠加或并排，不能和正式计划混淆。
  - 变更审查：原文、理解结果（可修改）、目标订单选择、差异、相关事实、回复草稿、批准 / 拒绝 / 已发送按钮。
  - 执行记录：开始 / 完成按钮、合并日志。
  - 订单导入表格。
- [ ] **T13 演示种子调参（1 h）**：跑 `DEMO_SCRIPT.md`，调测试参数直到每幕结果类型正确；把实际时刻填回文档；每幕写成回归测试。
- [ ] **T14 可靠性测试（1 h）**：重启恢复、双击批准、过期方案、重复消息。
- [ ] **T15 真实使用（≥ 1 h，可以和工作重叠）**：用你当天或周日的真实订单跑一次。记下：以前排一天要多久、这次多久、哪里不准、有没有帮你答应或拒绝了哪单。**这是文章最重要的素材。**
- [ ] **T16 录屏（2 h）**：按演示脚本，3–4 分钟，英文字幕。

### 周日 10/25：文章与发布（5 h ＋ 缓冲）

- [ ] **T17 跑 holdout 评测（0.5 h）**：结果表放进文章和 README。
- [ ] **T18 写完文章（3 h）**：按 `ARTICLE_OUTLINE.md`。
- [ ] **T19 README（1 h）**：英文；安装步骤、Ollama 模型、如何跑演示、如何跑测试、评测结果、限制与声明（"测试参数不是烘焙建议"）。
- [ ] **T20 发布（0.5 h）**：DEV 用官方模板发布，带 `#devchallenge #hf26challenge`；Prize Categories 只写 Best Use of Gemma。**20:00 CT 前发布。**
- 截止后若还有提交，在 README 末尾注明（规则要求）。

## 3. 每项任务的完成标准

一项任务只有满足以下全部才算完成：
- 相关测试通过；
- 如果它改变了任何决策，`DECISIONS.md` 已更新；
- 没有引入任何违反 `PRODUCT.md` 第 7 节底线的捷径。

## 4. 落后时的砍法（按顺序砍，砍到追上为止）

1. 英文释义（P1）
2. 余量自动用于新单（P1）
3. hypothesis 性质测试（保留普通单元测试）
4. 替代方案自动生成 → 改为只显示"最早 X 点"文字
5. 前端时间轴美化 → 用最简单的表格加色块
6. 订单的模型拆分 → 手动表单（模型只保留消息理解）

**永远不砍**：校验器、批准时重新检查、承诺保护、冻结事实、holdout 评测、文章。

## 5. 用编程助手时

- 每次让助手做一项任务时，把对应的 `docs/*.md` 一并给它，并要求先写测试。
- 如果用 DevRelay 保存会话，可以放进文章的"My Agent Session"一节（可选，评审喜欢）。
