"""
Logging

## What the levels are for

`INFO` and `DEBUG` have different audiences, and that is the distinction which
governs everything else here.

- **`INFO` is user interface.** It is the narrative of what happened, for
  somebody watching a command run. This is what `-v` / `-q` on the command line
  adjust, see
  [`get_level_from_verbosity`][openscm_zenodo.logging.get_level_from_verbosity].
- **`DEBUG` is diagnostics.** Request URLs, the plan a mirror worked out, MD5
  timings, expected misses — the things you want when working out why something
  behaved the way it did.

Messages follow one rule: **a function may only log what it knows.** It knows
what it *did*; it does not know why it was called. So `INFO` states outcomes as
facts ("Uploaded 2 file(s) to record '1234'"), and anything not yet true —
intentions, "about to do X" — goes to `DEBUG`. This is not a style preference:
a function cannot know how deeply it is nested, so a message which announces an
intention is a claim that some *other* caller may immediately contradict. Facts
compose safely at any depth; intentions do not.
`tests/test_log_messages.py` enforces it.

## Why the messages are free-form rather than structured

Structured logging (fields a machine can query, rather than a sentence) is the
better shape once something aggregates logs across many runs. We do not do it
yet, deliberately:

- **Field names are a public interface with no deprecation path.** The moment
  somebody greps `record_id` in their pipeline, we own that name, and we cannot
  know whether they want `record_id`, `recordId` or `zenodo.record.id` until such
  a consumer exists. Today the only consumer we know of is our own command-line
  interface, whose audience is a human.
- **Waiting costs nothing, because loguru separates the record from its
  rendering.** `logger.bind(record_id=...)` attaches fields to the same call that
  produces the sentence; a `serialize=True` sink then emits JSON while a normal
  sink keeps printing text. Adding structure later is additive — no caller
  changes, no migration, and the "structured logs are hard to read" problem is
  the sink's to solve, not ours. So there is no first-mover advantage to guessing
  a schema now.
- **The trigger to revisit is a real request**, i.e. somebody asking how to get
  these into their log aggregator. That request also tells us the field names,
  which is the piece we are missing.

## Why the library logs at all

The rule libraries follow is *do not configure logging, do emit it*: the
application decides where records go. We comply — `openscm_zenodo` disables its
own logger on import and
[`setup_logging`][openscm_zenodo.logging.setup_logging] is opt-in, which the CLI
calls and a library caller need not. That is what makes emitting these messages
harmless, and it is why they are not simply removed.

One consequence worth knowing: we log through **loguru**, which is a global
singleton. An application built on the standard library's `logging` needs an
interception handler to capture our records at all. If this package ends up
embedded in applications with real log pipelines, that — not the free-form
messages — is the thing to revisit, and it is a reason not to deepen the loguru
investment with a bespoke structured-field layer in the meantime.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from loguru import logger

from openscm_zenodo.progress import tqdm_write_sink

if TYPE_CHECKING:
    from loguru import HandlerConfig


class ConfigLike(TypedDict):
    """
    Configuration-like to use with loguru
    """

    handlers: list[HandlerConfig]


VERBOSITY_LEVELS: tuple[str, ...] = (
    "CRITICAL",
    "ERROR",
    "WARNING",
    "INFO",
    "DEBUG",
    "TRACE",
)
"""
Levels which verbosity steps through, quietest first

`-q` steps towards the front, `-v` towards the back, from
[`VERBOSITY_LEVEL_DEFAULT`][openscm_zenodo.logging.VERBOSITY_LEVEL_DEFAULT].
"""

VERBOSITY_LEVEL_DEFAULT = "INFO"
"""
Level used when neither `-v` nor `-q` is given

