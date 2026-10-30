"""Server side of figure export: embed the resolution in PNG and TIFF files.

The browser renders the figure at the requested size and posts the PNG here;
Pillow writes it back as PNG or TIFF with the dpi stored in the file, which
Word, Illustrator and journal systems read.
"""
from __future__ import annotations

from io import BytesIO

from flask import Response, abort, request

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MIN_DPI, MAX_DPI = 72, 1200
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def register_export_routes(app) -> None:
    @app.server.route("/export/image", methods=["POST"])
    def export_image():
        fmt = request.args.get("format", "png").lower()
        if fmt not in {"png", "tiff"}:
            abort(400, "format must be png or tiff")
        try:
            dpi = int(float(request.args.get("dpi", "300")))
        except ValueError:
            abort(400, "dpi must be a number")
        dpi = max(MIN_DPI, min(MAX_DPI, dpi))

        if request.content_length is not None and request.content_length > MAX_UPLOAD_BYTES:
            abort(413, "image too large")
        payload = request.get_data(cache=False)
        if len(payload) > MAX_UPLOAD_BYTES or not payload.startswith(PNG_SIGNATURE):
            abort(400, "expected a PNG image")

        try:
            from PIL import Image
        except ImportError:
            abort(501, "Pillow is not installed on the server")

        image = Image.open(BytesIO(payload))
        image.load()
        out = BytesIO()
        if fmt == "tiff":
            image.save(out, format="TIFF", dpi=(dpi, dpi), compression="tiff_lzw")
            mimetype = "image/tiff"
        else:
            image.save(out, format="PNG", dpi=(dpi, dpi))
            mimetype = "image/png"
        return Response(
            out.getvalue(),
            mimetype=mimetype,
            headers={"Cache-Control": "no-store"},
        )
