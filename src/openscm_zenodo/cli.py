"""
Command line interface(s)
"""
import logging

import click


"str: Default format used for logging output"
DEFAULT_LOG_FORMAT = "{process} {asctime} {levelname}:{name}:{message}"


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
        formatted_message = super(ColorFormatter, self).format(record)

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

        except Exception:  # pragma: no cover
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
