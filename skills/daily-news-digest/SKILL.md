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

### Fallback when `youtube-news-digest` is not installed

`youtube-news-digest` is a soft dependency. If `skill_view` returns "skill not found" at the analyze step, **do not** stop — the format spec is short enough to inline. Apply these rules directly:

- **Header:** `📰 Daily News Digest — <Month DD, YYYY>` (today's date in the digest, *not* the upload date — note the upload date in the analysis so the reader knows the gap).
- **TL;DR:** exactly 5 bullets. Each bullet leads with a concrete fact (a number, a name, a date, a quote-fragment). No bullet may start with "The host discusses..." or "In this story..." — those are summary, not fact.
- **Throughline:** one short sentence below the 5 bullets that names the connective tissue across the 5 stories in the host's own framing where possible.
- **Full Analysis:** one section per TL;DR bullet, in the same order. Each section opens with the load-bearing fact, then adds quotes and detail. **Preserve the host's hedges verbatim** — if the host says "correlation, not causation" or "in their exact words," keep that posture. Closing synthesis section restates the host's wrap-up rather than inventing a new one.
- **Target length:** 800-1500 words for the analysis body. If the host packed 5 dense stories with quotes, going slightly over is fine; if you're at 2000+, trim the per-section filler.
- **Source line** is non-negotiable. Always include `https://www.youtube.com/watch?v={videoId}` derived from the RSS `<yt:videoId>`.

## Delivery

The `youtube-news-digest` format skill (loaded at the analyze step) accepts a `deliver_to` parameter. Pass the configured target through to it:

- From a cron job: read the job's `deliver` field and pass it as `deliver_to` to the format skill.
- From an interactive chat: pass whatever target the user specifies, or leave it unset if they just want the digest in the chat response.
- From any other caller: pass through whatever delivery target was configured.

The format skill handles the actual `hermes send` invocation and the platform-configured check. The orchestrator's job is just to forward the parameter.

### Cron auto-DELIVERY override

When this pipeline runs as a scheduled cron job with a `[IMPORTANT: ... DELIVERY: Your final response will be automatically delivered ... do NOT use send_message ...]` preamble, **skip the `hermes send` step entirely**. The runtime is configured to route your final assistant response to the user; calling `hermes send` would double-deliver. The orchestrator's job in that mode is:

1. Compose the digest body (TL;DR + Full Analysis + any job-specific prefix/suffix blocks like COMEX market pulse).
2. Write the assembled body to `/tmp/digest.txt` (or `C:/Users/<user>/AppData/Local/Temp/digest.txt` on Windows — same path under MSYS).
3. Return the digest as the final assistant message. The cron runtime handles the rest.

If the preamble says `SILENT`, return exactly `[SILENT]` and nothing else when there is nothing new to report (e.g., a "Repeat:" that the user already has). Do not combine `[SILENT]` with content.

### Manual delivery via `hermes send` CLI

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

### `youtube-news-digest` is a soft dependency

The skill's analyze step says "load `youtube-news-digest`." That skill is not always installed in every profile — treat it as a soft dependency. If `skill_view(name="youtube-news-digest")` returns a "not found" error, fall back to the inline format spec under "Default Output Format → Fallback when `youtube-news-digest` is not installed" above. Do not abort the digest.

### `youtube-transcript-api` can fail with `ParseError` even when the video has captions

When the helper script returns `{"error": "no element found: line 1, column 0"}` (or any `xml.etree.ElementTree.ParseError`), the YouTube transcript API is being blocked or the response is malformed — usually IP-binding, rate-limiting, or YouTube's anti-bot layer rejecting the request. The video's captions almost certainly exist. **Do not** conclude "transcripts are disabled" from this error. Fall back to `yt-dlp` (see the recipe in `youtube-content` under "yt-dlp fallback when the API is blocked"). `yt-dlp` reads the page itself and pulls the caption track URL from the embedded `captionTracks` array, which is a different code path.

### The channel RSS feed needs the channel ID, not the handle

The `https://www.youtube.com/feeds/videos.xml?channel_id=...` URL requires a 24-char `UC...` channel ID, not a `@handle`. If you only have the handle (`@SharedSapience`), resolve it with:

```bash
curl -s -A "Mozilla/5.0" "https://www.youtube.com/@Handle" -L | grep -oE '"externalId":"[A-Za-z0-9_-]+"' | head -1
```

The `externalId` field in the page's `og:video` / metadata block is the `UC...` ID you need. This avoids needing a web search tool (which may not be configured) and the slow browser.

### Cron DELIVERY overrides the manual `hermes send` step

When the job preamble says "DELIVERY: Your final response will be automatically delivered," do not call `hermes send` and do not write to `/tmp/digest.txt` as a hard requirement — the runtime routes the assistant's final response directly. The orchestrator's only job in that mode is to produce the digest content as the final message. See "Delivery → Cron auto-DELIVERY override" above.

## Related

- **`youtube-content`** — transcript fetching, output format templates, RSS feed recipe, Windows/venv pitfalls. Load this skill first; the digest pipeline runs on top of it.
- **`youtube-news-digest`** — the canonical TL;DR + Full Analysis format and analysis workflow. Load this skill at the analyze step (step 4 of the Pipeline). The format spec, style rules, and quality-check questions all live there. This orchestrator skill should not re-specify the format; it just delegates.
- **`hermes send` CLI** — the actual delivery mechanism when the `send_message` tool isn't available. Check `hermes send --list` to see whether platforms are configured.
