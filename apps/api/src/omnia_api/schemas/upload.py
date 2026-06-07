"""Request schema for the in-preview image-replacement endpoint.

The user clicks an ``<img>`` in the preview and uploads their own picture; the
frontend sends the image's CURRENT ``src`` (``old_src``) and the uploaded asset
URL (``new_src``). The router swaps the former for the latter in index.html and
commits a snapshot — no LLM.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImagePatchRequest(BaseModel):
    old_src: str = Field(min_length=1, max_length=2000)
    new_src: str = Field(min_length=1, max_length=2000)
