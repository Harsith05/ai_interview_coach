"""Parsing user-provided question lists.

Accepted formats (mixable), one question per line:
    What is overfitting?
    1. What is REST?                         (numbering / bullets are stripped)
    What is a JOIN? :: Combines rows from two tables on a related column   (optional model answer)
    Q: Tell me about yourself.
    A: I'm a CS student who ...              (an "A:" / "Answer:" line attaches to the question above)
A .csv file with columns question[,answer] also works.
"""
import csv
import io
import re

MAX_QUESTIONS = 100
_NUMBERING = re.compile(r"^\s*(?:(?:q(?:uestion)?\s*\d*\s*[:.)-])|(?:\d+\s*[.):-])|[-*•●▪])\s*", re.I)
_ANSWER_LINE = re.compile(r"^\s*(?:a|ans|answer)\s*\d*\s*[:.)-]\s*", re.I)


def parse_text(text: str) -> list[dict]:
    items: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _ANSWER_LINE.match(line) and items:
            ans = _ANSWER_LINE.sub("", line).strip()
            items[-1]["answer"] = (items[-1]["answer"] + " " + ans).strip()
            continue
        line = _NUMBERING.sub("", line).strip()
        question, answer = line, ""
        for sep in ("::", "\t", " | "):
            if sep in line:
                question, answer = (p.strip() for p in line.split(sep, 1))
                break
        if len(question) >= 3:
            items.append({"question": question[:500], "answer": answer[:2000]})
    return items[:MAX_QUESTIONS]


def parse_csv(data: str) -> list[dict]:
    rows = list(csv.reader(io.StringIO(data)))
    if rows and rows[0] and rows[0][0].strip().lower() in ("question", "questions", "q"):
        rows = rows[1:]
    items = [{"question": r[0].strip()[:500], "answer": (r[1].strip() if len(r) > 1 else "")[:2000]}
             for r in rows if r and len(r[0].strip()) >= 3]
    return items[:MAX_QUESTIONS]


def parse_upload(filename: str, data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig", errors="ignore")
    return parse_csv(text) if filename.lower().endswith(".csv") else parse_text(text)


def to_text(items: list[dict]) -> str:
    """Inverse of parse_text, for the edit box."""
    return "\n".join(f"{i['question']} :: {i['answer']}" if i.get("answer") else i["question"] for i in items)
