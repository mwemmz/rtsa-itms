"""Minimal S3-compatible object storage client (AWS Signature Version 4).

Enough for off-site backups: put, get, head, list and delete. Works with any
S3-compatible service - Amazon S3, Cloudflare R2, Backblaze B2, MinIO - using
path-style URLs (``{endpoint}/{bucket}/{key}``). Built on httpx, which the
project already uses, rather than pulling in boto3 for five calls.
"""

import hashlib
import hmac
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlsplit

import httpx

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign_v4(
    method: str,
    url: str,
    headers: dict[str, str],
    payload_hash: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    now: datetime,
) -> dict[str, str]:
    """Return ``headers`` plus ``x-amz-date`` and ``Authorization`` for the request.

    Every header passed in is signed. ``payload_hash`` is the hex SHA-256 of the
    body (S3 also needs it sent as ``x-amz-content-sha256``; add it to ``headers``).
    """
    parts = urlsplit(url)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    all_headers = {**headers, "host": parts.netloc, "x-amz-date": amz_date}
    canonical_headers = {k.lower().strip(): " ".join(str(v).split()) for k, v in all_headers.items()}
    signed = ";".join(sorted(canonical_headers))
    query = sorted(
        (quote(unquote(k), safe="-_.~"), quote(unquote(v), safe="-_.~"))
        for k, _, v in (pair.partition("=") for pair in parts.query.split("&") if pair)
    )
    canonical_request = "\n".join([
        method.upper(),
        quote(parts.path or "/", safe="/-_.~%"),
        "&".join(f"{k}={v}" for k, v in query),
        "".join(f"{k}:{canonical_headers[k]}\n" for k in sorted(canonical_headers)),
        signed,
        payload_hash,
    ])
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    key = _hmac(("AWS4" + secret_key).encode("utf-8"), datestamp)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out = {k: v for k, v in all_headers.items() if k != "host"}
    out["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"
    )
    return out


@dataclass
class StoredObject:
    key: str
    size: int
    last_modified: str


class S3Error(RuntimeError):
    pass


class S3Client:
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str, region: str = "us-east-1",
                 timeout: float = 60.0, transport: httpx.BaseTransport | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def _url(self, key: str = "") -> str:
        return f"{self.endpoint}/{quote(self.bucket)}/{quote(key, safe='/-_.~')}" if key else \
            f"{self.endpoint}/{quote(self.bucket)}"

    def _request(self, method: str, url: str, body: bytes = b"", params: dict[str, str] | None = None) -> httpx.Response:
        if params:
            url += "?" + "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in params.items())
        payload_hash = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
        headers = sign_v4(method, url, {"x-amz-content-sha256": payload_hash}, payload_hash,
                          self.access_key, self.secret_key, self.region, "s3", datetime.now(timezone.utc))
        resp = self._http.request(method, url, content=body or None, headers=headers)
        return resp

    def _check(self, resp: httpx.Response, action: str) -> httpx.Response:
        if resp.status_code >= 300:
            code = ""
            try:
                code = ET.fromstring(resp.content).findtext("Code") or ""
            except ET.ParseError:
                pass
            raise S3Error(f"{action} failed: HTTP {resp.status_code} {code}".strip())
        return resp

    def put_object(self, key: str, data: bytes) -> None:
        self._check(self._request("PUT", self._url(key), data), f"upload of {key}")

    def get_object(self, key: str) -> bytes:
        return self._check(self._request("GET", self._url(key)), f"download of {key}").content

    def head_object(self, key: str) -> int | None:
        """Size in bytes, or None if the object doesn't exist."""
        resp = self._request("HEAD", self._url(key))
        if resp.status_code == 404:
            return None
        self._check(resp, f"lookup of {key}")
        return int(resp.headers.get("content-length", 0))

    def delete_object(self, key: str) -> None:
        self._check(self._request("DELETE", self._url(key)), f"delete of {key}")

    def list_objects(self, prefix: str = "") -> list[StoredObject]:
        out: list[StoredObject] = []
        token = None
        while True:
            params = {"list-type": "2", "prefix": prefix}
            if token:
                params["continuation-token"] = token
            root = ET.fromstring(self._check(self._request("GET", self._url(), params=params), "listing").content)
            ns = _S3_NS if root.tag.startswith(_S3_NS) else ""
            for item in root.findall(f"{ns}Contents"):
                out.append(StoredObject(key=item.findtext(f"{ns}Key") or "",
                                        size=int(item.findtext(f"{ns}Size") or 0),
                                        last_modified=item.findtext(f"{ns}LastModified") or ""))
            if (root.findtext(f"{ns}IsTruncated") or "").lower() != "true":
                return out
            token = root.findtext(f"{ns}NextContinuationToken")
