import logging
import re
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)

# Credentials must never reach the logs, and access logs print full URLs. The
# live-updates stream now opens with a short-lived one-time ?ticket= (the browser's
# EventSource cannot send headers), and older clients or scripts may still put an
# access token in the query string: redact all of them.
_TOKEN_IN_URL = re.compile(r"([?&](?:token|access_token|ticket)=)[^&\s\"]+")


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
