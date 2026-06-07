# MEMORY.md

## API Decisions

- Primary source is `registry.modelcontextprotocol.io/v0/servers` — the official MCP Registry REST API backed by Anthropic, GitHub, PulseMCP, Microsoft. This is the canonical metadata repository, not the GitHub `mcp` org (which is a downstream consumer).
- GitHub `mcp` org scraping was dropped because it lacks structured install metadata. The registry's `server.json` format includes `packages[]` with `registryType`, `runtimeHint`, `runtimeArguments`, `environmentVariables`, and `transport` — everything needed for auto-install.
- The registry supports cursor pagination (`metadata.nextCursor`). We fetch up to 2000 servers (20 pages x 100) on refresh.
- No auth required for reads. Registry is public.
- Deduplication strategy: keyed by `server.name` (namespace). Prefer `isLatest=true` when duplicates exist.

## Open Questions

- Should we cache package manifests (`server.json` per server) individually for faster info lookups?
- Should we support `mcp_marketplace_remove` to uninstall servers (Hermes CLI already has `hermes mcp remove`)?
- Should we add a `mcp_marketplace_update` to re-check registry versions and prompt for upgrades?
