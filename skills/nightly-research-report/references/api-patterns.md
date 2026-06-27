# Proven API Patterns for Research Ticks

When web_search/web_extract tools are unavailable, these direct API patterns work reliably:

## GitHub API - Recent Repositories

```bash
# Search repos created in last 6 days with agent/MCP keywords
curl -s "https://api.github.com/search/repositories?q=agent+framework+created:>2026-06-20&sort=stars&order=desc" | python -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for repo in data.get('items', [])[:5]:
        name = repo.get('full_name', '')
        desc = repo.get('description', 'No description')
        stars = repo.get('stargazers_count', 0)
        url = repo.get('html_url', '')
        created = repo.get('created_at', '')[:10]
        language = repo.get('language', 'Unknown')
        print(f'{name} ({stars} stars)')
        print(f'Language: {language} | Created: {created}')
        print(f'Description: {desc}')
        print(f'URL: {url}')
        print('---')
except Exception as e:
    print(f'Error: {e}')
"
```

## Hacker News API - AI/Agent Stories

```bash
# Get top stories, filter for AI/agent/LLM keywords
curl -s "https://hacker-news.firebaseio.com/v0/topstories.json" | python -c "
import sys, json, urllib.request, time
story_ids = json.load(sys.stdin)[:15]
for i, story_id in enumerate(story_ids):
    try:
        url = f'https://hacker-news.firebaseio.com/v0/item/{story_id}.json'
        with urllib.request.urlopen(url) as response:
            story = json.loads(response.read())
            title = story.get('title', '')
            story_url = story.get('url', '')
            if any(term in title.lower() for term in ['agent', 'ai', 'llm', 'gpt', 'anthropic', 'openai', 'mcp']):
                print(f'{title}')
                print(f'URL: {story_url}')
                print(f'HN: https://news.ycombinator.com/item?id={story_id}')
                print('---')
        if i > 0 and i % 5 == 0:
            time.sleep(0.5)  # Rate limit
    except Exception as e:
        continue
"
```

## arXiv API - Agent Papers

```bash
# Use the arxiv skill's pattern but adapt for 'python' not 'python3'
curl -s "https://export.arxiv.org/api/query?search_query=ti:agent+OR+ti:agentic+OR+abs:autonomous+agent+OR+abs:multi-agent+system+OR+abs:tool+calling&sortBy=submittedDate&sortOrder=descending&max_results=5" | python -c "
import sys, xml.etree.ElementTree as ET
ns = {'a': 'http://www.w3.org/2005/Atom'}
root = ET.parse(sys.stdin).getroot()
for i, entry in enumerate(root.findall('a:entry', ns)):
    title = entry.find('a:title', ns).text.strip().replace('\n', ' ')
    arxiv_id = entry.find('a:id', ns).text.strip().split('/abs/')[-1]
    published = entry.find('a:published', ns).text[:10]
    authors = ', '.join(a.find('a:name', ns).text for a in entry.findall('a:author', ns)[:2])
    summary = entry.find('a:summary', ns).text.strip()[:250]
    cats = ', '.join(c.get('term') for c in entry.findall('a:category', ns))
    print(f'[{arxiv_id}] {title}')
    print(f'Authors: {authors}')
    print(f'Published: {published} | Categories: {cats}')  
    print(f'Abstract: {summary}...')
    print(f'URL: https://arxiv.org/abs/{arxiv_id}')
    print('---')
"
```

## Notes

- Use `python` not `python3` (Windows git-bash environment)
- Rate limit HN API calls with `time.sleep(0.5)` every 5 requests
- GitHub API has higher rate limits, no auth needed for search
- Filter results in Python rather than trying complex URL query params
- Always include error handling with try/except
- Keep JSON parsing simple - use dict.get() with defaults