"""Blob 代理：`GET /blobs/{ref}`（BlobStore.url 返回的地址，不暴露本地路径）。"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from drugpaper.api.deps import ServicesDep
from drugpaper.api.errors import ApiError, ErrorResponse
from drugpaper.storage.base import BlobNotFound, is_valid_ref, sniff_content_type

router = APIRouter(tags=["blobs"], responses={404: {"model": ErrorResponse}})


@router.get("/blobs/{ref}", response_class=Response)
async def get_blob(sv: ServicesDep, ref: str) -> Response:
    if not is_valid_ref(ref):
        raise ApiError(400, "BAD_REF", "blob_ref 必须是 64 位十六进制 sha256")
    try:
        data = await sv.store.get(ref)
    except BlobNotFound as e:
        raise ApiError(404, "NOT_FOUND", f"blob 不存在：{ref}") from e
    return Response(
        data,
        media_type=sniff_content_type(data),
        headers={"Cache-Control": "private, max-age=31536000, immutable", "ETag": f'"{ref}"'},
    )
