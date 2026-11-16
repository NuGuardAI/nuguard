package retrieval

import (
	"bytes"
	"errors"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/ledongthuc/pdf"
)

var (
	ErrUnsupportedDocumentType = errors.New("unsupported document type")
	ErrUnsupportedScannedPDF   = errors.New("UNSUPPORTED_SCANNED_PDF")
	ErrEmptyDocument           = errors.New("document contains no text")
)

type Document struct {
	Filename  string
	MediaType string
	Text      string
	Sections  []DocumentSection
}

type DocumentSection struct {
	Locator string
	Text    string
}

func ParseDocument(filename, mediaType string, data []byte) (Document, error) {
	extension := strings.ToLower(filepath.Ext(filename))
	switch extension {
	case ".md", ".txt":
		if mediaType != "text/markdown" && mediaType != "text/plain" {
			return Document{}, ErrUnsupportedDocumentType
		}
		text := strings.TrimSpace(string(data))
		if text == "" {
			return Document{}, ErrEmptyDocument
		}
		return Document{Filename: filename, MediaType: mediaType, Text: text}, nil
	case ".pdf":
		if mediaType != "application/pdf" {
			return Document{}, ErrUnsupportedDocumentType
		}
		return parsePDF(filename, data)
	default:
		return Document{}, ErrUnsupportedDocumentType
	}
}

func parsePDF(filename string, data []byte) (Document, error) {
	reader, err := pdf.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return Document{}, fmt.Errorf("parse PDF: %w", err)
	}

	sections := make([]DocumentSection, 0, reader.NumPage())
	texts := make([]string, 0, reader.NumPage())
	for pageNumber := 1; pageNumber <= reader.NumPage(); pageNumber++ {
		text, err := reader.Page(pageNumber).GetPlainText(nil)
		if err != nil {
			return Document{}, fmt.Errorf("extract PDF page %d: %w", pageNumber, err)
		}
		text = strings.TrimSpace(text)
		if text == "" {
			continue
		}
		sections = append(sections, DocumentSection{
			Locator: fmt.Sprintf("Page %d", pageNumber),
			Text:    text,
		})
		texts = append(texts, text)
	}
	if len(sections) == 0 {
		return Document{}, ErrUnsupportedScannedPDF
	}
	return Document{
		Filename:  filename,
		MediaType: "application/pdf",
		Text:      strings.Join(texts, "\n\n"),
		Sections:  sections,
	}, nil
}
