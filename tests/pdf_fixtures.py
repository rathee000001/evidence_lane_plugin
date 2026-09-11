"""Synthetic PDF sources with known text, widgets and a scanned second page."""

import base64
import io
from pathlib import Path


def generation(*, mixed=False):
    def element(kind, y, **values):
        return {"kind": kind, "x": 50, "y": y, "width": 450, "height": 40, **values}

    pages = [
        {
            "elements": [
                element("text", 50, text="Native page remains searchable", font_size=20),
                element(
                    "table",
                    130,
                    height=110,
                    rows=[["Measure", "Value"], ["Samples", "24"], ["Status", "Ready"]],
                ),
                element("form_text", 300, text="Initial name", field_name="applicant"),
                element("form_checkbox", 370, width=20, height=20, field_name="consent"),
                element(
                    "form_choice",
                    430,
                    text="Review",
                    field_name="status",
                    options=["Review", "Ready"],
                ),
            ]
        }
    ]
    if mixed:
        import reportlab
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (1000, 450), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(Path(reportlab.__file__).parent / "fonts/Vera.ttf"), 45)
        for y, text in [
            (70, "SCANNED PAGE TWO"),
            (170, "Invoice 4827"),
            (270, "Total 125 dollars"),
        ]:
            draw.text((50, y), text, font=font, fill="black")
        output = io.BytesIO()
        image.save(output, format="PNG")
        image.close()
        pages.append(
            {
                "elements": [
                    element(
                        "image",
                        100,
                        height=225,
                        width=500,
                        image_base64=base64.b64encode(output.getvalue()).decode(),
                    )
                ]
            }
        )
    return {"logical_name": "fixture.pdf", "title": "PDF source fixture", "pages": pages}


def pdf_bytes(*, mixed=False):
    from evidence_lane_plugin.pdf_authoring import generate_pdf
    from evidence_lane_plugin.pdf_contracts import PdfGenerate

    return generate_pdf(PdfGenerate.model_validate(generation(mixed=mixed)))[0]
