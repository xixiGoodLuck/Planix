from pathlib import Path
from re import search

from fastapi import APIRouter, Request, Response, status

from ..errors import bad_request
from ..schemas import AiPayload, RagDocumentCreate, RagDocumentOut, RagIngestPayload, RagQueryOut
from ..services.rag import RagService

router = APIRouter(prefix="/api/rag", tags=["rag"])

rag = RagService()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {".txt", ".md"}


def _decode_upload(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise bad_request("file must be valid UTF-8 or GB18030 text")


def _parse_content_disposition(value: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in value.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        parsed[key.strip().lower()] = raw.strip().strip('"')
    return parsed


def _parse_multipart(content_type: str, body: bytes) -> tuple[str, str, str, bytes]:
    match = search(r"boundary=(?P<boundary>[^;]+)", content_type)
    if not match:
        raise bad_request("multipart boundary is missing")
    boundary = match.group("boundary").strip().strip('"').encode()
    delimiter = b"--" + boundary
    title = ""
    source_type = "upload"
    filename = ""
    file_bytes = b""

    for raw_part in body.split(delimiter):
        part = raw_part.strip()
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].strip()
        if b"\r\n\r\n" not in part:
            continue
        raw_headers, value = part.split(b"\r\n\r\n", 1)
        headers = raw_headers.decode("latin-1").split("\r\n")
        disposition = ""
        for header in headers:
            if header.lower().startswith("content-disposition:"):
                disposition = header.split(":", 1)[1].strip()
                break
        metadata = _parse_content_disposition(disposition)
        name = metadata.get("name", "")
        value = value.rstrip(b"\r\n")
        if name == "file":
            filename = metadata.get("filename", "")
            file_bytes = value
        elif name == "title":
            title = _decode_upload(value).strip()
        elif name == "sourceType":
            source_type = _decode_upload(value).strip() or "upload"

    if not filename:
        raise bad_request("file is required")
    return filename, title, source_type, file_bytes


@router.post("/documents", response_model=RagDocumentOut)
def create_document(payload: RagDocumentCreate) -> RagDocumentOut:
    return rag.create_document(payload)


@router.post("/documents/upload", response_model=RagDocumentOut)
async def upload_document(request: Request) -> RagDocumentOut:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise bad_request("request must use multipart/form-data")

    filename, title, source_type, raw = _parse_multipart(content_type, await request.body())
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise bad_request("only .txt and .md files are supported")

    if len(raw) > MAX_UPLOAD_BYTES:
        raise bad_request("file must be 5MB or smaller")
    if not raw:
        raise bad_request("file cannot be empty")

    content = _decode_upload(raw).strip()
    if not content:
        raise bad_request("file cannot be empty")

    fallback_title = Path(filename).stem or "Uploaded material"
    return rag.create_document(
        RagDocumentCreate(
            title=title.strip() or fallback_title,
            content=content,
            sourceType=source_type.strip() or "upload",
        )
    )


@router.get("/documents", response_model=list[RagDocumentOut])
def list_documents() -> list[RagDocumentOut]:
    return rag.list_documents()


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: str) -> Response:
    rag.delete_document(document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/ingest")
def rag_ingest(payload: RagIngestPayload) -> dict[str, int | str]:
    return rag.ingest(payload)


@router.post("/query", response_model=RagQueryOut)
def rag_query(payload: AiPayload) -> RagQueryOut:
    return rag.query(payload)
