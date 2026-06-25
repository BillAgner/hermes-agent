---
name: daily-news-digest
description: "Recurring YouTube news-channel digest job. Use when the user asks to 'find today's video from [channel] and analyze it,' wants a 'daily news digest' from a named YouTube news show, or runs a scheduled job that ingests a channel's latest episode (e.g. Shared Sapience / The Century Report, The Information, Marques Brownlee, whatever). Orchestrates the full pipeline (find, validate, fetch, analyze, deliver) and delegates the analysis step and format spec to the youtube-news-digest skill."
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [youtube, cron, news, digest, pipeline, orchestration, delivery]
    related_skills: [youtube-content, youtube-news-digest]
---

# Daily News Digest

A repeatable pipeline for the recurring job pattern: "find the latest news episode on YouTube channel X, validate it, summarize it, deliver the digest via messaging platform."

## Trigger

This is the right skill when the user says things like:

- "Find today's video from [channel name] and summarize it"
- "Run the daily news digest"
- "Analyze the latest [show name] episode"
- A scheduled job that hits one specific YouTube news channel on a daily/weekly cadence and emits a digest

If the user just shares a single video URL with no recurring framing, the bare `youtube-content` skill is enough — don't pull this one in.

## Pipeline

1. **Find the latest video** — use the channel RSS feed, not the channel page or the browser. The `youtube-content` skill documents the exact `curl` recipe and the Shorts-vs-full-episode discriminator. Don't re-derive it.
2. **Validate** — before fetching the transcript, confirm:
   - **Not music / reaction / entertainment** — read the title and (if needed) the RSS `<media:description>`. News shows name the date, the host, or the day's stories in the title. If the title is a song, a meme, or a "REACTING TO…" framing, stop and report "no valid news video."
   - **Within the freshness window** — typically last 24–48h. Parse `<published>` from the RSS and compare. If the freshest full episode is older than the window, don't fall back to a Short.
   - **Not already analyzed** — `session_search` for the video ID. If a prior transcript/summary exists, return the cached version with a "Repeat:" prefix instead of re-analyzing.
3. **Fetch the transcript** — delegate to the `youtube-content` skill's helper script. If `uv run` fails on the venv, fall back to system `python` (see pitfalls in `youtube-content`).
4. **Analyze** — load and follow the `youtube-news-digest` skill. It owns the canonical TL;DR + Full Analysis format and the workflow for producing it. Do not re-derive the format spec here; the format skill is the source of truth. If the video has a single topic with multiple angles (interview, monologue, single-issue deep-dive), the format skill handles expansion to 5 sections automatically.
5. **Deliver** — see Delivery section. If delivery fails, the digest content is still the deliverable; surface the failure honestly.

## Default Output Format

The format is owned by the `youtube-news-digest` skill. Load that skill when you reach the analyze step and follow its template:

```
📰 Daily News Digest — [Month DD, YYYY]
[Video Title]
Source: [YouTube watch URL]

🔥 TL;DR
[5 bullets, each leading with a concrete fact, plus 1-line throughline]

📋 Full Analysis
[5 sections with load-bearing detail, quotes, and a closing synthesis]
```

The format skill specifies the exact rules (bullet length, lead-with-fact, throughline-vs-summary, hedge preservation, 800-1500 word target). Do not re-specify them here — call out to the format skill and follow what it says.

**Override:** if the user explicitly asks for a different format (chapters, thread, blog post, raw transcript), bypass the format skill and use `youtube-content` directly.

## Delivery

The `youtube-news-digest` format skill (loaded at the analyze step) accepts a `deliver_to` parameter. Pass the configured target through to it:

- From a cron job: read the job's `deliver` field and pass it as `deliver_to` to the format skill.
- From an interactive chat: pass whatever target the user specifies, or leave it unset if they just want the digest in the chat response.
- From any other caller: pass through whatever delivery target was configured.

The format skill handles the actual `hermes send` invocation and the platform-configured check. The orchestrator's job is just to forward the parameter.

The user may say "use the `send_message` tool" — that tool is **not** registered in most sessions' function sets. The actual mechanism is the Hermes CLI:

```bash
hermes send --list                       # see configured platforms
hermes send --to telegram "message body" # send to home channel on platform
hermes send --to telegram:CHAT_ID "..."  # send to specific chat
hermes send --to discord:#ops "..."      # send to named channel
hermes send --to telegram --file PATH    # send file contents
```

`hermes send --list` reporting **"No messaging platforms configured"** means no platform is wired up — the fix is `hermes gateway setup` (interactive, picks Telegram/Discord/Slack/Signal/etc. and stores credentials in `~/.hermes/.env` + `~/.hermes/config.yaml`). That's a user task, not an agent task; surface the gap and deliver the digest in the chat response so nothing is lost.

If delivery succeeds, the digest leaves the session. If it fails, the error message from `hermes send` is the actual error — relay it directly, don't paraphrase.

## Pitfalls

### Don't conflate Shorts with full episodes

A news show may post 4–6 Shorts per day (one per story) in addition to the full ~20-minute episode. For a "news digest" the user wants the full episode. Filter on the RSS `link rel="alternate"` href: `watch?v=` = full, `/shorts/` = clip. Pick the most recent **full** episode within the freshness window, not the most recent **entry** of any kind.

### Don't ship a digest without the YouTube URL

The digest is unverifiable without the source link. Always include `https://www.youtube.com/watch?v={videoId}` in the header, derived from the RSS `<yt:videoId>`.

### Don't sharpen the host's hedges

News/analysis shows earn trust by flagging their own uncertainty. When the host says "this is correlation, not causation" or "I'm not claiming X," the summary carries that exact posture. Replacing it with a stronger claim changes the show.

### "Repeat:" prefix when the video isn't new

If `session_search` shows this video ID was already analyzed (likely because the cron job runs more than once per day, or a manual run happens to land on the same episode), the right move is to re-send the prior digest prefixed with `Repeat:` — not to re-analyze and re-write. The user wants the day's news, not a duplicate analysis.

### Don't use the browser tool for channel discovery

The channel page is heavy HTML, fails to render via the local browser in this environment, and the RSS feed gives the same data in 40KB. Always try the RSS feed first; only escalate to the browser if the RSS feed is missing or malformed.

## Related

- **`youtube-content`** — transcript fetching, output format templates, RSS feed recipe, Windows/venv pitfalls. Load this skill first; the digest pipeline runs on top of it.
- **`youtube-news-digest`** — the canonical TL;DR + Full Analysis format and analysis workflow. Load this skill at the analyze step (step 4 of the Pipeline). The format spec, style rules, and quality-check questions all live there. This orchestrator skill should not re-specify the format; it just delegates.
- **`hermes send` CLI** — the actual delivery mechanism when the `send_message` tool isn't available. Check `hermes send --list` to see whether platforms are configured.
