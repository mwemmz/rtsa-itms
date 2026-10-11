"""Photo evidence: what may be stored, and how it is served back.

One rule for every photo, whoever took it: an officer at the roadside or a
citizen reporting a driver. The type is read from the file's first bytes, never
its name or declared type, so nothing but a photo is ever stored or served.
"""

import hashlib

from fastapi import HTTPException, Response, UploadFile

from app.models.enforcement import ViolationPhoto

MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PHOTOS_PER_VIOLATION = 5
PHOTO_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def image_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def read_photo(upload: UploadFile) -> tuple[bytes, str]:
    """The photo's bytes and type, or an HTTP error saying what's wrong with it."""
    data = upload.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="A photo can be up to 5 MB")
    content_type = image_type(data)
    if content_type is None:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG or WebP photos can be attached")
    return data, content_type


def new_photo(data: bytes, content_type: str, uploaded_by, **owner) -> ViolationPhoto:
    """A photo row for ``owner`` (violation_id= or report_id=), fingerprinted now."""
    return ViolationPhoto(
        content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
        uploaded_by=uploaded_by,
        **owner,
    )


def photo_out(photo: ViolationPhoto, uploader: str | None) -> dict:
    return {
        "id": photo.id,
        "violation_id": photo.violation_id,
        "report_id": photo.report_id,
        "content_type": photo.content_type,
        "size_bytes": photo.size_bytes,
        "sha256": photo.sha256,
        "uploaded_by_name": uploader,
        "created_at": photo.created_at,
    }


def photo_response(photo: ViolationPhoto) -> Response:
    return Response(
        photo.data,
        media_type=photo.content_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="evidence-{photo.id}.{PHOTO_EXTENSIONS[photo.content_type]}"',
        },
    )
