# Project Context
**Project:** hermes-github-mcp-marketplace
**Stack:** Python (Hermes Plugin)
**Security Tier:** Standard.

## Agent Directives
- For Python: Use `uv` and `uvx`. For Node: Use `npm` and `npx`.
- Rely on `TODO.md` as the source of truth for task state.
- Write operational discoveries, API quirks, and tech decisions to `MEMORY.md`.
- Default to `/superpowers` and sub-agent delegation (`delegate_task`) for heavy lifting.
- NEVER commit `.env` or hardcode secrets. Use `.env.example` as reference.
- Plugin must conform to Hermes plugin manifest (`plugin.yaml`) and expose `TOOLS` + handlers in `__init__.py`.
