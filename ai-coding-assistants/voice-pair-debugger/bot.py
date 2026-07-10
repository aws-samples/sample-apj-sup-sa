"""Voice: voice pair debugger for AWS.

Run: uv run bot.py
Then open http://localhost:7860/client in your browser.
"""

import sys

from loguru import logger
from uvicorn.config import LOGGING_CONFIG

from voice.config import LOG_LEVEL

# Pipecat's dev runner calls logger.add(sys.stderr, level="DEBUG") inside its
# main(), overriding any level we set and flooding the console. Wrap logger.add
# so the console sink is always clamped to LOG_LEVEL, whoever adds it and
# whenever. Installed before importing Pipecat so import-time logs are caught.
_original_logger_add = logger.add


def _clamped_add(sink, *args, **kwargs):
    if sink is sys.stderr or sink is sys.stdout:
        kwargs["level"] = LOG_LEVEL
    return _original_logger_add(sink, *args, **kwargs)


logger.add = _clamped_add

logger.remove()
logger.add(sys.stderr, level=LOG_LEVEL)

# Quiet uvicorn's startup banner and per-request access logs. uvicorn re-applies
# its own logging config when the server starts, so set the levels on the config
# dict uvicorn.run reads, not on the loggers (which would be overwritten).
_uvicorn_level = LOG_LEVEL if LOG_LEVEL in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "WARNING"
for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    LOGGING_CONFIG["loggers"][_name]["level"] = _uvicorn_level

from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import TransportParams

from voice.pipeline import run_bot

transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
