---
name: youtube-content
description: "YouTube transcripts to summaries, threads, blogs."
platforms: [linux, macos, windows]
---

# YouTube Content Tool

## When to use

Use when the user shares a YouTube URL or video link, asks to summarize a video, requests a transcript, or wants to extract and reformat content from any YouTube video. Transforms transcripts into structured content (chapters, summaries, threads, blog posts).

Extract transcripts from YouTube videos and convert them into useful formats.

## Setup

Use `uv` so the dependency is installed into the same Hermes-managed environment
that runs the helper script:

```bash
uv pip install youtube-transcript-api
```

## Helper Script

`SKILL_DIR` is the directory containing this SKILL.md file. The script accepts any standard YouTube URL format, short links (youtu.be), shorts, embeds, live links, or a raw 11-character video ID.

```bash
# JSON output with metadata
uv run python3 SKILL_DIR/scripts/fetch_transcript.py "https://youtube.com/watch?v=VIDEO_ID"

# Plain text (good for piping into further processing)
uv run python3 SKILL_DIR/scripts/fetch_transcript.py "URL" --text-only

# With timestamps
uv run python3 SKILL_DIR/scripts/fetch_transcript.py "URL" --timestamps

# Specific language with fallback chain
uv run python3 SKILL_DIR/scripts/fetch_transcript.py "URL" --language tr,en
```

## Output Formats

After fetching the transcript, format it based on what the user asks for:

- **Chapters**: Group by topic shifts, output timestamped chapter list
- **Summary**: Concise 5-10 sentence overview of the entire video
- **Chapter summaries**: Chapters with a short paragraph summary for each
- **Thread**: Twitter/X thread format — numbered posts, each under 280 chars
- **Blog post**: Full article with title, sections, and key takeaways
- **Quotes**: Notable quotes with timestamps

### Example — Chapters Output

```
00:00 Introduction — host opens with the problem statement
03:45 Background — prior work and why existing solutions fall short
12:20 Core method — walkthrough of the proposed approach
24:10 Results — benchmark comparisons and key takeaways
31:55 Q&A — audience questions on scalability and next steps
```

## Workflow

1. **Fetch** the transcript using the helper script with `--text-only --timestamps` via `uv run python3`.
2. **Validate**: confirm the output is non-empty and in the expected language. If empty, retry without `--language` to get any available transcript. If still empty, tell the user the video likely has transcripts disabled.
3. **Chunk if needed**: if the transcript exceeds ~50K characters, split into overlapping chunks (~40K with 2K overlap) and summarize each chunk before merging.
4. **Transform** into the requested output format. If the user did not specify a format, default to a summary.
5. **Verify**: re-read the transformed output to check for coherence, correct timestamps, and completeness before presenting.

## Error Handling

- **Transcript disabled**: tell the user; suggest they check if subtitles are available on the video page.
- **Private/unavailable video**: relay the error and ask the user to verify the URL.
- **No matching language**: retry without `--language` to fetch any available transcript, then note the actual language to the user.
- **Dependency missing**: run `uv pip install youtube-transcript-api` and retry.
- **`ParseError: no element found: line 1, column 0`** (or any `xml.etree.ElementTree.ParseError`): the API is being blocked — IP binding, rate limit, or YouTube anti-bot. **Do not** conclude the video has no captions. Fall through to the `yt-dlp` recipe below; the video almost certainly has captions, the API just can't reach them.

## yt-dlp fallback when the API is blocked

`youtube-transcript-api` makes a direct HTTPS call to YouTube's `timedtext` endpoint and is frequently blocked. `yt-dlp` takes a different path — it renders the watch page and pulls the `captionTracks` URL out of the embedded JSON, which works in more environments and from more IP ranges.

```bash
# Skip video download, fetch both auto-generated and manual subs in best available format
yt-dlp --skip-download \
  --write-auto-subs --write-subs \
  --sub-format "vtt/srv3/srv2/srv1/best" \
  --sub-langs "en,en-US,en-orig" \
  -o "/tmp/yt_%(id)s.%(ext)s" \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

`yt-dlp` writes one VTT (or SRV3) per language under the output template. To read them:

```bash
uv pip install webvtt-py
python -c "
import webvtt
caps = webvtt.read('/tmp/yt_VIDEO_ID.en-US.vtt')   # manual captions if present
for c in caps:
    print(f'[{c.start} -> {c.end}] {c.text}')
"
```

Picking the right track when both are present: `en-US.vtt` (or any `xx-XX.vtt`) is the human-uploaded track — usually shorter, tighter, and cleaner. `en.vtt` and `en-orig.vtt` are auto-generated ASR — larger files, more word-level drift, but always present. Prefer the human track when both exist; fall back to ASR when it doesn't.

The `yt-dlp` recipe is also useful when `uv run` itself is broken on Windows (the venv `uv` creates is in a path with a space, which trips some Python finders). `yt-dlp` from the system Python (`pip install yt-dlp`) sidesteps the whole venv problem.

## Channel discovery from a handle (no API key, no browser)

If you have a `@handle` and need the `UC...` channel ID for the RSS feed:

```bash
curl -s -A "Mozilla/5.0" "https://www.youtube.com/@Handle" -L \
  | grep -oE '"externalId":"[A-Za-z0-9_-]+"' | head -1
```

The `externalId` value (e.g. `"UCU45D-fmlarTp7R_bdYY24g"`) is the channel ID. Then:

```bash
curl -s -A "Mozilla/5.0" "https://www.youtube.com/feeds/videos.xml?channel_id=UCU45D-fmlarTp7R_bdYY24g"
```

This is the same recipe the `daily-news-digest` orchestrator uses — it works in a fresh shell, needs no auth, and the response is 40-60KB of clean XML.
