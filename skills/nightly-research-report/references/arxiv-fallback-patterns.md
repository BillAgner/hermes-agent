---
name: arxiv-fallback-patterns
description: "ArXiv research patterns when web tools are unavailable or Python is missing. Tested workarounds from actual cron sessions."
---

# ArXiv Fallback Research Patterns

When `web_search` and `web_extract` are not configured, or when Python is missing for the arxiv skill's helper script, use these direct curl + grep patterns.

## Basic Paper Search (No Python)

```bash
# Multi-agent coordination papers
curl -s "https://export.arxiv.org/api/query?search_query=cat:cs.MA+OR+multi-agent+coordination&sortBy=submittedDate&sortOrder=descending&max_results=5" | grep -E "(title>|id>|published>|summary>)" | head -20

# Agent frameworks and MCP
curl -s "https://export.arxiv.org/api/query?search_query=all:agent+collaboration+OR+all:agent+orchestration+OR+all:multi-agent+workflow&sortBy=submittedDate&sortOrder=descending&max_results=3"

# Recent CS.AI papers
curl -s "https://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=5"
```

## Manual XML Parsing Pattern

When the Python helper script fails, extract key fields manually:

```bash
curl -s "arxiv-query-url" | grep -E "(title>|id>|published>|summary>)" | while read line; do
  if [[ "$line" == *"<title>"* ]]; then
    echo "Title: $(echo "$line" | sed 's/<[^>]*>//g' | sed 's/^[[:space:]]*//')"
  elif [[ "$line" == *"<id>"* ]]; then
    echo "ID: $(echo "$line" | sed 's/<[^>]*>//g' | grep -o '2[0-9][0-9][0-9]\.[0-9]*')"
  elif [[ "$line" == *"<published>"* ]]; then
    echo "Published: $(echo "$line" | sed 's/<[^>]*>//g' | cut -c1-10)"
  elif [[ "$line" == *"<summary>"* ]]; then
    echo "Abstract: $(echo "$line" | sed 's/<[^>]*>//g' | cut -c1-200)..."
  fi
done
```

## Effective Query Patterns (Tested)

These search patterns yielded relevant results in practice:

- `cat:cs.AI+OR+cat:cs.MA+multi-agent+coordination` - Multi-agent systems
- `all:mixture+of+agents+OR+all:model+routing` - Model ensemble techniques  
- `all:agent+collaboration+OR+all:agent+orchestration` - Agent workflows
- `cat:cs.CL+OR+cat:cs.AI+gui+agent` - GUI automation agents
- `all:context+engineering+OR+all:agent+memory` - Memory architectures

## Time-Recent Filtering

Always sort by submission date for overnight research:
- `&sortBy=submittedDate&sortOrder=descending`
- `&max_results=3` to 8 for time budget management
- Focus on papers from the last 24-48 hours

## Paper ID Extraction

Extract clean arXiv IDs for URLs:
```bash
echo "http://arxiv.org/abs/2606.27350v1" | sed 's/.*abs\///' | sed 's/v[0-9]*$//'
# Output: 2606.27350
```

## When to Use This Fallback

1. `web_search` returns "No web search provider configured"
2. `web_extract` returns "No web extract provider configured"  
3. `python3` command not found or returns Microsoft Store prompt
4. `arxiv` skill's helper script fails with Python errors

The fallback is arXiv-only but can still discover 2-3 high-quality findings per tick, especially for agent/ML research where arXiv coverage is strong.

## Integration with Tick Workflow

Replace the normal web search block with:

```bash
# Check if web tools are available
if ! web_search query="test" 2>&1 | grep -q "No.*provider configured"; then
    # Normal web research path
    web_search + web_extract
else
    # Fallback: arXiv-only research
    curl -s "https://export.arxiv.org/api/query?search_query=..." | grep -E "(title>|id>|published>|summary>)"
fi
```

This pattern was successfully used in Tick 4 of the 2026-06-26 session to discover valuable findings on self-evolving agent architectures and multi-agent coordination.