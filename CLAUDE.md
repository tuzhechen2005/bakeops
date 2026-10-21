# BakeOps — instructions for Claude

一人面包工作室的当天排程工具。作者本人就是面包师，也是用户。
参赛：DEV Hacktoberfest Open-Source AI Challenge Week 3（Slow and Steady）。
**截止：2026-10-25 23:59 PDT = 10-26（周一）01:59 美国中部时间。目标周日 20:00 CT 前发布。**

开始任何工作前先读 `docs/HANDOFF.md`（现状和下一步），再读当前任务对应的 spec。

## 文档地图

- `docs/PRODUCT.md`：产品定义、范围、底线（第 7 节）
- `docs/DECISIONS.md`：所有决策及来源。**spec 与它冲突时以它为准**；改决策要追加一行
- `docs/TASKS.md`：按天的任务、完成标准、落后时的砍法
- `docs/DOMAIN.md` / `SCHEDULER_SPEC.md` / `LLM_CONTRACT.md` / `API_SPEC.md`：各模块规格
- `docs/DEMO_SCRIPT.md`：录屏脚本；`docs/ARTICLE_OUTLINE.md`：参赛文章提纲

## 命令

```bash
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                                # 全部测试，应在几秒内通过
python spikes/t2_scheduler.py         # 排程四个场景
python eval/run_eval.py --model gemma4:e4b   # 模型评测（需本机 Ollama）
```

## 不能违反的规则

1. **模型只理解文字，不判断能不能做到。** 模型输出必须经过 `backend/bakeops/llm/checks.py`；时间、数量以 Python 复核结果为准。
2. **顾客有多笔订单时，面包师必须亲手选目标订单。** 模型的选择只能高亮，不能生效。
3. **已发生的步骤（frozen）不可被求解器改动；** 两个都已发生的步骤之间的约束跳过，事实不再被评判。
4. **候选方案 ≠ 正式计划。** 批准时用当前时钟重新校验，过期就拒绝。
5. **承诺只在面包师把回复标为"已发送"后产生，** 新方案必须守住所有承诺。
6. **三种结果不能混：** feasible（且通过 validator）/ infeasible（"按现在的分缸方式和参数做不到"）/ not_found（"X 秒内没找到"，绝不说成做不到）。
7. **`scheduler/validator.py` 不能 import OR-Tools。** 它是独立复核。
8. 求解器单线程（`num_workers=1`），保证同样输入得到同样计划。
9. 不编造烘焙参数，不判断食品安全。所有时间、容量来自配置；示例值是测试值，要标明。
10. 不接 Temporal、Auth0、LangGraph、向量库等用不上的东西。

## 数据与隐私

- `config/kitchen.yaml`（真实参数）、数据库文件不进仓库（已在 `.gitignore`）。
- 仓库里的订单、顾客一律用化名。加真实评测数据前先改名字。
- `eval/holdout.jsonl` 写好后封存：不看、不调提示词，只在最后跑一次。

## 工作方式

- 先写测试再写实现；每个任务完成后 `pytest` 全绿再提交。
- 每完成一个 `TASKS.md` 里的任务就提交一次，提交信息写清做了什么。
- 改变了任何决策 → 更新 `docs/DECISIONS.md`；做完任务 → 在 `docs/TASKS.md` 打勾。
- 用户用中文交流；代码、注释、提交信息、README 用英文。界面文字英文，顾客消息和回复草稿保持中文。
- 截止后如果还有提交，必须在 README 末尾注明（比赛规则）。
