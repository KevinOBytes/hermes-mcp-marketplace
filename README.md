# Hermes MCP Marketplace

Browse, search, and install MCP servers from the **official MCP Registry** (`registry.modelcontextprotocol.io`) directly into Hermes.

## Why This Exists

GitHub's `mcp` organization is a downstream curated view. The **canonical source** is `registry.modelcontextprotocol.io` — backed by Anthropic, GitHub, PulseMCP, and Microsoft. It exposes a real REST API with structured `server.json` metadata including install commands, transports, and environment variables.

This plugin uses the official registry as its primary source. No scraping, no GitHub API rate limits.

## Tools

| Tool | Purpose |
|------|---------|
| `mcp_marketplace_refresh` | Fetch and cache all servers from the official registry |
| `mcp_marketplace_search` | Search by keyword with optional transport/runtime filters |
| `mcp_marketplace_list` | Browse cached or live listings |
| `mcp_marketplace_info` | Deep-dive a server: install commands, env vars, transports |
| `mcp_marketplace_add` | One-click install into Hermes (auto-detects stdio vs HTTP) |

## Installation (into Hermes)

```bash
cd ~/.hermes/plugins && git clone https://github.com/KevinOBytes/hermes-mcp-marketplace.git
hermes mcp reload
```

## Requirements

- `httpx` — optional but recommended (`pip install httpx`). Falls back gracefully if absent.
- No API key needed — the official registry is public read.

## How It Works

1. **Discovery:** Calls `registry.modelcontextprotocol.io/v0/servers` with cursor pagination.
2. **Normalization:** Flattens `server.json` + `packages[]` into uniform internal records with `install_commands`, `env_vars`, `transports`.
3. **Auto-install:** For **stdio** servers, builds the command array from `registryType` + `runtimeHint` (npx, uvx, docker, pip, cargo, go). For **HTTP** servers, configures the remote URL and headers.
4. **Integration:** Uses `hermes mcp add` to register the server natively.

## Example Usage

```
# Refresh cache
mcp_marketplace_refresh

# Search for database servers
mcp_marketplace_search query=postgres

# Get install details
mcp_marketplace_info id=com.pulsemcp/remote-filesystem

# Install it
mcp_marketplace_add id=com.pulsemcp/remote-filesystem env={"GCS_BUCKET": "my-bucket"}
```

## Limitations

- The official registry is in **public preview** — breaking changes possible.
- Private MCP servers are not supported (registry does not list them).
- Some exotic build steps may need manual config post-install.

## License

MIT
