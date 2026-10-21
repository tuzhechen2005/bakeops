# BakeOps

**Paste today's orders. Get a baking plan that fits one person and one oven. When a customer asks "when?" or "can I add more?", get an answer you can actually keep.**

BakeOps is built by a one-person baker for a one-person bakery: dough is mixed on the day, busy days need four batches, and the only oven is also the proofer, so it cannot proof and bake at the same time.

- **A local open-weight model (Gemma 4 via Ollama)** reads messy Chinese text: handwritten-style order lists and customer messages.
- **Python checks every model answer** (quotes must be in the original text, times and quantities are re-parsed by rules, the baker always picks the order when a customer has more than one).
- **OR-Tools CP-SAT plans the day**: one pair of hands, one chamber that switches between proof and bake, proof windows, promises that must be kept, and steps that already happened stay fixed.
- **An independent validator** re-checks every plan without the solver.

Built for the [DEV Hacktoberfest Open-Source AI Challenge, Week 3: Slow and Steady](https://dev.to/challenges/hacktoberfest-week3-2026-10-19). Repository started 2026-10-21, inside the challenge window.

> **Status:** work in progress. The scheduler and the model checks work; the API and UI are next. See [`docs/TASKS.md`](docs/TASKS.md).

> **Not baking advice.** All times and capacities in this repo are test values for software testing. BakeOps never invents proof times or judges food safety; the baker enters every number.

## Quick start

Requires Python 3.11+.

```bash
git clone https://github.com/tuzhechen2005/bakeops
cd bakeops
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                              # ~65 tests, a few seconds
```

### See the scheduler work

```bash
python spikes/t2_scheduler.py
```

Four scenarios with test values: a morning plan; a request that is too early (refused, with the proven earliest time); a request that works by swapping two batches when nobody has been promised a time yet; and the same request refused once a time has been promised to someone else.

### Run the model check

Install [Ollama](https://ollama.com), then:

```bash
ollama pull gemma4:e4b
python eval/run_eval.py --model gemma4:e4b
```

It prints a table (orders parsed right, intent accuracy, whether the model stopped on ambiguous messages, whether a wrong order was ever used) and writes per-item results to `eval/results/`. Try a larger size with `--model gemma4:12b-it-qat`. Run a few items with `--only m03,m11`.

## Layout

```
docs/                 product, decisions, specs, tasks (start with PRODUCT.md)
config/               kitchen config; kitchen.example.yaml has test values
backend/bakeops/
  scheduler/          CP-SAT model, validator, config -> solver input
  llm/                Ollama client, prompts, post-model checks, time/qty parsers
backend/tests/
eval/                 model eval set and runner
spikes/               risk checks
```

## License

Apache-2.0
