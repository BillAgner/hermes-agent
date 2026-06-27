---
name: research-tick-prompt
description: "The exact prompt body for the research tick job in the nightly-research-report pipeline. Copy/paste as the `prompt` arg to `cronjob create`."
---

# Research Tick Prompt (copy/paste into `cronjob create ... --prompt`)

```text
You are one ~3-minute tick of the nightly agentic-research pipeline. The job fires every 30 minutes from 1:00am to 3:30am Pacific (6 ticks/night). Your goal: do ONE focused slice of research, append findings to the running state file, and exit cleanly.

## TIME BUDGET
3-minute hard cap. If you overrun you will be killed mid-search. Stop researching and write findings by the 2:30 mark. If you're in a long extract, abort it.

## STATE FILE
`C:\Data\Hermes_0.17.0\cron\output\nightly-agent-research\YYYY-MM-DD.md`

**Note:** Path may vary by installation. Check your actual Hermes directory structure and update accordingly (e.g., `C:\Data\Hermes\` vs `C:\Data\Hermes_0.17.0\`).

- Use `date +%Y-%m-%d` to get today's date (Pacific).
- At the start of the tick, `ls` the directory to see if the file exists. If not, create it with the header block shown below.
- Read the current contents (it has prior ticks' findings).
- Append your new tick's section at the bottom. Do not overwrite prior ticks.

## WHAT TO RESEARCH
"Agentic skills, projects, architectures, cutting-edge improvements" — pick ONE lane per tick:

- arXiv papers (cs.AI, cs.MA, cs.CL, cs.LG) from the last 24h — use the `arxiv` skill
- GitHub Trending for: agent frameworks, MCP servers, skill libraries, ACP/A2A protocol implementations
- AI lab announcements (Anthropic, OpenAI, DeepMind, Meta AI, Mistral, Nous Research, xAI, DeepSeek, MiniMax, Kimi, Qwen)
- Hacker News top stories tagged AI/agent/MCP/LLM
- Reddit r/LocalLLaMA, r/MachineLearning top
- Specific topics: agent loops, tool use, memory architectures, context engineering, multi-agent coordination, evaluation/guardrails, prompt caching, MCP/ACP/A2A protocols, model routing, context compression, subagent orchestration, agent memory tiers

## METHOD
1. Read the state file's "Sources covered" and "Findings" sections. Pick a lane that hasn't been covered or deepen a thread that needs more detail.
2. Do 2-4 targeted searches. **If web_search/web_extract are unavailable**, fall back to arXiv-only research using the `arxiv` skill — this can still yield valuable findings. For arxiv, use the skill's helper script when Python is available, or manual XML parsing with grep/sed when not.
3. Find 1-3 NEW findings per tick. For each, capture:
   - Title + source URL
   - 1-2 sentence description
   - 1-2 sentence "why this matters for Hermes" (tie to actual Hermes architecture: skills, MCP servers, memory tiers, agent loop, tools, gateway, etc.)
   - Status: New | Repeat (covered in prior tick) | Already-available (we have it)
   - Action: install | configure | monitor-only | no-action
4. If you find nothing genuinely new this tick, increment the "Idle ticks" counter. After 2 idle ticks, add `## RESEARCH COMPLETE` to the bottom of the state file — this signals the morning job to stop waiting.
5. End your tick with a one-line summary printed to stdout: "Tick N done: X new findings, Y repeats, idle=N"

## STATE FILE FORMAT

First tick creates the header:
```markdown
# Nightly Agentic Research — YYYY-MM-DD

## Sources covered so far
- (none yet)

## Findings so far
- (none yet)

## Idle ticks: 0

---

## Tick 1 — YYYY-MM-DD HH:MM
Lane: <lane name>
Searches: <comma-separated query list>

### Findings
1. [Title](URL) — description — *Why for Hermes: ...*
   Status: New
   Action: install
2. ...

### Sources covered this tick
- url1
- url2
```

Subsequent ticks append new `## Tick N` blocks AND update the "Sources covered so far" and "Findings so far" sections at the top.

## RULES
- DO NOT install anything. DO NOT write to other files. Research only.
- DO NOT call `hermes skills install` — the morning report proposes installs for the user to approve.
- If `blogwatcher-cli` is not installed (verify with `which blogwatcher-cli`), note it as a finding (Status: not-installed, Action: install) but don't install it yourself.
- **If web_search/web_extract tools are not configured**, use direct API calls via curl + python JSON parsing instead. Don't fail the tick over missing web tools.
- Don't fabricate URLs or claims. If you can't verify, say so explicitly.
- Self-limit: max 10-15 papers, max 20 web pages per tick. Don't go down rabbit holes.
- Don't read the same source twice across ticks — check the "Sources covered" list first.

## DELIVERY
`local` — no Telegram, no other channels. Just write the state file and exit.
```
