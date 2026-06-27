# Nightly Research Pipeline Patterns

## Automated Research Tick Workflow

### Time-Bounded arXiv Research (3-minute ticks)

**Core Loop:**
1. Check date: `date +%Y-%m-%d` 
2. Read existing state file to avoid duplicate sources
3. Target 2-4 searches max per tick
4. Focus on abstracts, not full papers
5. Write findings immediately, don't accumulate

### Proven Search Strategies for Agent Research

**Recent Agent Papers (24-48h):**
```bash
curl -s "https://export.arxiv.org/api/query?search_query=all:agent+memory+OR+all:context+engineering+OR+all:prompt+caching&sortBy=submittedDate&sortOrder=descending&max_results=8"
```

**GUI and Multimodal Agents:**
```bash
curl -s "https://export.arxiv.org/api/query?search_query=ti:GUI+agent+OR+ti:multimodal+web+agent+OR+ti:autonomous+agent&sortBy=submittedDate&sortOrder=descending&max_results=5"
```

**Multi-Agent Coordination:**
```bash
curl -s "https://export.arxiv.org/api/query?search_query=ti:multi-agent+OR+ti:model+routing+OR+ti:agent+orchestration&sortBy=submittedDate&sortOrder=descending&max_results=5"
```

### Platform-Specific Workarounds

**Windows MSYS Python Issues:**
- Always `which python3` before attempting XML parsing
- Fall back to `grep -E "(title>|id>|published>)" | sed 's/<[^>]*>//g'` when python3 fails
- Use raw curl + grep rather than blocking on missing python

### Findings Template Structure

**For each finding:**
```markdown
N. [Title](URL) — brief description — *Why for Hermes: specific architectural relevance*
   Status: New|Repeat|Already-available  
   Action: install|configure|monitor-only|no-action
```

**Categories to track:**
- Agent frameworks & orchestration
- MCP servers & protocol implementations  
- Memory management & context compression
- Multi-agent coordination patterns
- RL improvements & training techniques
- Security & guardrail systems

### Rate Limiting & Source Diversity

- arXiv: ~1 req/3sec, use sparingly
- GitHub API: Higher rate limit, good for trending repos
- Target 2-3 different source types per tick
- Never hit same API endpoint twice in one tick
- Balance between depth (fewer sources, more analysis) and breadth (more sources, surface findings)

## Context Engineering Research Focus Areas

**High-Value Topics for Hermes:**
1. Context window optimization & compression
2. Prompt caching & memory hierarchies  
3. Agent memory architectures & persistence
4. Multi-model routing & orchestration
5. Tool use & MCP protocol extensions
6. Self-improving agent loops
7. GUI automation & web agent patterns

**Search Terms by Priority:**
- Primary: `agent memory`, `context compression`, `MCP server`, `multi-agent`, `GUI agent`
- Secondary: `prompt caching`, `model routing`, `context window`, `tool use`
- Emerging: `agent orchestration`, `autonomous systems`, `agentic AI`