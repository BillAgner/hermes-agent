# Agent Research Patterns

## Date-Filtered Recent Paper Discovery

When conducting time-sensitive research (e.g., nightly research pipelines), filter results by recent submission dates:

```bash
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
    # Only show papers from last 2-3 days
    if (today - pub_date).days > 2:
        continue
    authors = ', '.join(a.find('a:name', ns).text for a in entry.findall('a:author', ns))
    summary = entry.find('a:summary', ns).text.strip()[:200]
    cats = ', '.join(c.get('term') for c in entry.findall('a:category', ns))
    print(f'{i+1}. [{arxiv_id}] {title}')
    print(f'   Authors: {authors}')
    print(f'   Published: {published} | Categories: {cats}')
    print(f'   Abstract: {summary}...')
    print(f'   URL: https://arxiv.org/abs/{arxiv_id}')
    print()
"
```

## Agent-Specific Search Queries

### Multi-Agent and GUI Agent Research
```bash
# Multi-category agent research
search_query=cat:cs.AI+OR+cat:cs.MA+OR+cat:cs.CL+OR+cat:cs.LG+AND+agent

# GUI and web agent research
search_query=abs:GUI+agent+OR+abs:multimodal+web+agent+OR+abs:autonomous+agent

# Autonomous systems and hardware co-design
search_query=abs:autonomous+agent+AND+(abs:hardware+OR+abs:co-design)
```

### High-Value Agent Categories
- `cs.AI` - Core AI/agent methods
- `cs.MA` - Multi-agent systems  
- `cs.CL` - Language agents and NLP-driven agents
- `cs.LG` - Learning-based agent architectures
- `cs.RO` - Robotics and embodied agents
- `cs.AR` - Architecture-aware agent design

## Pitfalls

### Python Command Issues
- On Windows with MSYS/Git Bash, use `python` not `python3` in curl pipes
- The skill examples should work across platforms - test both commands when available

### Date Filtering Precision  
- arXiv timestamps are in UTC
- For "last 24 hours" research, use 2-day buffer to account for timezone differences
- Submission dates vs. publication dates can differ - use `submittedDate` for most recent work

### Rate Limiting
- arXiv API: ~1 request per 3 seconds safe limit
- When batch-processing multiple searches, add delays: `sleep 3` between calls
- Use `max_results` parameter to avoid oversized responses