"""`saley chat forget` and `saley bot status` (R7, D262, docs/17 §10).

Why a module of its own: cli.py stays under the 800-line ceiling.
"""

from __future__ import annotations

from typing import Annotated

import typer

chat_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Chat memory of the bot (D262).",
)


@chat_app.command("forget")
def chat_forget(
    message_id: Annotated[int, typer.Argument(help="The Discord message id to remove.")],
) -> None:
    """Remove one stored chat line and the facts drawn from it (on request, docs/17 §7)."""
    from saley.bot import memory
    from saley.core.db import session_scope

    with session_scope() as session:
        gone = memory.forget(session, message_id)
    typer.echo("forgotten: 1 line" if gone else "no such line")
    raise typer.Exit(code=0 if gone else 1)


bot_app = typer.Typer(
    invoke_without_command=True,
    pretty_exceptions_show_locals=False,
    help="The Discord bot (no subcommand), and its status.",
)


@bot_app.command("status")
def bot_status() -> None:
    """The Discord numbers of today (the Sources & health block) and the bot heartbeat."""
    from saley.core import alive
    from saley.core.db import rw_engine
    from saley.web.notes_health import discord_state

    # The bot's own login (the bot container has no read-only URL, docs/17 §3).
    with rw_engine().connect() as conn:
        rows = discord_state(conn)["rows"]
    for row in rows:
        typer.echo(f"{row['label']}: {row['value']}")
    age = alive.age("bot")
    typer.echo(
        f"Bot heartbeat: {f'{age:.0f} s ago' if age is not None else 'none on this machine'}"
    )


def register(app: typer.Typer) -> None:
    app.add_typer(chat_app, name="chat")
    app.add_typer(bot_app, name="bot")
