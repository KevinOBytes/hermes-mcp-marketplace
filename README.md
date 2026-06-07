# Hermes GitHub MCP Marketplace

Browse, search, and install MCP servers from GitHub's MCP Registry and community sources directly into Hermes. Skills-like interactivity with search, filter, and one-click add.

## What It Does

GitHub's MCP Registry (`github.com/mcp`) is currently a **public preview with no programmatic API** for discovery. This plugin bridges the gap by:

1. Scraping/parsing the official `mcp` organization and community repos tagged `mcp-server`
2. Caching metadata locally (`cache.json`)
3. Providing interactive tools to search, inspect, and install servers into Hermes's native MCP config

## Tools

| Tool | Purpose |
|------|---------|
| `mcp_marketplace_refresh` | Refresh the local cache from GitHub sources |
| `mcp_marketplace_search` | Search servers by keyword, category, or transport |
| `mcp_marketplace_list` | List available servers (cached or live) |
| `mcp_marketplace_info` | Get detailed info + README analysis for a server |
| `mcp_marketplace_add` | Auto-install a server into Hermes (stdio or http) |

## Installation (into Hermes)

```bash
# Clone into Hermes plugins directory
cd ~/.hermes/plugins
git clone https://github.com/KevinOBytes/hermes-github-mcp-marketplace.git

# Restart Hermes or reload MCP
hermes mcp reload
```

## Requirements

- `GITHUB_TOKEN` env var (PAT) — strongly recommended for rate limits; works without but may hit unauth limits fast.
- `httpx` — optional but recommended; falls back to urllib if absent.

## How It Works

- **Discovery:** Queries GitHub API for repos in `github.com/mcp` org + searches `topic:mcp-server` across all of GitHub.
- **Auto-detection:** Parses READMEs for `docker`, `npx`, `uvx`, `pip`, `go install`, `cargo install` hints to determine how to run the server.
- **Install:** Uses `hermes mcp add` to register the server with the detected command and environment placeholders.

## Limitations

- GitHub MCP Registry has **no official search API** yet — we use repo/topic heuristics.
- README parsing for install commands is best-effort regex; exotic build steps need manual config.
- HTTP transport servers require an explicit `url` parameter (auto-filled only for known endpoints like GitHub Copilot MCP).

## License

MIT
