"""CLI entrypoint."""

import typer

app = typer.Typer(help="Terminal-first YouTube search and download workflow.")


@app.command()
def doctor() -> None:
    """Temporary bootstrap command."""
    typer.echo("Bootstrap in progress.")


def main() -> None:
    """Run the CLI app."""
    app()

