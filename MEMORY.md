# MEMORY.md

## API Quirks & Decisions

- GitHub MCP Registry (`github.com/mcp`) is public preview with no programmatic marketplace API. We use GitHub REST API against the `mcp` org + `topic:mcp-server` search as a polyfill.
- Unauthenticated GitHub Search API has strict rate limits (~10 req/min). `GITHUB_TOKEN` is optional but practically required.
- Hermes plugin discovery looks for `plugin.yaml` + `__init__.py` with a `TOOLS` list. No additional registration step needed if files are in `~/.hermes/plugins/<name>/`.
- `hermes mcp add` CLI syntax varies by transport:
  - stdio: `hermes mcp add NAME --command 'COMMAND_JSON'`
  - http: `hermes mcp add NAME --url URL`
- The `plugin.yaml` `provides_tools` key must list exact tool names from `__init__.py` for Hermes to expose them.

## Open Questions

- Should we add a `mcp_marketplace_remove` tool to uninstall servers? Hermes CLI already supports `hermes mcp remove NAME`.
- Should we support `mcp_marketplace_update` to bump cached entries and re-add with new versions?