`INFO` is the narrative of what happened, which is what somebody running a
command wants to see, see this module's docstring.
"""


def get_level_from_verbosity(verbose: int = 0, quiet: int = 0) -> str:
    """
    Work out a logging level from how many times `-v` and `-q` were given

    Parameters
    ----------
    verbose
        Number of times `-v` was given, each step showing more

    quiet
        Number of times `-q` was given, each step showing less

    Returns
    -------
    :
        Level to log at, one of
        [`VERBOSITY_LEVELS`][openscm_zenodo.logging.VERBOSITY_LEVELS]

    Examples
    --------
    >>> get_level_from_verbosity()
    'INFO'
    >>> get_level_from_verbosity(verbose=1)
    'DEBUG'
    >>> get_level_from_verbosity(quiet=1)
    'WARNING'

    Asking for more than there is gets you all there is,
    rather than an error about a flag which can only have been meant kindly

    >>> get_level_from_verbosity(verbose=10)
    'TRACE'
    """
    default = VERBOSITY_LEVELS.index(VERBOSITY_LEVEL_DEFAULT)
    step = default + verbose - quiet

    return VERBOSITY_LEVELS[max(0, min(step, len(VERBOSITY_LEVELS) - 1))]


def get_default_config(
    level: str = "INFO",
) -> ConfigLike:
    """
    Get default logging configuration

    By default, we write via `tqdm` so that log lines
    do not cause issues with progress bars.

    Parameters
    ----------
    level
        Level to apply to the logging

    Returns
    -------
    :
        Default logging configuration
    """
    return dict(
        handlers=[
            dict(
                sink=tqdm_write_sink,
                level=level,
                colorize=True,
                format=" - ".join(
                    [
                        "{process}",
                        "{thread}",
                        "<green>{time:!UTC}</>",
                        "<level>{level}</>",
                        "<cyan>{name}:{file}:{line}</>",
                        "<level>{message}</>",
                    ]
                ),
            )
        ],
    )


def setup_logging(
    enable: bool,
    logging_config: Path | ConfigLike | None = None,
    logging_level: str | None = None,
) -> None:
    """
    Set up logging

    Parameters
    ----------
    enable
        Whether to enable the logger.

        If `False`, we explicitly disable logging,
        ignoring the value of all other arguments.

    logging_config
        If a `dict`, passed to
        [`loguru.logger.configure`][loguru._logger.Logger.configure].
        If not passed, [`get_default_config`][openscm_zenodo.logging.get_default_config]
        is used.
        Otherwise, we try and load this from disk using
        [`loguru_config.LoguruConfig`](https://github.com/erezinman/loguru-config).

        This takes precedence over `log_level`.

    logging_level
        Log level to apply to the default config.
    """
    if not enable:
        # Should already be disabled, but just in case
        logger.disable("openscm_zenodo")
        return

    if logging_config is None:
        if logging_level is not None:
            config = get_default_config(level=logging_level)
        else:
            config = get_default_config()

        # Not sure what is going on with type hints, one for another day
        logger.configure(handlers=config["handlers"])

    elif isinstance(logging_config, dict):
        logger.configure(**logging_config)

    else:
        # Type ignore while we wait for new release of loguru-config
        try:
            from loguru_config import LoguruConfig  # type: ignore # noqa: PLC0415
        except ImportError:
            msg = (
                "[loguru-config](https://github.com/erezinman/loguru-config) "
                "is required to load config from disk. "
                "Run `pip install loguru-config`."
                "If that doesn't work, see installation instructions here: "
                "https://github.com/erezinman/loguru-config#installation"
            )
            print(msg)

            raise

        loguru_configurer = LoguruConfig.load(logging_config, configure=False)
        loguru_configurer.parse().configure()

    logger.enable("openscm_zenodo")

    if logging_config is not None and logging_level is not None:
        logger.warning("`logging_level` is ignored if `logging_config` is supplied")


def mask_token(input: str, token: str | None) -> str:
    """
    Mask any token values in `input`

    Parameters
    ----------
    input
        Value in which the token values should be masked

    token
        Token value to mask

        If not supplied, this function becomes a no-op.

    Returns
    -------
    :
        `input` with `token` masked
    """
    if token is None:
        return input

    return input.replace(token, "***")
