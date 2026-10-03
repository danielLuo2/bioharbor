"""`bioharbor` command-line interface."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from . import __version__
from . import gpu as gpu_mod
from .databases import KNOWN_DATABASES, list_databases, setup_database
from .harbor import Harbor, default_home
from .registry import load_tools

app = typer.Typer(
    help="BioHarbor: run real bioinformatics from your AI agent.",
    no_args_is_help=True,
    add_completion=False,
)


def _print(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=2, ensure_ascii=False))


@app.command()
def version() -> None:
    """Print the BioHarbor version."""
    typer.echo(__version__)


@app.command()
def doctor() -> None:
    """Check that this machine is ready: Python, GPUs, workspace, tools."""
    ok = True
    typer.echo(
        f"bioharbor {__version__} | Python {platform.python_version()} | "
        f"{platform.system()} {platform.machine()}"
    )

    home = default_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        typer.echo(f"[ok]   workspace       {home}")
    except OSError as exc:
        ok = False
        typer.echo(f"[fail] workspace       {home}: {exc}")

    gpus = gpu_mod.probe()
    if gpus:
        for g in gpus:
            typer.echo(
                f"[ok]   gpu {g.index}           {g.name}: {g.mem_free_gb:.1f}/"
                f"{g.mem_total_gb:.1f} GB free, {g.util_pct}% busy"
            )
    else:
        typer.echo("[warn] gpu             none visible (GPU tools will be unavailable)")
    try:
        import pynvml  # noqa: F401
    except ImportError:
        typer.echo(
            "[info] nvml            not installed; using nvidia-smi "
            "(pip install 'bioharbor[gpu]' for faster polling)"
        )

    tools = load_tools()
    typer.echo(f"[ok]   tools           {len(tools)} registered: {', '.join(sorted(tools))}")
    mmseqs = os.environ.get("BIOHARBOR_MMSEQS") or shutil.which("mmseqs")
    typer.echo(
        f"{'[ok]  ' if mmseqs else '[warn]'} {'mmseqs':<15} "
        f"{mmseqs or 'not found; search_homologs unavailable (conda install -c bioconda mmseqs2)'}"
    )
    dbs = list_databases(home)
    names = ", ".join(d.name for d in dbs)
    typer.echo(
        f"{'[ok]  ' if dbs else '[info]'} {'databases':<15} "
        f"{names or 'none installed (bioharbor setup-db swissprot)'}"
    )
    try:
        import torch
        import transformers  # noqa: F401

        cuda = torch.cuda.is_available()
        typer.echo(
            f"{'[ok]  ' if cuda else '[warn]'} {'esmfold':<15} torch {torch.__version__}, "
            f"CUDA {'available' if cuda else 'NOT available (predict_structure needs a GPU)'}"
        )
    except ImportError:
        typer.echo(f"[info] {'esmfold':<15} not installed (pip install 'bioharbor[esmfold]')")
    raise typer.Exit(0 if ok else 1)


tools_app = typer.Typer(help="Inspect available tools.", no_args_is_help=True)
app.add_typer(tools_app, name="tools")


@tools_app.command("list")
def tools_list() -> None:
    """List registered tools."""
    for name, spec in sorted(load_tools().items()):
        first_line = spec.description.splitlines()[0] if spec.description else ""
        kind = "job" if spec.slow else "inline"
        gpu = " gpu" if spec.resources.gpu else ""
        line = f"{name:<20} [{kind}{gpu}] {first_line}"
        typer.echo(_fit(line))


def _fit(line: str) -> str:
    """Truncate to the terminal width so lists stay one line per item."""
    width = shutil.get_terminal_size((120, 24)).columns
    return line if len(line) <= width else line[: width - 1] + "…"


@tools_app.command("describe")
def tools_describe(name: str) -> None:
    """Show a tool's description and input schema."""
    harbor = Harbor(workers=0)
    _print(harbor.describe(name))


