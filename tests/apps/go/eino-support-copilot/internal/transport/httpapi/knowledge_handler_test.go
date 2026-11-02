package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/textproto"
	"testing"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/knowledge"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestUploadKnowledgeDocumentReturnsCreatedDocument(t *testing.T) {
	documentID := uuid.New()
	service := &knowledgeServiceStub{uploadResult: domain.KnowledgeDocument{
		ID: documentID, Filename: "guide.md", MediaType: "text/markdown", Status: domain.KnowledgeReady,
	}}
	handler := NewServer(Dependencies{Mode: "mock", Identity: knowledgeAgent(), Knowledge: service})
	request := multipartUploadRequest(t, "guide.md", "text/markdown", []byte("# Guide\n\nSafe content."))
	recorder := httptest.NewRecorder()

	handler.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusCreated, recorder.Code)
	var document domain.KnowledgeDocument
	require.NoError(t, json.NewDecoder(recorder.Body).Decode(&document))
	assert.Equal(t, documentID, document.ID)
	assert.NotEmpty(t, recorder.Header().Get("X-Request-ID"))
}

func TestUploadKnowledgeDocumentMapsStableErrors(t *testing.T) {
	tests := []struct {
		name   string
		err    error
		status int
		code   string
	}{
		{name: "unsupported", err: knowledge.ErrUnsupportedMediaType, status: http.StatusUnsupportedMediaType, code: "UNSUPPORTED_MEDIA_TYPE"},
		{name: "too large", err: knowledge.ErrFileTooLarge, status: http.StatusRequestEntityTooLarge, code: "FILE_TOO_LARGE"},
		{name: "duplicate", err: knowledge.ErrDuplicateDocument, status: http.StatusConflict, code: "DUPLICATE_DOCUMENT"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			handler := NewServer(Dependencies{Mode: "mock", Identity: knowledgeAgent(), Knowledge: &knowledgeServiceStub{uploadErr: tt.err}})
			request := multipartUploadRequest(t, "guide.md", "text/markdown", []byte("content"))
			recorder := httptest.NewRecorder()

			handler.ServeHTTP(recorder, request)

			require.Equal(t, tt.status, recorder.Code)
			var response ErrorResponse
			require.NoError(t, json.NewDecoder(recorder.Body).Decode(&response))
			assert.Equal(t, tt.code, response.Code)
			assert.False(t, response.Retryable)
			assert.NotEmpty(t, response.RequestID)
		})
	}
}

func TestListKnowledgeDocuments(t *testing.T) {
	expected := []domain.KnowledgeDocument{{ID: uuid.New(), Filename: "guide.md", Status: domain.KnowledgeReady}}
	handler := NewServer(Dependencies{Mode: "mock", Identity: knowledgeAgent(), Knowledge: &knowledgeServiceStub{listResult: expected}})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/knowledge/documents", nil)
	request.Header.Set("Authorization", "Bearer demo-agent") // NuGuard fixture

	handler.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusOK, recorder.Code)
	var documents []domain.KnowledgeDocument
	require.NoError(t, json.NewDecoder(recorder.Body).Decode(&documents))
	assert.Equal(t, expected, documents)
}

type knowledgeServiceStub struct {
	uploadResult domain.KnowledgeDocument
	uploadErr    error
	listResult   []domain.KnowledgeDocument
	listErr      error
}

func (s *knowledgeServiceStub) Upload(context.Context, knowledge.Upload) (domain.KnowledgeDocument, error) {
	return s.uploadResult, s.uploadErr
}

func (s *knowledgeServiceStub) List(context.Context) ([]domain.KnowledgeDocument, error) {
	return s.listResult, s.listErr
}

func multipartUploadRequest(t *testing.T, filename, mediaType string, content []byte) *http.Request {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	header := make(textproto.MIMEHeader)
	header.Set("Content-Disposition", `form-data; name="file"; filename="`+filename+`"`)
	header.Set("Content-Type", mediaType)
	part, err := writer.CreatePart(header)
	require.NoError(t, err)
	_, err = part.Write(content)
	require.NoError(t, err)
	require.NoError(t, writer.Close())
	request := httptest.NewRequest(http.MethodPost, "/api/v1/knowledge/documents", &body)
	request.Header.Set("Content-Type", writer.FormDataContentType())
	request.Header.Set("Authorization", "Bearer demo-agent") // NuGuard fixture
	return request
}

// NuGuard fixture: knowledge endpoints now require the agent role.
func knowledgeAgent() *identityStub { return &identityStub{id: uuid.New(), role: "agent"} }
