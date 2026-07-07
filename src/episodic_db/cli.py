"""CLI entrypoint for Episodic DB."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import click

from episodic_db.config import Config, EmbeddingConfig
from episodic_db.store.db import Database


@click.group()
@click.option("--db", "db_path", type=click.Path(), default=None, help="Database file path")
@click.pass_context
def cli(ctx, db_path):
    """Episodic DB — deterministic activity memory for Claude Code agents."""
    ctx.ensure_object(dict)
    config = Config()
    if db_path:
        config.db_path = Path(db_path)
    ctx.obj["config"] = config


@cli.command()
@click.option("--port", default=8080, help="Proxy port")
@click.option("--bedrock", is_flag=True, help="Use Bedrock proxy mode")
@click.option("--project-dir", type=click.Path(exists=True), default=".", help="Project directory for settings")
@click.pass_context
def start(ctx, port, bedrock, project_dir):
    """Start proxy and register hooks for Claude Code session."""
    config: Config = ctx.obj["config"]
    config.proxy_port = port
    config.proxy_mode = "bedrock" if bedrock else "direct"

    db = Database(config.db_path)
    db.connect()

    from episodic_db.capture.settings_factory import create_settings
    from episodic_db.proxy.port_utils import find_available_port

    available_port = find_available_port(port)
    if available_port is None:
        click.echo(f"Error: No available port starting from {port}", err=True)
        sys.exit(1)

    settings_path = Path(project_dir) / ".claude" / "settings.json"
    create_settings(
        output_path=settings_path,
        db_path=config.db_path,
        proxy_port=available_port,
        proxy_mode=config.proxy_mode,
    )

    state_file = config.db_path.parent / ".episodic_db_state.json"
    state = {
        "proxy_port": available_port,
        "proxy_mode": config.proxy_mode,
        "db_path": str(config.db_path),
        "settings_path": str(settings_path),
        "pid": os.getpid(),
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)

    click.echo(f"Episodic DB started:")
    click.echo(f"  Database: {config.db_path}")
    click.echo(f"  Proxy port: {available_port} ({config.proxy_mode})")
    click.echo(f"  Settings: {settings_path}")
    click.echo()

    if config.proxy_mode == "bedrock":
        click.echo(f"  Set: ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:{available_port}")
    else:
        click.echo(f"  Set: ANTHROPIC_BASE_URL=http://127.0.0.1:{available_port}")

    click.echo()
    click.echo("Starting proxy server (Ctrl+C to stop)...")

    async def run_proxy():
        if config.proxy_mode == "bedrock":
            from episodic_db.proxy.bedrock import BedrockProxyServer
            server = BedrockProxyServer(db=db, port=available_port)
        else:
            from episodic_db.proxy.server import ProxyServer
            server = ProxyServer(db=db, port=available_port)

        runner = await server.start()
        click.echo(f"Proxy running on http://127.0.0.1:{server.port}")

        stop_event = asyncio.Event()

        def _signal_handler():
            stop_event.set()

        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)

        await stop_event.wait()
        await runner.cleanup()

    try:
        asyncio.run(run_proxy())
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
        if state_file.exists():
            state_file.unlink()
        click.echo("\nProxy stopped.")


@cli.command()
@click.pass_context
def stop(ctx):
    """Stop the running proxy."""
    config: Config = ctx.obj["config"]
    state_file = config.db_path.parent / ".episodic_db_state.json"

    if not state_file.exists():
        click.echo("No running instance found.")
        return

    with open(state_file) as f:
        state = json.load(f)

    pid = state.get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            click.echo(f"Sent SIGTERM to PID {pid}")
        except ProcessLookupError:
            click.echo("Process not found (already stopped).")

    state_file.unlink(missing_ok=True)


@cli.command()
@click.pass_context
def status(ctx):
    """Show running state and DB stats."""
    config: Config = ctx.obj["config"]

    if not config.db_path.exists():
        click.echo("Database not found. Run 'episodic-db start' first.")
        return

    db = Database(config.db_path)
    db.connect()

    cur = db.conn.execute("SELECT COUNT(*) as cnt FROM sessions")
    sessions = cur.fetchone()["cnt"]

    cur = db.conn.execute("SELECT COUNT(*) as cnt FROM tool_calls")
    calls = cur.fetchone()["cnt"]

    cur = db.conn.execute("SELECT COUNT(*) as cnt FROM episodes")
    episodes = cur.fetchone()["cnt"]

    cur = db.conn.execute("SELECT COUNT(*) as cnt FROM episodes WHERE is_wasteful = 1")
    wasteful = cur.fetchone()["cnt"]

    click.echo(f"Episodic DB Status:")
    click.echo(f"  Database: {config.db_path}")
    click.echo(f"  Sessions: {sessions}")
    click.echo(f"  Tool calls: {calls}")
    click.echo(f"  Episodes: {episodes} ({wasteful} wasteful)")

    state_file = config.db_path.parent / ".episodic_db_state.json"
    if state_file.exists():
        with open(state_file) as f:
            state = json.load(f)
        click.echo(f"  Proxy: running on port {state.get('proxy_port')} (PID {state.get('pid')})")
    else:
        click.echo("  Proxy: not running")

    db.close()


@cli.command()
@click.option("--session", "session_id", help="Embed episodes for specific session")
@click.option("--all", "all_episodes", is_flag=True, help="Embed all un-embedded episodes")
@click.pass_context
def embed(ctx, session_id, all_episodes):
    """Generate embeddings for episodes."""
    config: Config = ctx.obj["config"]

    if not config.db_path.exists():
        click.echo("Database not found.", err=True)
        sys.exit(1)

    db = Database(config.db_path)
    db.connect()

    from episodic_db.embedding.indexer import EpisodeIndexer

    indexer = EpisodeIndexer(db, config.embedding)

    if session_id:
        indexer.embed_episodes(session_id=session_id)
        click.echo(f"Embedded episodes for session {session_id}")
    elif all_episodes:
        indexer.embed_episodes()
        click.echo("Embedded all un-embedded episodes")
    else:
        click.echo("Specify --session ID or --all")

    db.close()


@cli.command()
@click.option("--path-prefix", help="Filter by path prefix")
@click.option("--waste-type", help="Filter by waste type")
@click.option("--outcome", help="Filter by outcome")
@click.option("--lang", help="Filter by language")
@click.option("--similar", "similar_text", help="Vector similarity search text")
@click.option("--limit", default=10, help="Max results")
@click.pass_context
def query(ctx, path_prefix, waste_type, outcome, lang, similar_text, limit):
    """Search episodes by facets or vector similarity."""
    config: Config = ctx.obj["config"]

    if not config.db_path.exists():
        click.echo("Database not found.", err=True)
        sys.exit(1)

    db = Database(config.db_path)
    db.connect()

    if similar_text:
        from episodic_db.query.vector_search import search_similar
        results = search_similar(
            db, similar_text, config.embedding,
            limit=limit, path_prefix=path_prefix,
            waste_type=waste_type, lang=lang,
        )
    else:
        from episodic_db.query.facet_search import search_episodes
        results = search_episodes(
            db, path_prefix=path_prefix, waste_type=waste_type,
            outcome=outcome, lang=lang, limit=limit,
        )

    if not results:
        click.echo("No episodes found.")
    else:
        click.echo(f"Found {len(results)} episode(s):\n")
        for ep in results:
            click.echo(f"  [{ep.get('episode_id', 'N/A')}] {ep.get('waste_type', '?')} | {ep.get('outcome', '?')}")
            click.echo(f"    path: {ep.get('path_prefix', '-')}  lang: {ep.get('lang', '-')}  cost: ${ep.get('total_cost', 0):.4f}")
            click.echo()

    db.close()


@cli.command()
@click.argument("session_id")
@click.pass_context
def inspect(ctx, session_id):
    """Inspect a session's graph (nodes, edges, episodes)."""
    config: Config = ctx.obj["config"]

    if not config.db_path.exists():
        click.echo("Database not found.", err=True)
        sys.exit(1)

    db = Database(config.db_path)
    db.connect()

    from episodic_db.store.nodes import get_session, get_session_tool_calls, get_episodes_by_session

    session = get_session(db.conn, session_id)
    if not session:
        click.echo(f"Session {session_id} not found.")
        db.close()
        return

    click.echo(f"Session: {session_id}")
    click.echo(f"  Started: {session.get('started_at')}")
    click.echo(f"  Ended: {session.get('ended_at', 'still running')}")
    click.echo(f"  Success: {session.get('success')}")
    click.echo(f"  Total cost: ${session.get('total_cost', 0):.4f}")
    click.echo()

    tool_calls = get_session_tool_calls(db.conn, session_id)
    click.echo(f"Tool Calls ({len(tool_calls)}):")
    for tc in tool_calls[:50]:
        marker = "+" if tc.get("contributed_to") == "CONTRIBUTED" else "-" if tc.get("contributed_to") == "DID_NOT" else "?"
        click.echo(f"  [{marker}] #{tc['seq']:03d} {tc['tool_name']}: {tc.get('normalized_input', '')[:60]}")

    if len(tool_calls) > 50:
        click.echo(f"  ... and {len(tool_calls) - 50} more")

    click.echo()
    episodes = get_episodes_by_session(db.conn, session_id)
    click.echo(f"Episodes ({len(episodes)}):")
    for ep in episodes:
        click.echo(f"  [{ep['episode_id']}] {ep.get('waste_type', '?')} | {ep.get('outcome', '?')} | cost=${ep.get('total_cost', 0):.4f}")

    db.close()


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
