package httpapi

import (
	"errors"
	"net/http"
	"strings"

	"github.com/guiyangyuan/eino-support-copilot/internal/knowledge"
	"github.com/guiyangyuan/eino-support-copilot/internal/retrieval"
)

const multipartOverheadBytes = 1 << 20

// NuGuard fixture: upstream left both knowledge endpoints unauthenticated; they now require the
// "agent" (support staff) role, like the ticket, run and evaluation handlers.
func uploadKnowledgeDocument(service KnowledgeService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if _, ok := requireDemoRole(w, r, identity, "agent"); !ok {
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "KNOWLEDGE_UNAVAILABLE", "knowledge service is unavailable", true)
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, knowledge.MaxUploadBytes+multipartOverheadBytes)
		if err := r.ParseMultipartForm(multipartOverheadBytes); err != nil {
			if strings.Contains(err.Error(), "request body too large") {
				WriteError(w, r, http.StatusRequestEntityTooLarge, "FILE_TOO_LARGE", "file exceeds 10 MB", false)
				return
			}
			WriteError(w, r, http.StatusBadRequest, "INVALID_MULTIPART", "a multipart file field is required", false)
			return
		}
		if r.MultipartForm != nil {
			defer r.MultipartForm.RemoveAll()
		}
		file, header, err := r.FormFile("file")
		if err != nil {
			WriteError(w, r, http.StatusBadRequest, "FILE_REQUIRED", "multipart field file is required", false)
			return
		}
		defer file.Close()

		document, err := service.Upload(r.Context(), knowledge.Upload{
			Filename:  header.Filename,
			MediaType: header.Header.Get("Content-Type"),
			Size:      header.Size,
			Reader:    file,
		})
		if err != nil {
			writeKnowledgeError(w, r, err)
			return
		}
		writeJSON(w, http.StatusCreated, document)
	}
}

func listKnowledgeDocuments(service KnowledgeService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if _, ok := requireDemoRole(w, r, identity, "agent"); !ok { // NuGuard fixture
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "KNOWLEDGE_UNAVAILABLE", "knowledge service is unavailable", true)
			return
		}
		documents, err := service.List(r.Context())
		if err != nil {
			WriteError(w, r, http.StatusInternalServerError, "KNOWLEDGE_LIST_FAILED", "unable to list knowledge documents", true)
			return
		}
		writeJSON(w, http.StatusOK, documents)
	}
}

func writeKnowledgeError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, knowledge.ErrUnsupportedMediaType):
		WriteError(w, r, http.StatusUnsupportedMediaType, "UNSUPPORTED_MEDIA_TYPE", "only Markdown, text, and text-based PDF files are supported", false)
	case errors.Is(err, knowledge.ErrContentTypeMismatch):
		WriteError(w, r, http.StatusUnsupportedMediaType, "CONTENT_TYPE_MISMATCH", "file content does not match its declared type", false)
	case errors.Is(err, knowledge.ErrFileTooLarge):
		WriteError(w, r, http.StatusRequestEntityTooLarge, "FILE_TOO_LARGE", "file exceeds 10 MB", false)
	case errors.Is(err, knowledge.ErrDuplicateDocument):
		WriteError(w, r, http.StatusConflict, "DUPLICATE_DOCUMENT", "this document has already been uploaded", false)
	case errors.Is(err, retrieval.ErrUnsupportedScannedPDF), errors.Is(err, retrieval.ErrEmptyDocument):
		WriteError(w, r, http.StatusUnprocessableEntity, "KNOWLEDGE_INDEX_FAILED", "document does not contain extractable text", false)
	default:
		WriteError(w, r, http.StatusInternalServerError, "KNOWLEDGE_INDEX_FAILED", "document could not be indexed", true)
	}
}
