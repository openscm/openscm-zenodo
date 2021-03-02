"""
Command line interface(s)
"""
import logging

import click

from .uploading import upload_file
from .zenodo import get_bucket_id

DEFAULT_LOG_FORMAT = "{process} {asctime} {levelname}:{name}:{message}"
"""str: Default format used for logging output"""


class ColorFormatter(logging.Formatter):
    """
    Colour formatter for log messages

    A handy little tool for making our log messages look slightly prettier
    """

    colors = {
        "DEBUG": dict(fg="blue"),
        "INFO": dict(fg="green"),
        "WARNING": dict(fg="yellow"),
        "ERROR": dict(fg="red"),
        "EXCEPTION": dict(fg="red"),
        "CRITICAL": dict(fg="red"),
    }

    def format(self, record):
        """
        Format a record so it has pretty colours

        Parameters
        ----------
        record : :obj:`logging.LogRecord`
            Record to format

        Returns
        -------
        str
            Formatted message string
        """
        formatted_message = super().format(record)

        if not record.exc_info:
            level = record.levelname.upper()

            if level in self.colors:
                level_colour = click.style("{}".format(level), **self.colors[level])
                formatted_message = formatted_message.replace(level, level_colour)

        return formatted_message


class ClickHandler(logging.Handler):
    """
    Handler which emits using click when going to stdout
    """

    _use_stderr = True

    def emit(self, record):
        """
        Emit a record

        Parameters
        ----------
        record : :obj:`logging.LogRecord`
            Record to emit
        """
        try:
            msg = self.format(record)
            click.echo(msg, err=self._use_stderr)

        except Exception:  # pragma: no cover # pylint: disable=broad-except
            self.handleError(record)


_default_handler = ClickHandler()
_default_handler.formatter = ColorFormatter(DEFAULT_LOG_FORMAT, style="{")


@click.group(name="openscm-zenodo")
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "EXCEPTION", "CRITICAL"]),
)
def cli(log_level):
    """
    OpenSCM-Zenodo's command-line interface
    """
    netcdf_scm_logger = logging.getLogger("openscm_zenodo")
    netcdf_scm_logger.handlers.append(_default_handler)
    netcdf_scm_logger.setLevel(log_level)

    logging.captureWarnings(True)


_zenodo_url = click.option(
    "--zenodo-url",
    default="sandbox.zenodo.org",
    type=click.Choice(["sandbox.zenodo.org", "zenodo.org"]),
    show_default=True,
    help="Zenodo server to which to upload.",
)
_token = click.option('--token', envvar='ZENODO_TOKEN')


@cli.command(context_settings={"help_option_names": ["-h", "--help"]})
def create_new_version():
    r"""
    Create new version of a Zenodo record (i.e. a specific Zenodo deposition ID)
    """
    pass


@cli.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("file_to_upload", type=click.Path(exists=True, readable=True, dir_okay=False, resolve_path=True))
@click.argument("bucket")
@_zenodo_url
@_token
def upload(file_to_upload, bucket, zenodo_url, token):
    r"""
    Upload a file to a Zenodo bucket

    ``file_to_upload`` will be uploaded to the Zenodo bucket specified by
    ``bucket``.
    """
    # TODO: test this function
    upload_file(filepath=file_to_upload, bucket=bucket, zenodo_url=zenodo_url, token=token)


@cli.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("deposition_id")
@_zenodo_url
@_token
def get_bucket(deposition_id, zenodo_url, token):
    r"""
    Get the bucket associated with a given Zenodo deposition ID (``deposition_id``)

    The ``upload`` command can then be used to upload files to this bucket.

    This command is handy when you know your deposition ID but you've
    forgotten the bucket address.
    """
    bucket_id = get_bucket_id(deposition_id=deposition_id, zenodo_url=zenodo_url, token=token)

    click.echo(bucket_id)
