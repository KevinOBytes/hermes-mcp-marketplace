#!/usr/bin/env python3
"""
hermes-mcp-marketplace TUI
==========================

Interactive terminal browser for the official MCP Registry.
Browse by category, paginate through servers, preview details,
and install directly into Hermes.

Usage:
    python3 scripts/marketplace_tui.py

Requirements:
    pip install httpx rich textual
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

try:
    import httpx
except ImportError:
    print("ERROR: httpx is required. Run: pip install httpx")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.layout import Layout
    from rich.prompt import Prompt, Confirm
    from rich.status import Status
except ImportError:
    print("ERROR: rich is required. Run: pip install rich")
    sys.exit(1)

console = Console()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
API_VERSION = "v0"
CACHE_FILE = Path(__file__).parent.parent / "cache.json"
PAGE_SIZE = 15


def _fetch_json(url: str, params: Optional[dict] = None) -> dict:
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"error": str(e)}


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {"servers": [], "last_updated": None}


def _save_cache(data: dict) -> None:
    CACHE_FILE.write_text(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Registry normalization (same logic as plugin)
# ---------------------------------------------------------------------------

def _normalize_server(raw: dict) -> dict:
    server = raw.get("server", {})
    meta = raw.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
    name = server.get("name", "")
    display_name = name.split("/")[-1] if "/" in name else name

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

    install_commands: List[dict] = []
    for p in packages:
        if not isinstance(p, dict):
            continue
        cmd = _package_to_command(p)
        if cmd:
            install_commands.append(cmd)

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

    for arg in runtime_args:
        v = arg.get("value", "")
        t = arg.get("type", "positional")
        if t == "positional" and v:
            command.append(v)

    return {"command": command, "env": env, "registry_type": registry_type, "identifier": identifier}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def refresh_registry(limit: int = 100, max_pages: int = 50) -> List[dict]:
    """Fetch all pages from registry and cache."""
    all_servers: List[dict] = []
    seen = set()
    cursor: Optional[str] = None
    pages = 0

    with Status("[bold green]Fetching servers from registry.modelcontextprotocol.io...", console=console):
        while pages < max_pages:
            pages += 1
            params: Dict[str, Any] = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            data = _fetch_json(f"{REGISTRY_BASE}/{API_VERSION}/servers", params=params)
            if "error" in data:
                console.print(f"[red]Error: {data['error']}[/red]")
                break
            servers = data.get("servers", [])
            if not servers:
                break
            for s in servers:
                norm = _normalize_server(s)
                sid = norm["id"]
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
    console.print(f"[green]Fetched {len(all_servers)} servers across {pages} pages[/green]")
    return all_servers


def load_servers(force_refresh: bool = False) -> List[dict]:
    if force_refresh:
        return refresh_registry()
    cache = _load_cache()
    if not cache.get("servers"):
        console.print("[yellow]Cache empty — fetching from registry...[/yellow]")
        return refresh_registry()
    return cache["servers"]


# ---------------------------------------------------------------------------
# Category analysis
# ---------------------------------------------------------------------------

def compute_categories(servers: List[dict]) -> Dict[str, int]:
    cats: Dict[str, int] = {}
    for s in servers:
        # Derive categories from namespace prefix and transports
        ns = s.get("namespace", "")
        parts = ns.split(".")
        if len(parts) >= 2:
            org = parts[1] if parts[0] in ("com", "io", "ai", "app", "dev") else parts[0]
            cats[org] = cats.get(org, 0) + 1
        for t in s.get("transports", []):
            cats[f"transport:{t}"] = cats.get(f"transport:{t}", 0) + 1
    return dict(sorted(cats.items(), key=lambda x: x[1], reverse=True))


# ---------------------------------------------------------------------------
# TUI Screens
# ---------------------------------------------------------------------------

def draw_header(servers: List[dict], current_page: int, total_pages: int, filter_text: str = "") -> None:
    title = Text("Hermes MCP Marketplace", style="bold cyan")
    subtitle = Text(f"Official Registry | {len(servers)} servers | Page {current_page}/{total_pages}", style="dim")
    if filter_text:
        subtitle.append(f" | Filter: {filter_text}", style="yellow")
    console.print(Panel.fit(title + "\n" + subtitle, border_style="cyan"))


def draw_server_list(servers: List[dict], page: int, page_size: int, selected_idx: int) -> None:
    start = (page - 1) * page_size
    end = start + page_size
    page_servers = servers[start:end]

    table = Table(show_header=True, header_style="bold magenta", box="ROUNDED")
    table.add_column("#", width=3, justify="right")
    table.add_column("Name", min_width=20)
    table.add_column("Version", width=8)
    table.add_column("Transports", min_width=12)
    table.add_column("Description", min_width=30, max_width=50)
    table.add_column("Status", width=8)

    for i, s in enumerate(page_servers):
        idx = start + i
        style = "reverse bold" if idx == selected_idx else ""
        name = s.get("title") or s.get("name", "")
        transports = ", ".join(s.get("transports", []))
        desc = (s.get("description", "") or "")[:60]
        status = "[green]latest[/green]" if s.get("is_latest") else "[dim]old[/dim]"
        table.add_row(str(idx + 1), name, s.get("version", ""), transports, desc, status, style=style)

    console.print(table)


def draw_server_detail(s: dict) -> None:
    panel_parts: List[str] = []
    panel_parts.append(f"[bold cyan]ID:[/bold cyan] {s['id']}")
    panel_parts.append(f"[bold]Version:[/bold] {s.get('version', '')}")
    panel_parts.append(f"[bold]Transports:[/bold] {', '.join(s.get('transports', []))}")
    if s.get("url"):
        panel_parts.append(f"[bold]URL:[/bold] {s['url']}")
    if s.get("repository", {}).get("url"):
        panel_parts.append(f"[bold]Repo:[/bold] {s['repository']['url']}")
    panel_parts.append(f"\n[bold]Description:[/bold] {s.get('description', 'N/A')}")

    if s.get("install_commands"):
        panel_parts.append("\n[bold green]Install Commands:[/bold green]")
        for cmd in s["install_commands"]:
            panel_parts.append(f"  {' '.join(cmd['command'])}")

    if s.get("env_vars"):
        panel_parts.append("\n[bold yellow]Environment Variables:[/bold yellow]")
        for e in s["env_vars"]:
            req = "[red]required[/red]" if e.get("required") else "optional"
            sec = " [red](secret)[/red]" if e.get("secret") else ""
            panel_parts.append(f"  {e['name']}: {e.get('description', '')} ({req}){sec}")

    console.print(Panel("\n".join(panel_parts), title=s.get("title") or s.get("name"), border_style="green"))


def draw_help() -> None:
    help_text = """
