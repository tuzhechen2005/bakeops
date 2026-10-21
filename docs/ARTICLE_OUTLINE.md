# Article Outline (DEV submission)

> Write in English. Judges weight **Writing Quality** most, then **Relevance to the theme**, **Creativity**, **Technical Execution**.
> Every number in brackets `[ ]` must come from a real measurement or the holdout eval. Do not invent any of them.

## Title options

1. *My oven is also my proofer. So I taught a local Gemma to plan my baking day.*
2. *The sloth baker: paste my orders, go back to my dough*
3. *A bakery planner that says "no" when it should*

Pick one that leads with the personal problem, not the tech.

## Cover image

A real photo of your notebook or your oven (no customer names visible), not an AI image.

---

## What I Built

- Open with **you**: one sentence on who you are (a one-person bakery), one on the pain (four batches of dough, three proofs, one oven that is also the proofer).
- A concrete moment: *a customer messages "can I add six cheese buns by three?" while your hands are in dough.*
- Then the product in one sentence: *Paste today's orders, get a plan that fits one person and one oven. Paste a customer's message, get an answer you can actually keep.*
- Who gets to be lazier: you, and any one-person kitchen built the same way (one key machine that switches modes, steps that can't wait long, promises made in chat).

## Demo

- Embed the 3–4 minute video (English subtitles).
- Under it, three screenshots with one-line captions:
  1. The morning plan: oven lane alternating proof / bake.
  2. The "yes": adding an order while every promised time is kept.
  3. The "no": the earliest time it can actually do, with the facts that block it.

## Code

- Embed the GitHub repo.
- One line on how to run the demo locally (`ollama pull …`, `make demo`).

## How I Built It

Keep the split clear — this is the heart of the technical story:

**The model understands words. The program decides what's possible.**

1. **Gemma (local, via Ollama)** turns messy Chinese text into validated JSON:
   - my notebook shorthand ("张三 红豆包4个 奶酪包4个 李四 奶酪包四个", no line breaks between customers);
   - customer messages ("再加两个" vs "总共两个", "四点" with no AM/PM, which of two orders).
   - It is never asked whether something is feasible.
2. **Python re-checks the model**: evidence must be a substring of the original text; times and quantities are re-parsed by rules; if a customer has more than one order, I always pick the target myself.
3. **OR-Tools CP-SAT** schedules the day: one person, oven modes with switch-over time, proof windows, promises as hard constraints, things that already happened frozen.
4. **An independent validator** re-checks every plan without the solver, again at the moment I click approve.
5. **Three honest outcomes**: a checked plan; "can't, and here is the earliest I can"; "didn't find one in 5 s" — never mislabeled as impossible.
6. Small diagram: message → Gemma → validation → (you confirm) → solver → validator → diff → (you approve) → reply draft → (you send) → promise.

A short "what I cut" paragraph: no raw-ingredient inventory, one day only, no auto-sending messages. Judges trust scoped projects.

## Why Does Open Innovation Matter?

Make each point specific to this kitchen:

- **Customer data stays in my kitchen.** Names and orders never leave my laptop.
- **Works when the Wi-Fi doesn't.** Kitchens are bad at Wi-Fi; local inference doesn't care.
- **Costs nothing per message.** [N] messages on a busy day, no meter running.
- **The model is a replaceable part.** Because every output is validated, I could swap Gemma sizes and just re-run the eval. Show the table:

| Model | Wrong-order (raw) | Wrong-order (system) | Ambiguity stop rate | Intent accuracy | Order-parse accuracy |
|---|---|---|---|---|---|
| Gemma [size A] | [ ] | 0 | [ ] | [ ] | [ ] |
| Gemma [size B] | [ ] | 0 | [ ] | [ ] | [ ] |

- **Another baker can fork it.** Steps, oven, products are all config; prompts and eval set are in the repo.

## My Agent Session (optional)

If you used a coding agent, link the DevRelay session. One sentence on how the docs in `docs/` were fed to the agent task by task.

## The Lazy Payoff

This is where a real baker beats every synthetic demo. Only real numbers:

- How long morning planning used to take: [your estimate]
- How long it took with BakeOps on [date]: [measured]
- Customer questions answered that day: [ ]; orders it told me not to accept by the time asked: [ ]
- One honest thing it got wrong or I had to fix by hand.

If you could not use it on a real day before the deadline, delete this section rather than estimate.

## Prize Categories

- Best Use of Gemma

---

## Writing checklist

- [ ] First paragraph mentions the sloth theme in a human way (the lazy baker, the steady planner), not as a gimmick.
- [ ] Every Chinese example has an English gloss.
- [ ] Disclaimer: demo timings are test values, not baking advice; the system never invents proof times or judges food safety.
- [ ] No claim the solver is "optimal" unless CP-SAT returned OPTIMAL.
- [ ] Under 2,000 words; headers match the template.
