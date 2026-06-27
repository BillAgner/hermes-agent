# arXiv API Parsing Fallbacks

When the normal XML parsing approach fails during research ticks, use these fallback patterns.

## Primary approach (from arxiv skill)

```bash
curl -s "https://export.arxiv.org/api/query?search_query=cat:cs.AI&max_results=5" | python3 -c "
import sys, xml.etree.ElementTree as ET
# ... XML parsing script
"
```

## When XML parsing fails

**Symptom:** `xml.etree.ElementTree.ParseError: no element found: line 1, column 0`

**Cause:** arXiv API sometimes returns empty responses or malformed XML

**Fallbacks (in order):**

1. **Test the API endpoint first:**
   ```bash
   curl -s "https://export.arxiv.org/api/query?search_query=cat:cs.AI&max_results=5" | head -10
   ```
   If empty or error HTML, skip arXiv for this tick.

2. **Use python from full path:**
   ```bash
   /c/Users/bobup/AppData/Local/Programs/Python/Python312/python -c "..."
   ```
   Windows PATH issues can cause python3 to fail.

3. **Simplify to basic extraction:**
   ```bash
   curl -s "https://export.arxiv.org/api/query?search_query=all:agent+OR+all:MCP&max_results=5" | grep -E "<title>|<id>|<published>" | head -20
   ```

4. **Switch to Semantic Scholar API:**
   ```bash
   curl -s "https://api.semanticscholar.org/graph/v1/paper/search?query=agent+framework&limit=3&fields=title,authors,year,externalIds" | python3 -m json.tool
   ```

## Time budget during fallbacks

- Don't spend more than 30 seconds debugging arXiv API issues
- If first fallback doesn't work, switch to GitHub API or another research lane
- Always prioritize completing the tick over perfect arXiv parsing

## GitHub API as reliable alternative

When both web_search and arXiv fail, GitHub API is consistently available:

```bash
# Recent agent frameworks
curl -s "https://api.github.com/search/repositories?q=agent+framework+created:>2026-06-25&sort=updated&order=desc"

# Recent MCP servers  
curl -s "https://api.github.com/search/repositories?q=MCP+server+created:>2026-06-25&sort=updated&order=desc"
```

Extract with grep/jq for structured data without complex parsing.