[bold]Navigation[/bold]
  j/↓      Move down
  k/↑      Move up
  d        Page down
  u        Page up
  g        Go to first page
  G        Go to last page

[bold]Actions[/bold]
  Enter    View server details
  i        Install into Hermes
  o        Open repository URL in browser
  r        Refresh registry cache
  s        Search
  c        Filter by category
  /        Quick search
  q        Quit

[bold]Install[/bold]
  When installing, you'll be prompted for required env vars.
  The plugin auto-detects stdio (npx/uvx/docker) or HTTP transport.
"""
    console.print(Panel(help_text, title="Help", border_style="blue"))


# ---------------------------------------------------------------------------
# Install logic
# ---------------------------------------------------------------------------

def install_server(s: dict) -> bool:
    name = s["id"].replace("/", "_").replace(".", "_")
    transports = [t.lower() for t in s.get("transports", [])]
    remotes = s.get("remotes", [])
    install_commands = s.get("install_commands", [])

    # Pick transport
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

    # Collect env vars
    env_overrides: Dict[str, str] = {}
    for e in s.get("env_vars", []):
        if e.get("required") or e.get("secret"):
            val = Prompt.ask(f"  {e['name']} ({e.get('description', '')})", password=e.get("secret", False), default=e.get("default", ""))
            env_overrides[e["name"]] = val

    # Build hermes command
    if transport in ("http", "sse", "streamable-http"):
        remote = None
        for r in remotes:
            if r.get("type", "").lower() == transport or (transport == "http" and r.get("type") == "streamable-http"):
                remote = r
                break
        if not remote and remotes:
            remote = remotes[0]
        if not remote:
            console.print("[red]No remote endpoint found.[/red]")
            return False

        url = remote.get("url", "")
        headers = {}
        for h in remote.get("headers", []):
            headers[h["name"]] = h.get("value", "")
        cmd = ["hermes", "mcp", "add", name, "--url", url]
        for k, v in headers.items():
            cmd.extend(["--header", f"{k}={v}"])
        for k, v in env_overrides.items():
            cmd.extend(["--env", f"{k}={v}"])
    else:
        if not install_commands:
            console.print("[red]No installable stdio package found.[/red]")
            return False
        cmd_info = install_commands[0]
        command = cmd_info.get("command", [])
        merged_env = {k: env_overrides.get(k, v) for k, v in cmd_info.get("env", {}).items()}
        for k, v in env_overrides.items():
            merged_env[k] = v
        cmd = ["hermes", "mcp", "add", name, "--command", json.dumps(command)]
        for k, v in merged_env.items():
            cmd.extend(["--env", f"{k}={v}"])

    console.print(f"\n[dim]Running: {' '.join(cmd[:6])}...[/dim]")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            console.print(f"[green]Successfully installed '{name}' into Hermes![/green]")
            return True
        else:
            console.print(f"[red]Install failed (exit {result.returncode}):[/red]")
            console.print(result.stderr or result.stdout)
            return False
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return False


# ---------------------------------------------------------------------------
# Main TUI loop
# ---------------------------------------------------------------------------

def main() -> None:
    console.clear()

    # Load data
    servers = load_servers(force_refresh="--refresh" in sys.argv or "-r" in sys.argv)
    if not servers:
        console.print("[red]No servers found. Check network or try --refresh.[/red]")
        sys.exit(1)

    # State
    filtered_servers = servers[:]
    current_page = 1
    selected_idx = 0
    filter_text = ""
    category_filter = ""
    running = True

    def total_pages() -> int:
        return max(1, (len(filtered_servers) + PAGE_SIZE - 1) // PAGE_SIZE)

    def ensure_bounds():
        nonlocal current_page, selected_idx
        tp = total_pages()
        if current_page > tp:
            current_page = tp
        if selected_idx >= len(filtered_servers):
            selected_idx = max(0, len(filtered_servers) - 1)
        if selected_idx < 0:
            selected_idx = 0

    def redraw():
        console.clear()
        draw_header(filtered_servers, current_page, total_pages(), filter_text)
        draw_server_list(filtered_servers, current_page, PAGE_SIZE, selected_idx)
        console.print("\n[dim]Press 'h' for help | 'q' to quit[/dim]")

    redraw()

    import tty
    import termios
    import select

    def get_key() -> str:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while True:
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                if ready:
                    ch = sys.stdin.read(1)
                    if ch == '\x1b':
                        seq = sys.stdin.read(2)
                        if seq == '[A':
                            return 'UP'
                        elif seq == '[B':
                            return 'DOWN'
                        elif seq == '[5':
                            sys.stdin.read(1)  # ~
                            return 'PAGE_UP'
                        elif seq == '[6':
                            sys.stdin.read(1)  # ~
                            return 'PAGE_DOWN'
                        return f'ESC-{seq}'
                    elif ch == '\n' or ch == '\r':
                        return 'ENTER'
                    elif ch == '\x03':
                        return 'CTRL_C'
                    elif ch == '\x7f':
                        return 'BACKSPACE'
                    else:
                        return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    while running:
        try:
            key = get_key()
        except KeyboardInterrupt:
            break

        if key == 'q' or key == 'CTRL_C':
            running = False
            continue
        elif key == 'h' or key == '?':
            console.clear()
            draw_help()
            Prompt.ask("\nPress Enter to return")
            redraw()
            continue
        elif key == 'j' or key == 'DOWN':
            selected_idx += 1
            if selected_idx >= current_page * PAGE_SIZE:
                if current_page < total_pages():
                    current_page += 1
        elif key == 'k' or key == 'UP':
            selected_idx -= 1
            if selected_idx < (current_page - 1) * PAGE_SIZE:
                if current_page > 1:
                    current_page -= 1
        elif key == 'd' or key == 'PAGE_DOWN':
            if current_page < total_pages():
                current_page += 1
                selected_idx = min(selected_idx, (current_page - 1) * PAGE_SIZE)
        elif key == 'u' or key == 'PAGE_UP':
            if current_page > 1:
                current_page -= 1
                selected_idx = max(selected_idx, (current_page - 1) * PAGE_SIZE)
        elif key == 'g':
            current_page = 1
            selected_idx = 0
        elif key == 'G':
            current_page = total_pages()
            selected_idx = (current_page - 1) * PAGE_SIZE
        elif key == 'ENTER':
            if 0 <= selected_idx < len(filtered_servers):
                console.clear()
                draw_server_detail(filtered_servers[selected_idx])
                Prompt.ask("\nPress Enter to return")
                redraw()
                continue
        elif key == 'i':
            if 0 <= selected_idx < len(filtered_servers):
                console.clear()
                s = filtered_servers[selected_idx]
                draw_server_detail(s)
                if Confirm.ask(f"\nInstall '{s['id']}' into Hermes?"):
                    install_server(s)
                    Prompt.ask("Press Enter to continue")
                redraw()
                continue
        elif key == 'o':
            if 0 <= selected_idx < len(filtered_servers):
                repo = filtered_servers[selected_idx].get("repository", {}).get("url")
                if repo:
                    webbrowser.open(repo)
        elif key == 'r':
            console.clear()
            servers = refresh_registry()
            filtered_servers = servers[:]
            current_page = 1
            selected_idx = 0
            filter_text = ""
            redraw()
            continue
        elif key == 's' or key == '/':
            query = Prompt.ask("Search")
            if query:
                q_lower = query.lower()
                filtered_servers = [
                    s for s in servers
                    if q_lower in (s.get("name", "") + s.get("description", "") + s.get("title", "")).lower()
                ]
                filter_text = query
                current_page = 1
                selected_idx = 0
            else:
                filtered_servers = servers[:]
                filter_text = ""
            redraw()
            continue
        elif key == 'c':
            cats = compute_categories(servers)
            cat_table = Table(title="Categories", box="ROUNDED")
            cat_table.add_column("Category", style="cyan")
            cat_table.add_column("Count", justify="right")
            for cat, count in list(cats.items())[:20]:
                cat_table.add_row(cat, str(count))
            console.clear()
            console.print(cat_table)
            choice = Prompt.ask("Enter category (or blank to clear)")
            if choice:
                if choice.startswith("transport:"):
                    t = choice.replace("transport:", "")
                    filtered_servers = [s for s in servers if t in [x.lower() for x in s.get("transports", [])]]
                else:
                    filtered_servers = [s for s in servers if choice.lower() in s.get("namespace", "").lower()]
                filter_text = f"category:{choice}"
                current_page = 1
                selected_idx = 0
            else:
                filtered_servers = servers[:]
                filter_text = ""
            redraw()
            continue

        ensure_bounds()
        redraw()

    console.clear()
    console.print("[dim]Goodbye.[/dim]")


if __name__ == "__main__":
    main()
