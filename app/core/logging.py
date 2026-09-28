import logging
import re
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)

# The live-updates stream authenticates with ?token=<access token> because the
# browser's EventSource cannot send headers. Access logs print the full URL, so
# without this every open tab would write a working session token to the logs.
_TOKEN_IN_URL = re.compile(r"([?&](?:token|access_token)=)[^&\s\"]+")


class RedactTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_IN_URL.sub(r"\1[redacted]", a) if isinstance(a, str) else a for a in record.args
            )
        elif isinstance(record.msg, str):
            record.msg = _TOKEN_IN_URL.sub(r"\1[redacted]", record.msg)
        return True


logging.getLogger("uvicorn.access").addFilter(RedactTokens())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
