"""PDF export of an interview report (xhtml2pdf: pure Python, no browser needed)."""
import io
import re

from coach.templating import templates

# The PDF's built-in fonts have no emoji glyphs; drop them instead of printing boxes.
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def render_pdf(interview, user) -> bytes:
    from xhtml2pdf import pisa

    html = templates.get_template("report_pdf.html").render(
        iv=interview, user=user, answers=[a.result for a in interview.answers], s=interview.summary or {},
    )
    html = _EMOJI.sub("", html)
    out = io.BytesIO()
    result = pisa.CreatePDF(html, dest=out, encoding="utf-8")
    if result.err:
        raise RuntimeError("PDF generation failed")
    return out.getvalue()
