import click
from g4a import __version__
from g4a.cli.commands.init import init
from g4a.cli.commands.capture import capture
from g4a.cli.commands.log import log_cmd
from g4a.cli.commands.why import why


@click.group()
@click.version_option(__version__, prog_name="g4a")
def cli():
    """g4a - the reasoning layer for AI-written code."""
    pass


cli.add_command(init)
cli.add_command(capture)
cli.add_command(log_cmd)
cli.add_command(why)
