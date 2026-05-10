PDF_SIGNATURE = b"%PDF-"
PDF_MIME_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "binary/pdf",
}
DOC_MIME_TYPES = {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


__all__ = [
    "DOCX_MIME_TYPE",
    "DOC_MIME_TYPES",
    "PDF_MIME_TYPES",
    "PDF_SIGNATURE",
]
