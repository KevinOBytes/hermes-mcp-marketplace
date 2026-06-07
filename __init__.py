"""
hermes-mcp-marketplace
======================

Browse, search, and install MCP servers from the official MCP Registry
(registry.modelcontextprotocol.io) directly into Hermes.

Tools:
  - mcp_marketplace_refresh    Refresh local cache from registry API
  - mcp_marketplace_search     Search servers by keyword, transport, runtime
  - mcp_marketplace_list       List cached/available servers
  - mcp_marketplace_info       Get detailed info + install config for a server
  - mcp_marketplace_add        Install/add an MCP server into Hermes

No auth required — the official registry is public read.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
API_VERSION = "v0"
CACHE_FILE = Path(__file__).parent / "cache.json"


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {"servers": [], "last_updated": None}


def _save_cache(data: dict) -> None:
    CACHE_FILE.write_text(json.dumps(data, indent=2, default=str))


def _fetch_json(url: str, params: Optional[dict] = None) -> dict:
    try:
        import httpx
    except ImportError:
        return {"error": "httpx is not installed. Run: pip install httpx"}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def _list_servers(limit: int = 100, cursor: Optional[str] = None, search: Optional[str] = None) -> dict:
    params: Dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    if search:
        params["search"] = search
    return _fetch_json(f"{REGISTRY_BASE}/{API_VERSION}/servers", params=params)


def _normalize_server(raw: dict) -> dict:
    """Flatten registry response into a consistent internal record."""
    server = raw.get("server", {})
    meta = raw.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
    name = server.get("name", "")
    display_name = name.split("/")[-1] if "/" in name else name

    # Extract transports from remotes + packages
    transports = set()
    remotes = server.get("remotes", [])
    packages = server.get("packages", [])
    for r in remotes:
        if isinstance(r, dict) and "type" in r:
            transports.add(r["type"])
    for p in packages:
        if isinstance(p, dict):
            t = p.get("transport", {}).get("type")
            if t:
                transports.add(t)
            if p.get("runtimeHint"):
                transports.add(p["runtimeHint"])

    # Extract install commands from packages
    install_commands: List[dict] = []
    for p in packages:
        if not isinstance(p, dict):
            continue
        cmd = _package_to_command(p)
        if cmd:
            install_commands.append(cmd)

    # Extract env vars from packages + remotes headers
    env_vars: List[dict] = []
    seen = set()
    for p in packages:
        for e in p.get("environmentVariables", []):
            key = e.get("name")
            if key and key not in seen:
                seen.add(key)
                env_vars.append({
                    "name": key,
                    "description": e.get("description", ""),
                    "required": e.get("isRequired", False),
                    "secret": e.get("isSecret", False),
                    "default": e.get("default"),
                })
    for r in remotes:
        for h in r.get("headers", []):
            key = h.get("name")
            if key and key not in seen:
                seen.add(key)
                env_vars.append({
                    "name": key,
                    "description": h.get("description", ""),
                    "required": h.get("isRequired", False),
                    "secret": h.get("isSecret", False),
                })

    return {
        "id": name,
        "name": display_name,
        "namespace": name,
        "description": server.get("description", ""),
        "title": server.get("title", ""),
        "version": server.get("version", ""),
        "url": server.get("websiteUrl", ""),
        "repository": server.get("repository", {}),
        "transports": sorted(transports),
        "install_commands": install_commands,
        "env_vars": env_vars,
        "packages": packages,
        "remotes": remotes,
        "status": meta.get("status", "unknown"),
        "is_latest": meta.get("isLatest", False),
        "updated_at": meta.get("updatedAt"),
    }


def _package_to_command(pkg: dict) -> Optional[dict]:
    """Convert a registry package into a Hermes stdio command array."""
    registry_type = pkg.get("registryType", "").lower()
    identifier = pkg.get("identifier", "")
    runtime_hint = pkg.get("runtimeHint", "")
    runtime_args = pkg.get("runtimeArguments", [])
    env = {e["name"]: "" for e in pkg.get("environmentVariables", [])}

    command: List[str] = []

    if runtime_hint == "npx":
        command = ["npx", "-y", identifier]
    elif runtime_hint == "uvx":
        command = ["uvx", identifier]
    elif runtime_hint == "pip":
        command = ["python3", "-m", identifier]
    elif registry_type == "npm":
        command = ["npx", "-y", identifier]
    elif registry_type == "oci":
        command = ["docker", "run", "-i", "--rm", identifier]
    elif registry_type == "pypi":
        command = ["uvx", identifier]
    elif registry_type == "cargo":
        command = ["cargo", "run", "--bin", identifier]
    elif registry_type == "go":
        command = ["go", "run", identifier]
    else:
        return None

    # Append runtimeArguments
    for arg in runtime_args:
        v = arg.get("value", "")
        t = arg.get("type", "positional")
        if t == "positional" and v:
            command.append(v)

    return {"command": command, "env": env, "registry_type": registry_type, "identifier": identifier}


# ---------------------------------------------------------------------------
# Hermes CLI helper
# ---------------------------------------------------------------------------

def _hermes_mcp_add_stdio(name: str, command: List[str], env: Optional[dict] = None) -> dict:
    try:
        cmd = ["hermes", "mcp", "add", name, "--command", json.dumps(command)]
        if env:
            for k, v in env.items():
                cmd.extend(["--env", f"{k}={v}"])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    except Exception as e:
        return {"error": str(e)}


def _hermes_mcp_add_http(name: str, url: str, headers: Optional[dict] = None, env: Optional[dict] = None) -> dict:
    try:
        cmd = ["hermes", "mcp", "add", name, "--url", url]
        if headers:
            for k, v in headers.items():
                cmd.extend(["--header", f"{k}={v}"])
        if env:
            for k, v in env.items():
                cmd.extend(["--env", f"{k}={v}"])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_mcp_marketplace_refresh(args: dict, **kw) -> str:
    """Refresh the local cache from the official MCP Registry."""
    all_servers: List[dict] = []
    seen = set()
    cursor: Optional[str] = None
    pages = 0
    max_pages = 20  # Safety cap (~2000 servers)

    while pages < max_pages:
        pages += 1
        data = _list_servers(limit=100, cursor=cursor)
        if "error" in data:
            break
        servers = data.get("servers", [])
        if not servers:
            break
        for s in servers:
            norm = _normalize_server(s)
            sid = norm["id"]
            # Deduplicate by id, prefer latest version
            if sid in seen:
                existing = next((x for x in all_servers if x["id"] == sid), None)
                if existing and norm.get("is_latest"):
                    all_servers[all_servers.index(existing)] = norm
                continue
            seen.add(sid)
            all_servers.append(norm)
        cursor = data.get("metadata", {}).get("nextCursor")
        if not cursor:
            break

    cache = {
        "servers": all_servers,
        "last_updated": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "total_fetched": len(all_servers),
        "pages": pages,
    }
    _save_cache(cache)

    return json.dumps({
        "total_servers": len(all_servers),
        "pages_fetched": pages,
        "last_updated": cache["last_updated"],
    }, indent=2)


def handle_mcp_marketplace_search(args: dict, **kw) -> str:
    """Search the MCP Registry for servers by keyword, transport, or runtime."""
    query = args.get("query", "").strip()
    transport = args.get("transport", "").strip().lower()
    runtime = args.get("runtime", "").strip().lower()
    limit = args.get("limit", 20)

    # If we have a query, hit the live API for freshness
    if query:
        data = _list_servers(limit=min(limit, 100), search=query)
        if "error" not in data:
            servers = [_normalize_server(s) for s in data.get("servers", [])]
            if transport:
                servers = [s for s in servers if transport in [t.lower() for t in s.get("transports", [])]]
            if runtime:
                servers = [s for s in servers if any(runtime in (c.get("registry_type", "") + c.get("runtime_hint", "")).lower() for c in s.get("install_commands", []))]
            return json.dumps(servers, indent=2, default=str)

    # Fallback to cache
    cache = _load_cache()
    servers = cache.get("servers", [])
    if not servers:
        handle_mcp_marketplace_refresh({})
        cache = _load_cache()
        servers = cache.get("servers", [])

    q_lower = query.lower()
    results = []
    for s in servers:
        score = 0
        text = f"{s.get('name','')} {s.get('description','')} {s.get('title','')}"
        if q_lower and q_lower in text.lower():
            score += 10
            if q_lower in s.get("name", "").lower():
                score += 5
        if transport and transport in [t.lower() for t in s.get("transports", [])]:
            score += 8
        if runtime and any(runtime in (c.get("registry_type", "") + c.get("runtime_hint", "")).lower() for c in s.get("install_commands", [])):
            score += 5
        if not query and not transport and not runtime:
            score = 1  # include everything when no filters
        if score > 0:
            results.append({**s, "_score": score})

    results.sort(key=lambda x: x["_score"], reverse=True)
    results = results[:limit]
    for r in results:
        r.pop("_score", None)
    return json.dumps(results, indent=2, default=str)


def handle_mcp_marketplace_list(args: dict, **kw) -> str:
    """List available MCP servers from cache or live registry."""
    source = args.get("source", "cached")  # cached | live
    limit = args.get("limit", 30)
    transport = args.get("transport", "").strip().lower()

    if source == "live":
        data = _list_servers(limit=min(limit, 100))
        if "error" not in data:
            servers = [_normalize_server(s) for s in data.get("servers", [])]
            if transport:
                servers = [s for s in servers if transport in [t.lower() for t in s.get("transports", [])]]
            return json.dumps(servers, indent=2, default=str)

    cache = _load_cache()
    servers = cache.get("servers", [])
    if not servers:
        handle_mcp_marketplace_refresh({})
        cache = _load_cache()
        servers = cache.get("servers", [])

    if transport:
        servers = [s for s in servers if transport in [t.lower() for t in s.get("transports", [])]]

    servers = sorted(servers, key=lambda x: x.get("updated_at", "") or "", reverse=True)[:limit]
    return json.dumps(servers, indent=2, default=str)


def handle_mcp_marketplace_info(args: dict, **kw) -> str:
    """Get detailed info about a specific MCP server by namespace ID."""
    server_id = args.get("id", "").strip()
    if not server_id:
        return json.dumps({"error": "Provide 'id' in namespace format (e.g., 'com.pulsemcp/remote-filesystem')."})

    # Try live lookup via search first
    data = _list_servers(limit=10, search=server_id.split("/")[-1])
    if "error" not in data:
        for s in data.get("servers", []):
            if s.get("server", {}).get("name") == server_id:
                return json.dumps(_normalize_server(s), indent=2, default=str)

    # Fallback to cache exact match
    cache = _load_cache()
    for s in cache.get("servers", []):
        if s.get("id") == server_id:
            return json.dumps(s, indent=2, default=str)

    return json.dumps({"error": f"Server '{server_id}' not found in registry or cache."})


def handle_mcp_marketplace_add(args: dict, **kw) -> str:
    """Install/add an MCP server into Hermes from the marketplace."""
    server_id = args.get("id", "").strip()
    name = args.get("name", "").strip()  # Hermes MCP server name
    transport = args.get("transport", "").strip().lower()  # stdio | http | sse | streamable-http
    env = args.get("env", {})  # Optional override env vars

    if not server_id:
        return json.dumps({"error": "Provide 'id' in namespace format (e.g., 'com.pulsemcp/remote-filesystem')."})

    if not name:
        name = server_id.replace("/", "_").replace(".", "_")

    # Resolve server info
    info_json = handle_mcp_marketplace_info({"id": server_id}, **kw)
    info = json.loads(info_json)
    if "error" in info:
        return json.dumps({"error": f"Could not resolve server: {info['error']}"})

    transports = [t.lower() for t in info.get("transports", [])]
    remotes = info.get("remotes", [])
    packages = info.get("packages", [])
    install_commands = info.get("install_commands", [])

    # Determine target transport
    if not transport:
        if "stdio" in transports or "sse" in transports:
            transport = "stdio"
        elif "streamable-http" in transports or "http" in transports:
            transport = "http"
        elif remotes:
            transport = remotes[0].get("type", "http")
        elif install_commands:
            transport = "stdio"
        else:
            transport = "stdio"

    # HTTP transport: use remotes
    if transport in ("http", "sse", "streamable-http"):
        remote = None
        for r in remotes:
            if r.get("type", "").lower() == transport or (transport == "http" and r.get("type") == "streamable-http"):
                remote = r
                break
        if not remote and remotes:
            remote = remotes[0]

        if not remote:
            return json.dumps({"error": f"No remote endpoint found for transport '{transport}'.", "server": info})

        url = remote.get("url", "")
        headers = {}
        for h in remote.get("headers", []):
            headers[h["name"]] = h.get("value", "")
        # Merge env overrides
        merged_env = {e["name"]: env.get(e["name"], "") for e in info.get("env_vars", [])}
        for k, v in env.items():
            merged_env[k] = v

        result = _hermes_mcp_add_http(name, url, headers, merged_env)
        return json.dumps({
            "success": result.get("returncode", 1) == 0,
            "name": name,
            "transport": transport,
            "url": url,
            "headers": headers,
            "env": merged_env,
            "cli_stdout": result.get("stdout"),
            "cli_stderr": result.get("stderr"),
            "returncode": result.get("returncode"),
        }, indent=2)

    # STDIO transport: use install commands from packages
    if not install_commands:
        return json.dumps({
            "error": f"No installable stdio package found for '{server_id}'.",
            "transports": transports,
            "packages": packages,
        })

    cmd_info = install_commands[0]
    command = cmd_info.get("command", [])
    merged_env = {k: env.get(k, v) for k, v in cmd_info.get("env", {}).items()}
    for k, v in env.items():
        merged_env[k] = v

    result = _hermes_mcp_add_stdio(name, command, merged_env)
    return json.dumps({
        "success": result.get("returncode", 1) == 0,
        "name": name,
        "transport": "stdio",
        "command": command,
        "env": merged_env,
        "cli_stdout": result.get("stdout"),
        "cli_stderr": result.get("stderr"),
        "returncode": result.get("returncode"),
    }, indent=2)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "mcp_marketplace_refresh",
        "schema": {
            "name": "mcp_marketplace_refresh",
            "description": "Refresh the local cache of MCP servers from the official MCP Registry API (registry.modelcontextprotocol.io). Fetches all pages.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        "handler": handle_mcp_marketplace_refresh,
        "description": "Refresh MCP server cache from official registry",
        "emoji": "🔄",
    },
    {
        "name": "mcp_marketplace_search",
        "schema": {
            "name": "mcp_marketplace_search",
            "description": (
                "Search the official MCP Registry for servers by keyword. "
                "Can also filter by transport type (stdio, http, sse, streamable-http) or runtime (npm, docker, uvx). "
                "Queries the live API for freshness."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keyword (e.g., 'postgres', 'slack', 'filesystem')",
                    },
                    "transport": {
                        "type": "string",
                        "enum": ["stdio", "http", "sse", "streamable-http"],
                        "description": "Filter by transport type",
                    },
                    "runtime": {
                        "type": "string",
                        "enum": ["npm", "docker", "uvx", "npx", "pip", "cargo", "go"],
                        "description": "Filter by package runtime / registry type",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 20, max 100)",
                    },
                },
                "required": [],
            },
        },
        "handler": handle_mcp_marketplace_search,
        "description": "Search MCP servers in the official registry",
        "emoji": "🔍",
    },
    {
        "name": "mcp_marketplace_list",
        "schema": {
            "name": "mcp_marketplace_list",
            "description": (
                "List available MCP servers from the local cache or live registry. "
                "Can filter by transport type."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["cached", "live"],
                        "description": "Source: cached (default) or live API",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default: 30)",
                    },
                    "transport": {
                        "type": "string",
                        "enum": ["stdio", "http", "sse", "streamable-http"],
                        "description": "Filter by transport type",
                    },
                },
                "required": [],
            },
        },
        "handler": handle_mcp_marketplace_list,
        "description": "List available MCP servers",
        "emoji": "📋",
    },
    {
        "name": "mcp_marketplace_info",
        "schema": {
            "name": "mcp_marketplace_info",
            "description": (
                "Get detailed information about a specific MCP server from the official registry. "
                "Includes install commands, required environment variables, and transport options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Server namespace ID (e.g., 'com.pulsemcp/remote-filesystem', 'io.github.Digital-Defiance/mcp-filesystem')",
                    },
                },
                "required": ["id"],
            },
        },
        "handler": handle_mcp_marketplace_info,
        "description": "Get detailed info about an MCP server",
        "emoji": "ℹ️",
    },
    {
        "name": "mcp_marketplace_add",
        "schema": {
            "name": "mcp_marketplace_add",
            "description": (
                "Install/add an MCP server from the official registry into Hermes. "
                "Auto-detects transport (stdio vs http) and configures the right command or URL. "
                "For stdio servers, uses the registry's declared install command (npx, uvx, docker, etc.). "
                "For remote servers, configures the endpoint URL and headers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Server namespace ID (e.g., 'com.pulsemcp/remote-filesystem')",
                    },
                    "name": {
                        "type": "string",
                        "description": "Friendly name for Hermes MCP config (default: sanitized repo name)",
                    },
                    "transport": {
                        "type": "string",
                        "enum": ["stdio", "http", "sse", "streamable-http"],
                        "description": "Transport type. Default: auto-detect from registry metadata",
                    },
                    "env": {
                        "type": "object",
                        "description": "Environment variable overrides (e.g., {'GCS_BUCKET': 'my-bucket'}). Registry-required vars are auto-populated as empty placeholders.",
                    },
                },
                "required": ["id"],
            },
        },
        "handler": handle_mcp_marketplace_add,
        "description": "Install an MCP server into Hermes from the official registry",
        "emoji": "➕",
    },
]
