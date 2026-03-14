# -*- coding: utf-8 -*-
"""
Multimodal request helpers: attachment detection, file extraction, and
preprocessing to keep /v1/chat/completions interface unchanged.
"""

import base64
import csv
import importlib
import io
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("api")

MAX_MULTIMODAL_FILES = 5
MAX_TEXT_EXTRACT_CHARS = 12000


def _get_latest_user_text(raw_messages: List[Any]) -> str:
    for message in reversed(raw_messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                    continue
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type in {"text", "input_text", "output_text"}:
                    text = part.get("text") or part.get("input_text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts).strip()
    return ""


def _decode_file_data(file_data: str) -> bytes:
    payload = file_data.strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    return base64.b64decode(payload)


def _infer_file_kind(file_name: str, mime_type: str) -> str:
    lowered_name = (file_name or "").lower()
    lowered_mime = (mime_type or "").lower()

    if lowered_mime.startswith("text/plain") or lowered_name.endswith(".txt"):
        return "txt"
    if lowered_mime.startswith("text/csv") or lowered_name.endswith(".csv"):
        return "csv"
    if lowered_mime in {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    } or lowered_name.endswith((".xlsx", ".xls")):
        return "xlsx"
    if lowered_mime == "application/pdf" or lowered_name.endswith(".pdf"):
        return "pdf"
    return "unknown"


def _extract_txt(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "big5", "latin-1"):
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode("utf-8", errors="ignore")


def _extract_csv(data: bytes) -> str:
    text = _extract_txt(data)
    reader = csv.reader(io.StringIO(text))
    rows = []
    for index, row in enumerate(reader):
        rows.append(row)
        if index >= 10:
            break

    if not rows:
        return "[CSV 檔案為空]"

    header = rows[0]
    body = rows[1:6]
    lines = [f"Columns: {', '.join(header)}"]
    for idx, row in enumerate(body, 1):
        lines.append(f"Row {idx}: {row}")
    return "\n".join(lines)


def _extract_pdf(data: bytes) -> str:
    try:
        pypdf_module = importlib.import_module("pypdf")
        PdfReader = getattr(pypdf_module, "PdfReader")
    except Exception:
        return "[PDF 內容未展開：缺少 pypdf 套件]"

    try:
        reader = PdfReader(io.BytesIO(data))
        pages: List[str] = []
        for page in reader.pages[:10]:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(page_text.strip())
        if not pages:
            return "[PDF 未擷取到可讀文字]"
        return "\n\n".join(pages)
    except Exception as exc:
        logger.warning("[Multimodal] PDF extraction failed: %s", exc)
        return f"[PDF 解析失敗: {exc}]"


def _extract_xlsx(data: bytes) -> str:
    try:
        openpyxl_module = importlib.import_module("openpyxl")
        load_workbook = getattr(openpyxl_module, "load_workbook")
    except Exception:
        return "[XLSX 內容未展開：缺少 openpyxl 套件]"

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines: List[str] = []
        for sheet_name in workbook.sheetnames[:5]:
            sheet = workbook[sheet_name]
            lines.append(f"Sheet: {sheet_name}")
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), 1):
                values = ["" if value is None else str(value) for value in row]
                lines.append(f"Row {row_index}: {values}")
                if row_index >= 5:
                    break
        return "\n".join(lines) if lines else "[XLSX 檔案為空]"
    except Exception as exc:
        logger.warning("[Multimodal] XLSX extraction failed: %s", exc)
        return f"[XLSX 解析失敗: {exc}]"


def _summarize_input_file(file_name: str, mime_type: str, data: bytes) -> Tuple[str, str]:
    kind = _infer_file_kind(file_name, mime_type)
    if kind == "txt":
        extracted = _extract_txt(data)
    elif kind == "csv":
        extracted = _extract_csv(data)
    elif kind == "pdf":
        extracted = _extract_pdf(data)
    elif kind == "xlsx":
        extracted = _extract_xlsx(data)
    else:
        extracted = "[目前不支援直接解析此檔案格式，請依檔案名稱與需求作答]"

    compact = extracted.strip()[:MAX_TEXT_EXTRACT_CHARS]
    summary = (
        f"[Attached File]\n"
        f"name: {file_name or '(unnamed)'}\n"
        f"mime_type: {mime_type or '(unknown)'}\n"
        f"kind: {kind}\n"
        f"content:\n{compact}"
    )
    return kind, summary


def prepare_multimodal_messages(
    raw_messages: List[Any],
    max_files: int = MAX_MULTIMODAL_FILES,
) -> Tuple[List[Any], Dict[str, Any]]:
    """Preprocess multimodal content parts while preserving API shape.

    - `image_url` parts are preserved for multimodal-capable chat models.
    - `input_file` parts are converted into text parts so the external API stays
      unchanged while current router remains chat-completions based.
    """
    profile: Dict[str, Any] = {
        "attachment_count": 0,
        "image_count": 0,
        "file_count": 0,
        "file_kinds": [],
        "has_multimodal_input": False,
        "has_image_input": False,
        "has_file_input": False,
        "latest_user_text": _get_latest_user_text(raw_messages),
    }

    processed_messages: List[Any] = []

    for message in raw_messages:
        if not isinstance(message, dict):
            processed_messages.append(message)
            continue

        content = message.get("content", "")
        if not isinstance(content, list):
            processed_messages.append(message)
            continue

        processed_parts: List[Any] = []
        for part in content:
            if isinstance(part, str):
                processed_parts.append({"type": "text", "text": part})
                continue

            if not isinstance(part, dict):
                continue

            part_type = part.get("type")
            if part_type in {"text", "input_text", "output_text"}:
                text = part.get("text") or part.get("input_text") or ""
                if isinstance(text, str) and text:
                    processed_parts.append({"type": "text", "text": text})
                continue

            if part_type == "image_url":
                profile["attachment_count"] += 1
                profile["image_count"] += 1
                profile["has_multimodal_input"] = True
                profile["has_image_input"] = True
                processed_parts.append(part)
                continue

            if part_type == "input_file":
                profile["attachment_count"] += 1
                profile["file_count"] += 1
                profile["has_multimodal_input"] = True
                profile["has_file_input"] = True
                if profile["file_count"] > max_files:
                    raise ValueError(f"最多只支援 {max_files} 份文件輸入")

                file_name = str(part.get("file_name") or part.get("filename") or "")
                mime_type = str(part.get("mime_type") or part.get("content_type") or "")
                file_data = part.get("file_data") or part.get("data")

                if not isinstance(file_data, str) or not file_data.strip():
                    processed_parts.append({
                        "type": "text",
                        "text": (
                            f"[Attached File]\nname: {file_name or '(unnamed)'}\n"
                            f"mime_type: {mime_type or '(unknown)'}\n"
                            "content: [檔案內容缺失，請依現有資訊回覆]"
                        ),
                    })
                    continue

                try:
                    raw_file = _decode_file_data(file_data)
                    kind, summary = _summarize_input_file(file_name, mime_type, raw_file)
                except Exception as exc:
                    kind = _infer_file_kind(file_name, mime_type)
                    summary = (
                        f"[Attached File]\nname: {file_name or '(unnamed)'}\n"
                        f"mime_type: {mime_type or '(unknown)'}\n"
                        f"kind: {kind}\n"
                        f"content: [檔案解析失敗: {exc}]"
                    )

                profile["file_kinds"].append(kind)
                processed_parts.append({"type": "text", "text": summary})
                continue

        cloned = dict(message)
        cloned["content"] = processed_parts if processed_parts else ""
        processed_messages.append(cloned)

    return processed_messages, profile
