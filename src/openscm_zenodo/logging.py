"""
Logging
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any, Optional, Union

from loguru import logger
from typing_extensions import TypeAlias

LoggingConfigType: TypeAlias = Union[dict[str, list[dict[str, Any]]], None]


def get_default_config(
    level: str = "INFO",
) -> dict[str, list[dict[str, Union[Union[io.TextIOWrapper, Any], str, bool]]]]:
    """
    Get default logging configuration

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
                sink=sys.stderr,
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
    logging_config: Optional[Union[Path, LoggingConfigType]] = None,
    logging_level: Optional[str] = None,
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
        # mypy not happy about kwargs being passed here,
        # fair enough I guess
        logger.configure(**logging_config)  # type: ignore

    else:
        # Type ignore while we wait for new release of loguru-config
        try:
            from loguru_config import LoguruConfig  # type: ignore
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
        loguru_configurer.load()

    logger.enable("openscm_zenodo")

    if logging_config is not None and logging_level is not None:
        logger.warning("`logging_level` is ignored if `logging_config` is supplied")


def mask_token(input: str, token: Union[str, None]) -> str:
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
