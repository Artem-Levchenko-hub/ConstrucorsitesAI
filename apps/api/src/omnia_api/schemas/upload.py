"""Request schemas for the in-preview manual edits (no LLM).

* ImagePatchRequest — user clicks an ``<img>`` and uploads their own picture;
  the frontend sends the image's current ``src`` and the uploaded asset URL.
* TextPatchRequest — user edits a text element's content directly; the frontend
  sends the element's current text, the new text, and the occurrence index (so
  repeated labels like a "Заказать" button hit the right one).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImagePatchRequest(BaseModel):
    old_src: str = Field(min_length=1, max_length=2000)
    new_src: str = Field(min_length=1, max_length=2000)


class TextPatchRequest(BaseModel):
    old_text: str = Field(min_length=1, max_length=5000)
    new_text: str = Field(min_length=0, max_length=5000)
    # 0-based index of the occurrence to replace among identical pure-text
    # elements (document order). Disambiguates repeated labels.
    index: int = Field(default=0, ge=0, le=2000)
