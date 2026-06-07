"""
hermes-github-mcp-marketplace
=============================

Browse, search, and install MCP servers from GitHub's MCP Registry
and community sources directly into Hermes.

Tools:
  - mcp_marketplace_search    Search for MCP servers by keyword, category, or owner
  - mcp_marketplace_list      List cached/available MCP servers
  - mcp_marketplace_info      Get detailed info about a specific server
  - mcp_marketplace_add       Install/add an MCP server to Hermes
  - mcp_marketplace_refresh   Refresh the local cache from GitHub

Requires:
  GITHUB_TOKEN – GitHub Personal Access Token (for higher rate limits & private repos)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"
MCP_REGISTRY_ORG = "mcp"
MCP_TOPIC = "mcp-server"
CACHE_FILE = Path(__file__).parent / "cache.json"


def _github_headers() -> dict:
    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    return {"Accept": "application/vnd.github+json"}


def _check_available() -> bool:
    # Token optional but strongly recommended for rate limits
    return True


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
    """Simple GET with error handling."""
    try:
        import httpx
    except ImportError:
        return {"error": "httpx is not installed. Run: pip install httpx"}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=_github_headers(), params=params)
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                return {"error": "GitHub API rate limit exceeded. Set GITHUB_TOKEN for higher limits."}
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def _discover_registry_servers() -> List[Dict[str, Any]]:
    """Fetch servers from the official github.com/mcp registry organization."""
    results = []
    # Get repos in the mcp org
    data = _fetch_json(f"{GITHUB_API}/orgs/{MCP_REGISTRY_ORG}/repos", params={"per_page": 100, "type": "public"})
    if "error" in data:
        logger.warning("Failed to fetch mcp org repos: %s", data["error"])
        return results

    for repo in data:
        if not isinstance(repo, dict):
            continue
        # Skip the registry repo itself and non-server repos
        name = repo.get("name", "")
        if name in ("mcp", ".github"):
            continue
        # Determine transport type from description or topics
        topics = repo.get("topics", [])
        desc = repo.get("description") or ""
        transport = _infer_transport(topics, desc, repo.get("html_url", ""))
        results.append({
            "id": f"mcp/{name}",
            "name": name,
            "owner": MCP_REGISTRY_ORG,
            "full_name": repo.get("full_name"),
            "description": desc,
            "stars": repo.get("stargazers_count", 0),
            "url": repo.get("html_url"),
            "topics": topics,
            "transport": transport,
            "source": "github_registry",
            "language": repo.get("language"),
            "license": repo.get("license", {}).get("name") if repo.get("license") else None,
            "updated_at": repo.get("updated_at"),
        })
    return results


def _discover_community_servers(query: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
    """Search GitHub for community MCP servers by topic or keyword."""
    q = f"topic:{MCP_TOPIC}"
    if query:
        q += f" {query}"
    data = _fetch_json(f"{GITHUB_API}/search/repositories", params={"q": q, "sort": "stars", "order": "desc", "per_page": limit})
    if "error" in data:
        logger.warning("GitHub search failed: %s", data["error"])
        return []

    results = []
    for repo in data.get("items", []):
        topics = repo.get("topics", [])
        desc = repo.get("description") or ""
        transport = _infer_transport(topics, desc, repo.get("html_url", ""))
        results.append({
            "id": f"{repo['owner']['login']}/{repo['name']}",
            "name": repo.get("name"),
            "owner": repo["owner"]["login"],
            "full_name": repo.get("full_name"),
            "description": desc,
            "stars": repo.get("stargazers_count", 0),
            "url": repo.get("html_url"),
            "topics": topics,
            "transport": transport,
            "source": "community",
            "language": repo.get("language"),
            "license": repo.get("license", {}).get("name") if repo.get("license") else None,
            "updated_at": repo.get("updated_at"),
        })
    return results


def _infer_transport(topics: List[str], description: str, html_url: str) -> str:
    """Best-guess the MCP transport type from repo metadata."""
    d = (description or "").lower()
    if "http" in d or "remote" in d or "sse" in topics:
        return "http"
    if "stdio" in d or "stdio" in topics:
        return "stdio"
    # Default to stdio for most local servers
    return "stdio"


def _fetch_readme(owner: str, repo: str) -> str:
    """Fetch decoded README content from GitHub."""
    data = _fetch_json(f"{GITHUB_API}/repos/{owner}/{repo}/readme")
    if "error" in data:
        return ""
    import base64
    content = data.get("content", "")
    if data.get("encoding") == "base64":
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            return ""
    return content


def _extract_install_hints(readme: str, language: Optional[str]) -> Dict[str, Optional[str]]:
    """Parse README for Docker, npx, uvx, pip install, or go install hints."""
    hints: Dict[str, Optional[str]] = {"docker": None, "npx": None, "uvx": None, "pip": None, "go": None, "cargo": None, "npm": None}

    # Docker image
    docker_match = re.search(r"ghcr\.io/[^\s\)]+|docker run[^\n]*-i[^\n]*(?:ghcr|github)", readme, re.IGNORECASE)
    if docker_match:
        hints["docker"] = docker_match.group(0).strip()

    # npx
    npx_match = re.search(r"npx\s+[^\n\r]+", readme)
    if npx_match:
        hints["npx"] = npx_match.group(0).strip()

    # uvx
    uvx_match = re.search(r"uvx?\s+(?:run\s+)?[^\n\r]+", readme, re.IGNORECASE)
    if uvx_match:
        hints["uvx"] = uvx_match.group(0).strip()

    # pip
    pip_match = re.search(r"pip\s+install\s+[^\n\r]+", readme, re.IGNORECASE)
    if pip_match:
        hints["pip"] = pip_match.group(0).strip()

    # npm global
    npm_match = re.search(r"npm\s+(?:i|install)\s+-g\s+[^\n\r]+", readme, re.IGNORECASE)
    if npm_match:
        hints["npm"] = npm_match.group(0).strip()

    # go install
    go_match = re.search(r"go\s+install\s+[^\n\r]+", readme, re.IGNORECASE)
    if go_match:
        hints["go"] = go_match.group(0).strip()

    # cargo install
    cargo_match = re.search(r"cargo\s+install\s+[^\n\r]+", readme, re.IGNORECASE)
    if cargo_match:
        hints["cargo"] = cargo_match.group(0).strip()

    return {k: v for k, v in hints.items() if v}


def _extract_env_vars(readme: str) -> List[str]:
    """Pull likely env var names from README (e.g., GITHUB_PERSONAL_ACCESS_TOKEN)."""
    envs = re.findall(r"[A-Z][A-Z0-9_]*(?:_API_KEY|_TOKEN|_SECRET|_URL|_HOST)", readme)
    return sorted(set(envs))


def _hermes_mcp_add_command(name: str, command: List[str], env: Optional[dict] = None) -> dict:
    """Add an MCP server to Hermes via the CLI."""
    try:
        cmd = ["hermes", "mcp", "add", name, "--command", json.dumps(command)]
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
    """Refresh the local cache of MCP servers from GitHub sources."""
    registry = _discover_registry_servers()
    community = _discover_community_servers(limit=50)
    all_servers = registry + [s for s in community if s["id"] not in {r["id"] for r in registry}]
    cache = {"servers": all_servers, "last_updated": __import__("datetime").datetime.utcnow().isoformat() + "Z"}
    _save_cache(cache)
    categories = {}
    for s in all_servers:
        for t in s.get("topics", []):
            categories[t] = categories.get(t, 0) + 1
    summary = {
        "total_servers": len(all_servers),
        "registry_count": len(registry),
        "community_count": len(community),
        "top_categories": sorted(categories.items(), key=lambda x: x[1], reverse=True)[:10],
    }
    return json.dumps(summary, indent=2)


def handle_mcp_marketplace_search(args: dict, **kw) -> str:
    """Search for MCP servers by keyword, category, or owner."""
    query = args.get("query", "").strip()
    category = args.get("category", "").strip()
    transport = args.get("transport", "").strip().lower()
    limit = args.get("limit", 20)

    cache = _load_cache()
    servers = cache.get("servers", [])

    if not servers:
        # Auto-refresh if cache is empty
        handle_mcp_marketplace_refresh({})
        cache = _load_cache()
        servers = cache.get("servers", [])

    results = []
    q_lower = query.lower()
    for s in servers:
        score = 0
        text = f"{s.get('name','')} {s.get('description','')} {' '.join(s.get('topics',[]))}"
        if q_lower and q_lower in text.lower():
            score += 10
            # Prefer name match
            if q_lower in s.get("name", "").lower():
                score += 5
        if category and category.lower() in [t.lower() for t in s.get("topics", [])]:
            score += 8
        if transport and s.get("transport", "").lower() == transport:
            score += 3
        if not query and not category:
            score = s.get("stars", 0)  # Default ranking by stars
        if score > 0 or (not query and not category and not transport):
            results.append({**s, "_score": score})

    results.sort(key=lambda x: x["_score"], reverse=True)
    results = results[:limit]
    for r in results:
        r.pop("_score", None)
    return json.dumps(results, indent=2, default=str)


def handle_mcp_marketplace_list(args: dict, **kw) -> str:
    """List available MCP servers (cached or live)."""
    source = args.get("source", "cached")  # cached | registry | community | all
    limit = args.get("limit", 30)
    transport = args.get("transport", "").strip().lower()

    if source == "registry":
        servers = _discover_registry_servers()
    elif source == "community":
        servers = _discover_community_servers(limit=limit)
    elif source == "all":
        r = _discover_registry_servers()
        c = _discover_community_servers(limit=limit)
        seen = {s["id"] for s in r}
        servers = r + [s for s in c if s["id"] not in seen]
    else:
        cache = _load_cache()
        servers = cache.get("servers", [])
        if not servers:
            handle_mcp_marketplace_refresh({})
            cache = _load_cache()
            servers = cache.get("servers", [])

    if transport:
        servers = [s for s in servers if s.get("transport", "").lower() == transport]

    servers = sorted(servers, key=lambda x: x.get("stars", 0), reverse=True)[:limit]
    return json.dumps(servers, indent=2, default=str)


def handle_mcp_marketplace_info(args: dict, **kw) -> str:
    """Get detailed info about a specific MCP server by owner/repo or ID."""
    owner = args.get("owner", "").strip()
    repo = args.get("repo", "").strip()
    server_id = args.get("id", "").strip()

    if server_id and not owner:
        parts = server_id.split("/")
        if len(parts) == 2:
            owner, repo = parts

    if not owner or not repo:
        return json.dumps({"error": "Provide owner + repo, or id in 'owner/repo' format."})

    # Fetch repo metadata
    meta = _fetch_json(f"{GITHUB_API}/repos/{owner}/{repo}")
    if "error" in meta:
        return json.dumps({"error": meta["error"]})

    readme = _fetch_readme(owner, repo)
    hints = _extract_install_hints(readme, meta.get("language"))
    envs = _extract_env_vars(readme)

    info = {
        "id": f"{owner}/{repo}",
        "name": meta.get("name"),
        "owner": meta["owner"]["login"],
        "description": meta.get("description"),
        "stars": meta.get("stargazers_count"),
        "url": meta.get("html_url"),
        "topics": meta.get("topics", []),
        "language": meta.get("language"),
        "license": meta.get("license", {}).get("name") if meta.get("license") else None,
        "updated_at": meta.get("updated_at"),
        "install_hints": hints,
        "likely_env_vars": envs,
        "readme_snippet": readme[:2000] if readme else "",
    }
    return json.dumps(info, indent=2, default=str)


def handle_mcp_marketplace_add(args: dict, **kw) -> str:
    """Add/install an MCP server into Hermes from the marketplace."""
    server_id = args.get("id", "").strip()
    owner = args.get("owner", "").strip()
    repo = args.get("repo", "").strip()
    name = args.get("name", "").strip()  # Hermes MCP server name
    transport = args.get("transport", "stdio").strip().lower()
    env = args.get("env", {})  # Optional env vars dict

    if server_id and not owner:
        parts = server_id.split("/")
        if len(parts) == 2:
            owner, repo = parts

    if not owner or not repo:
        return json.dumps({"error": "Provide owner + repo, or id in 'owner/repo' format."})

    if not name:
        name = repo.replace("-", "_").replace(" ", "_")

    # Get repo info to figure out how to run it
    info_json = handle_mcp_marketplace_info({"owner": owner, "repo": repo}, **kw)
    info = json.loads(info_json)
    if "error" in info:
        return json.dumps({"error": f"Could not fetch server info: {info['error']}"})

    hints = info.get("install_hints", {})
    language = info.get("language", "").lower()
    likely_envs = info.get("likely_env_vars", [])

    # Build command based on detected install hints / language
    command: List[str] = []

    if "docker" in hints:
        # Try to extract a clean docker run command
        docker_hint = hints["docker"]
        # Simple heuristic: if it mentions ghcr.io, use docker run -i --rm
        image_match = re.search(r"(ghcr\.io/[^\s\)]+)", docker_hint)
        if image_match:
            image = image_match.group(1)
            command = ["docker", "run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", image]
    elif "npx" in hints:
        # Extract package name from npx command
        npx_cmd = hints["npx"]
        pkg_match = re.search(r"npx\s+(-y\s+)?([^\s]+)", npx_cmd)
        pkg = pkg_match.group(2) if pkg_match else f"@{owner}/{repo}"
        command = ["npx", "-y", pkg]
    elif "uvx" in hints:
        uvx_cmd = hints["uvx"]
        pkg_match = re.search(r"uvx?\s+(?:run\s+)?([^\s]+)", uvx_cmd, re.IGNORECASE)
        pkg = pkg_match.group(1) if pkg_match else f"{owner}/{repo}"
        command = ["uvx", pkg]
    elif "go" in hints and language == "go":
        command = [repo]  # go install binaries typically have matching name
    elif "cargo" in hints:
        command = [repo.replace("-", "_")]
    elif "npm" in hints:
        npm_cmd = hints["npm"]
        pkg_match = re.search(r"npm\s+(?:i|install)\s+-g\s+([^\s]+)", npm_cmd)
        pkg = pkg_match.group(1) if pkg_match else f"@{owner}/{repo}"
        command = [pkg]
    elif language in ("typescript", "javascript"):
        # Default to npx for TS/JS repos
        command = ["npx", "-y", f"@{owner}/{repo}"]
    elif language == "python":
        command = ["uvx", f"{owner}/{repo}"]
    elif language == "go":
        command = [repo]
    else:
        # Fallback: stdio with a placeholder telling user to configure
        command = ["echo", f"Please configure the command for {owner}/{repo} manually in Hermes MCP config."]

    # Build env dict from args + detected env vars (with empty placeholders)
    merged_env: Dict[str, str] = {}
    for e in likely_envs:
        merged_env[e] = env.get(e, "")
    for k, v in env.items():
        merged_env[k] = v

    # If transport is http, we need a URL instead of command
    if transport == "http":
        url = args.get("url", "").strip()
        if not url:
            # Try to guess from README or known patterns
            if "github" in owner.lower() and "mcp" in repo.lower():
                url = "https://api.githubcopilot.com/mcp/"
            else:
                return json.dumps({
                    "error": "HTTP transport requires an explicit 'url' parameter (or known endpoint).",
                    "detected_hints": hints,
                    "likely_env_vars": likely_envs,
                })
        # Use hermes mcp add with --url
        try:
            cmd = ["hermes", "mcp", "add", name, "--url", url]
            if merged_env:
                for k, v in merged_env.items():
                    cmd.extend(["--env", f"{k}={v}"])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return json.dumps({
                "success": result.returncode == 0,
                "name": name,
                "transport": "http",
                "url": url,
                "env": merged_env,
                "cli_stdout": result.stdout,
                "cli_stderr": result.stderr,
                "returncode": result.returncode,
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})
    else:
        # stdio via command
        result = _hermes_mcp_add_command(name, command, merged_env)
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
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "mcp_marketplace_refresh",
        "schema": {
            "name": "mcp_marketplace_refresh",
            "description": "Refresh the local cache of MCP servers from GitHub Registry and community sources.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        "handler": handle_mcp_marketplace_refresh,
        "description": "Refresh MCP server cache from GitHub",
        "emoji": "🔄",
    },
    {
        "name": "mcp_marketplace_search",
        "schema": {
            "name": "mcp_marketplace_search",
            "description": (
                "Search the GitHub MCP marketplace/registry for MCP servers by keyword, "
                "category (topic), or transport type. Returns ranked results with metadata."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keyword (e.g., 'postgres', 'slack', 'scrape')",
                    },
                    "category": {
                        "type": "string",
                        "description": "GitHub topic/category to filter by (e.g., 'database', 'browser')",
                    },
                    "transport": {
                        "type": "string",
                        "enum": ["stdio", "http", "sse"],
                        "description": "Filter by transport type",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 20)",
                    },
                },
                "required": [],
            },
        },
        "handler": handle_mcp_marketplace_search,
        "description": "Search for MCP servers in the marketplace",
        "emoji": "🔍",
    },
    {
        "name": "mcp_marketplace_list",
        "schema": {
            "name": "mcp_marketplace_list",
            "description": (
                "List available MCP servers from the marketplace cache or live sources. "
                "Can filter by source (official registry vs community) and transport type."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["cached", "registry", "community", "all"],
                        "description": "Source to list from. Default: cached",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default: 30)",
                    },
                    "transport": {
                        "type": "string",
                        "enum": ["stdio", "http", "sse"],
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
                "Get detailed information about a specific MCP server from its GitHub repo. "
                "Includes README analysis, install hints, and likely required environment variables."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Server ID in 'owner/repo' format (e.g., 'mcp/github')",
                    },
                    "owner": {
                        "type": "string",
                        "description": "GitHub owner/organization",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository name",
                    },
                },
                "required": [],
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
                "Install/add an MCP server from the marketplace into Hermes. "
                "Auto-detects the run command from README hints (docker, npx, uvx, pip, go, cargo). "
                "For stdio servers, configures the command. For HTTP servers, configures the URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Server ID in 'owner/repo' format",
                    },
                    "owner": {
                        "type": "string",
                        "description": "GitHub owner",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository name",
                    },
                    "name": {
                        "type": "string",
                        "description": "Friendly name for Hermes MCP config (default: repo name)",
                    },
                    "transport": {
                        "type": "string",
                        "enum": ["stdio", "http", "sse"],
                        "description": "Transport type. Default: auto-detect, fallback stdio",
                    },
                    "env": {
                        "type": "object",
                        "description": "Environment variables to configure for the server (e.g., {'GITHUB_PERSONAL_ACCESS_TOKEN': 'ghp_xxx'})",
                    },
                    "url": {
                        "type": "string",
                        "description": "Required for HTTP transport: the MCP server endpoint URL",
                    },
                },
                "required": [],
            },
        },
        "handler": handle_mcp_marketplace_add,
        "description": "Install an MCP server into Hermes from the marketplace",
        "emoji": "➕",
    },
]
