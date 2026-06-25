@echo off
REM qmd-mcp.cmd - Launch QMD MCP server for Hermes.
REM Hermes config.yaml invokes this wrapper as the MCP command.
REM Ollama routing is enabled: embeddings + query expansion go through
REM ollama HTTP API (bge-m3, qwen3:8b). Skips the ~2GB bundled-GGUF download.
REM Rerank is a no-op for now (returns input order with synthetic scores);
REM qmd_deep_search falls back to BM25+vector fusion.
set QMD_USE_OLLAMA=1
set OLLAMA_HOST=http://127.0.0.1:11434
set QMD_OLLAMA_EMBED_MODEL=bge-m3:latest
set QMD_OLLAMA_EXPAND_MODEL=qwen3:8b
cd /d "C:\Data\Hermes_0.17.0\~\qmd"
"%USERPROFILE%\AppData\Roaming\npm\bun.cmd" run qmd mcp