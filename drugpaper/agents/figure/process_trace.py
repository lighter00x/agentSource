"""Persist exact image inputs for process replay without storing base64 in trace records."""

from __future__ import annotations

import base64
import hashlib
from typing import Any


async def image_references(store: Any, images: list, cache: dict | None = None) -> list[dict]:
    cache = cache if cache is not None else {}
    result = []
    for image in images:
        ref = image.blob_ref
        if not ref and image.data_b64:
            key = hashlib.sha256(image.data_b64.encode()).hexdigest()
            ref = cache.get(key)
            if ref is None:
                ref = await store.put(base64.b64decode(image.data_b64), image.mime)
                cache[key] = ref
        if ref:
            result.append({"blob_ref": ref, "mime": image.mime})
    return result