def _parse_kv(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise typer.BadParameter(f"expected key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        if value.startswith("@"):  # key=@file.fa reads the file
            value = Path(value[1:]).read_text()
        else:
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        out[key] = value
    return out


@app.command()
def run(
    tool_name: Annotated[str, typer.Argument(help="Tool to run, e.g. seq_stats")],
    params: Annotated[
        list[str] | None,
        typer.Argument(help="key=value pairs; key=@path reads a file (e.g. sequence=@query.fa)"),
    ] = None,
    wait: Annotated[float, typer.Option(help="Seconds to wait for slow tools.")] = 3600,
    brief: Annotated[
        bool, typer.Option("--brief", "-b", help="Human-readable summary instead of JSON.")
    ] = False,
) -> None:
    """Run a tool exactly as an agent would, and print the JSON result."""
    harbor = Harbor()
    try:
        result = harbor.run(tool_name, _parse_kv(params or []), wait_s=wait)
    except KeyError as exc:
        typer.echo(str(exc.args[0]), err=True)
        raise typer.Exit(2) from exc
    finally:
        harbor.close()
    if brief:
        typer.echo(format_brief(result))
    else:
        _print(result)
    raise typer.Exit(1 if result.get("status") == "failed" else 0)


def format_brief(out: dict[str, Any]) -> str:
    """One status line, the tool's message, then suggestions and output files."""
    status = out.get("status", "?")
    mark = {"succeeded": "✓", "failed": "✗"}.get(status, "…")
    head = f"{mark} {out.get('tool', '')} {status}"
    if "duration_s" in out:
        d = out["duration_s"]
        head += f" in {d * 1000:.0f} ms" if d < 1 else f" in {d:.1f}s"
    if out.get("gpu") is not None:
        head += f" on GPU {out['gpu']}"
    lines = [head]
    if err := out.get("error"):
        lines.append(f"  {err.get('message')}")
        if err.get("hint"):
            lines.append(f"  hint: {err['hint']}")
    if res := out.get("result"):
        lines.append(f"  {res.get('message', '')}")
        lines += [f"  → {s}" for s in res.get("suggestions", [])]
        lines += [f"  file: {f}" for f in res.get("files", [])]
    if out.get("hint"):
        lines.append(f"  {out['hint']}")
    return "\n".join(lines)


@app.command()
def jobs(limit: int = 20, status: str | None = None) -> None:
    """List recent jobs."""
    harbor = Harbor(workers=0)
    for j in harbor.store.list(limit=limit, status=status):
        d = j.to_dict()
        typer.echo(f"{j.id}  {j.status:<11} {j.tool:<22} {d.get('duration_s', '')}")


@app.command("setup-db")
def setup_db(
    name: Annotated[
        str,
        typer.Argument(help=f"One of: {', '.join(KNOWN_DATABASES)}, or any name with --from-fasta"),
    ],
    from_fasta: Annotated[Path | None, typer.Option(help="Build from your own FASTA.")] = None,
    threads: int = 4,
) -> None:
    """Download and build a sequence database for search_homologs."""
    from .databases import INSTALL_MMSEQS
    from .errors import BioHarborError
    from .runtime import require_binary

    try:
        mmseqs = require_binary("mmseqs", INSTALL_MMSEQS)
        if from_fasta is None and name in KNOWN_DATABASES:
            info = KNOWN_DATABASES[name]
            typer.echo(f"Downloading {info['source']} ({info['size']}) ...")
        db = setup_database(default_home(), name, mmseqs, from_fasta=from_fasta, threads=threads)
    except BioHarborError as exc:
        typer.echo(f"error: {exc} ({exc.hint})", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Installed {db.name}: {db.meta.get('n_sequences')} sequences at {db.prefix}")


@app.command()
def databases() -> None:
    """List installed databases."""
    dbs = list_databases(default_home())
    if not dbs:
        typer.echo("No databases installed. Try: bioharbor setup-db swissprot")
    for d in dbs:
        typer.echo(
            f"{d.name:<12} {d.meta.get('n_sequences')} seqs  source={d.meta['source']} "
            f"created={d.meta['created']}"
        )


@app.command()
def serve(
    http: Annotated[
        bool, typer.Option(help="Serve over Streamable HTTP instead of stdio.")
    ] = False,
    host: str = "127.0.0.1",
    port: int = 8765,
    workers: Annotated[int, typer.Option(help="Concurrent background jobs.")] = 2,
) -> None:
    """Start the MCP server (stdio for Claude Desktop/Code, or --http for shared use)."""
    from .server import build_server

    harbor = Harbor(workers=workers, recover=True)
    if harbor.recovered:
        typer.echo(f"marked {harbor.recovered} interrupted job(s) as failed", err=True)
    server = build_server(harbor)
    try:
        if http:
            typer.echo(f"BioHarbor MCP on http://{host}:{port}/mcp", err=True)
            server.run("streamable-http", host=host, port=port)
        else:
            server.run("stdio")
    finally:
        harbor.close()


def _claude_desktop_config() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home())) / "Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


@app.command("install-claude")
def install_claude(
    write: Annotated[
        bool,
        typer.Option(
            help="Add BioHarbor to the Claude Desktop config file (a .bak backup is made)."
        ),
    ] = False,
) -> None:
    """Show (or write) the config that connects Claude Desktop / Claude Code to BioHarbor."""
    exe = shutil.which("bioharbor") or sys.executable
    args = (
        ["serve"] if exe.endswith(("bioharbor", "bioharbor.exe")) else ["-m", "bioharbor", "serve"]
    )
    entry = {"command": exe, "args": args}
    typer.echo("Claude Code:\n  claude mcp add bioharbor -- " + " ".join([exe, *args]) + "\n")
    cfg = _claude_desktop_config()
    if not write:
        typer.echo(f"Claude Desktop ({cfg}):")
        _print({"mcpServers": {"bioharbor": entry}})
        typer.echo("\nRe-run with --write to add it automatically.")
        return
    data: dict[str, Any] = {}
    if cfg.exists():
        data = json.loads(cfg.read_text() or "{}")
        shutil.copy2(cfg, cfg.with_suffix(".json.bak"))
    data.setdefault("mcpServers", {})["bioharbor"] = entry
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(data, indent=2))
    typer.echo(f"Added bioharbor to {cfg}. Restart Claude Desktop to load it.")


if __name__ == "__main__":
    app()
