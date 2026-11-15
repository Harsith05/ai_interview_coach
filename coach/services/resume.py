"""Extract text from an uploaded resume (PDF or plain text)."""
import io
import logging

log = logging.getLogger(__name__)

MAX_RESUME_BYTES = 5 * 1024 * 1024
MAX_CHARS = 8000


def extract_text(data: bytes, filename: str) -> str:
    if not data:
        return ""
    if len(data) > MAX_RESUME_BYTES:
        raise ValueError("Resume file is larger than 5 MB.")
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:10])
        except Exception as e:
            raise ValueError("Could not read that PDF.") from e
    elif name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="ignore")
    else:
        raise ValueError("Please upload the resume as a PDF or .txt file.")
    text = " ".join(text.split())  # collapse whitespace
    return text[:MAX_CHARS]
