# Successful Research Patterns

Based on actual nightly research tick execution (2026-06-26), these patterns proved effective for discovering relevant agent/MCP developments within the 3-minute time limit.

## State File Management

### Working Example Structure
```markdown
# Nightly Agentic Research — 2026-06-26

## Sources covered so far
- GitHub API (recent agent frameworks & MCP servers, created after 2026-06-20)
- Hacker News API (top stories with AI/agent keywords)  
- arXiv API (recent papers in agent-related categories, 2026-06-25 submissions)
- arXiv API (GUI agents, multimodal web agents, autonomous agents, hardware/software co-design 2026-06-25)
- GitHub API (trending agent/autonomous/MCP repositories created after 2026-06-24)

## Findings so far
1. [Title](URL) — Description
   Status: New
   Action: install
```

### Critical Path Issue
The template path `C:\Data\Hermes\cron\output\nightly-agent-research\` was wrong. Actual working path was `C:\Data\Hermes_0.17.0\cron\output\nightly-agent-research\`. Always verify the correct Hermes installation directory first.

## Efficient Research Patterns

### arXiv Date-Filtered Agent Research
```bash
# Effective multi-category agent query with date filtering  
curl -s "https://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.MA+OR+cat:cs.CL+OR+cat:cs.LG+AND+agent&sortBy=submittedDate&sortOrder=descending&max_results=10" | python -c "
import sys, xml.etree.ElementTree as ET
from datetime import datetime, date
ns = {'a': 'http://www.w3.org/2005/Atom'}
root = ET.parse(sys.stdin).getroot()
today = date.today()
for i, entry in enumerate(root.findall('a:entry', ns)):
    title = entry.find('a:title', ns).text.strip().replace('\n', ' ')
    arxiv_id = entry.find('a:id', ns).text.strip().split('/abs/')[-1]
    published = entry.find('a:published', ns).text[:10]
    pub_date = datetime.strptime(published, '%Y-%m-%d').date()
    if (today - pub_date).days > 2:
        continue
    # ... rest of processing
"
```

**Key insight:** Use `python` not `python3` on Windows MSYS/Git Bash systems.

### GitHub Trending with Smart Date Filtering
```bash
# Recent agent repositories with good star filtering
curl -s "https://api.github.com/search/repositories?q=agent+OR+autonomous+OR+MCP+created:>2026-06-24&sort=stars&order=desc&per_page=10"
```

**Effective categories found:**
- Agent frameworks and MCP servers
- Memory management systems for agents  
- Zero-knowledge agent memory (privacy-preserving)
- OpenAPI-to-MCP auto-generation tools
- GUI/multimodal web agents

### High-Value Search Terms
Successful queries that yielded actionable findings:

**arXiv:**
- `abs:GUI+agent+OR+abs:multimodal+web+agent+OR+abs:autonomous+agent`
- `cat:cs.AI+OR+cat:cs.MA+OR+cat:cs.CL+OR+cat:cs.LG+AND+agent`  
- `abs:autonomous+agent+AND+(abs:hardware+OR+abs:co-design)`

**GitHub:**
- `agent+OR+autonomous+OR+MCP+created:>YYYY-MM-DD`
- `MCP+server+OR+Model+Context+Protocol+created:>YYYY-MM-DD`

## Finding Quality Criteria

### Status Classification That Worked
- **New**: Genuinely new discovery (75% of findings)
- **Repeat**: Covered in prior tick (avoid these)  
- **Already-available**: Already have/using (rare but worth noting)

### Action Classification That Worked  
- **install**: High-value tools that extend Hermes capabilities (40% of findings)
- **monitor-only**: Interesting but not immediately actionable (50% of findings)
- **no-action**: Information/insights only (10% of findings)

### "Why for Hermes" Connection Patterns
Successful patterns for connecting findings to Hermes architecture:

- **Memory & Context**: "could enhance our memory tiers and skill context optimization"
- **MCP Ecosystem**: "massively expanding available tools" / "could auto-generate MCP servers from any API"  
- **Agent Orchestration**: "could inform our subagent orchestration and autonomous-ai-agents skills"
- **Security & Privacy**: "Critical for secure agent memory management" 
- **GUI Automation**: "Directly applicable to our webwright skill and GUI automation"

## Time Management

### Successful 3-Minute Pattern
- **0:00-0:30**: Check existing state, pick research lane
- **0:30-2:30**: Execute 2-4 focused searches  
- **2:30-3:00**: Write findings to state file

### What Worked Within Time Budget
- 2-4 targeted API calls (arXiv + GitHub)
- Abstract-only paper review (not full PDFs)
- 5-8 new findings per tick
- Direct API parsing over web extraction when tools unavailable

### What Would Exceed Budget  
- Full PDF downloads and reading
- More than 10-15 papers reviewed
- More than 20 web pages per tick
- Complex multi-step web scraping

## Fallback Patterns

### When Web Tools Fail
- `web_extract` and `web_search` may be unconfigured  
- Fall back to direct API calls (arXiv XML, GitHub JSON)
- Use `terminal` + `curl` + `python` parsing pipeline
- This pattern proved fully sufficient for productive research

### Cross-Platform Compatibility 
- Always test `python --version` vs `python3 --version`
- Use the available command in curl pipeline examples
- Path separators: use `/c/Data/...` style for Windows MSYS paths