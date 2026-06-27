# Arxiv Parsing Workaround (Windows)

When Python is missing on Windows (as in Hermes default cron environment), use these POSIX-compatible methods to parse arXiv API output:

```bash
# Extract titles from XML response
curl -s 'https://export.arxiv.org/api/query?search_query=all:agent&max_results=5' | grep '<a:title>' | sed 's/\n//g' | cut -d '>' -f2 | cut -d '<' -f1
```

For more robust parsing:

```bash
# Parse full metadata (requires `sed` and no Python)
curl -s 'https://export.arxiv.org/api/query?q=au:clark&max_results=5' | \ 
  grep '<a:title>' -o | sed 's/\n//g' | cut -d '>' -f2 | cut -d '<' -f1 \ 
  > titles.txt

curl -s 'https://export.arxiv.org/api/query?q=au:clark&max_results=5' | \ 
  grep '<a:id>' -o | sed 's/\n//g' | cut -d '>' -f2 | cut -d '<' -f1 \ 
  > arxiv_ids.txt
```

> **Note**: The POSIX commands above are fully compatible with Windows Git Bash/Msys. Use `sed` from MSYS or install via Cygwin if needed. This approach avoids installation requirements for the cron job runtime.