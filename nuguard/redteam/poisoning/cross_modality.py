"""CrossModalitySmuggling — embeds text payloads into non-text data formats.

Content safety classifiers typically operate on text.  By embedding instructions
inside image metadata (EXIF), filenames, or other non-text surfaces, attackers can
smuggle payloads into multimodal AI systems that process file uploads.

The embedded instruction may be executed when:
- A multimodal model reads the file's metadata during indexing.
- A tool extracts metadata and passes it to an LLM as context.
- An OCR or EXIF-reader tool returns the metadata to the agent.

All methods are pure Python — no external image-processing libraries needed.
The EXIF embedding uses the ``struct`` standard library module for raw byte
manipulation.
"""
from __future__ import annotations

import struct
import urllib.parse

# JPEG magic bytes
_JPEG_SOI = b"\xff\xd8"
# EXIF marker
_JPEG_APP1 = b"\xff\xe1"
# EXIF header
_EXIF_HEADER = b"Exif\x00\x00"
# TIFF little-endian byte order mark
_TIFF_LE = b"II"
# TIFF magic number
_TIFF_MAGIC = b"\x2a\x00"
# User comment EXIF tag (0x9286)
_TAG_USER_COMMENT = 0x9286


class CrossModalitySmuggling:
    """Embeds text payloads into binary/filename surfaces to bypass text classifiers."""

    def embed_in_exif(self, text_payload: str) -> bytes:
        """Create a minimal valid JPEG file with an EXIF UserComment containing the payload.

        The resulting bytes can be saved as a ``.jpg`` file and uploaded to a
        system that processes image metadata.

        The EXIF structure is hand-crafted using ``struct`` — no Pillow/exifread
        dependency needed.

        Parameters
        ----------
        text_payload:
            The text instruction to embed in the UserComment EXIF field.

        Returns
        -------
        Raw bytes of a minimal JPEG with embedded EXIF payload.
        """
        payload_bytes = text_payload.encode("utf-8")

        # UserComment value: 8-byte charset identifier + payload
        # "UNICODE\x00" is the standard charset prefix for Unicode user comments
        user_comment_data = b"UNICODE\x00" + payload_bytes
        value_len = len(user_comment_data)

        # Build a minimal TIFF/IFD with one tag (UserComment)
        # IFD entry format: tag(2) + type(2) + count(4) + value_offset(4)
        # We use type=1 (BYTE), count=len, offset will point past the IFD
        n_entries = 1
        # IFD starts at offset 8 (after "II" + magic + IFD offset)
        ifd_offset = 8
        ifd_size = 2 + n_entries * 12 + 4  # entry_count + entries + next_ifd_ptr
        value_area_offset = ifd_offset + ifd_size

        ifd_entry = struct.pack(
            "<HHII",
            _TAG_USER_COMMENT,   # tag
            1,                    # type = BYTE
            value_len,            # count
            value_area_offset,    # value offset (from start of TIFF header)
        )

        tiff_header = (
            _TIFF_LE
            + _TIFF_MAGIC
            + struct.pack("<I", ifd_offset)  # IFD offset from start of TIFF
        )
        ifd = (
            struct.pack("<H", n_entries)
            + ifd_entry
            + struct.pack("<I", 0)   # next IFD offset = 0 (no more IFDs)
        )

        exif_body = _EXIF_HEADER + tiff_header + ifd + user_comment_data
        app1_payload = struct.pack(">H", len(exif_body) + 2) + exif_body

        # Build minimal JPEG: SOI + APP1 + minimal EOI
        # (Browsers/tools will accept this as a valid (if tiny) JPEG)
        jpeg_bytes = (
            _JPEG_SOI
            + _JPEG_APP1
            + app1_payload
            + b"\xff\xd9"   # EOI marker
        )
        return jpeg_bytes

    def embed_in_filename(self, text_payload: str, extension: str = ".txt") -> str:
        """Encode a payload into a filename using URL-percent-encoding.

        Many file-processing systems pass filenames to LLMs as context (e.g.
        "You are processing a file named X").  Encoding the payload in the
        filename causes the LLM to receive the instruction as part of its context.

        Parameters
        ----------
        text_payload:
            The text instruction to embed.
        extension:
            File extension to append (default ``.txt``).

        Returns
        -------
        A filename string with the payload URL-encoded in the base name.
        The filename looks like a plausible document name to casual inspection.
        """
        # URL-encode the payload to make it look like a file hash/reference
        encoded = urllib.parse.quote(text_payload, safe="")
        # Truncate to a reasonable filename length (255 bytes is typical FS limit)
        max_base = 200 - len(extension)
        if len(encoded) > max_base:
            encoded = encoded[:max_base]
        return f"document_{encoded}{extension}"

    def extract_from_filename(self, filename: str) -> str | None:
        """Reverse ``embed_in_filename`` to recover the embedded payload.

        Parameters
        ----------
        filename:
            The filename produced by ``embed_in_filename``.

        Returns
        -------
        Decoded payload string, or ``None`` if the filename doesn't match
        the expected pattern.
        """
        import re
        m = re.match(r"^document_(.+)\.[^.]+$", filename)
        if not m:
            return None
        try:
            return urllib.parse.unquote(m.group(1))
        except Exception:
            return None
