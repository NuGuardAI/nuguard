package retrieval

import (
	"bytes"
	"fmt"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestParseDocumentAcceptsMarkdown(t *testing.T) {
	doc, err := ParseDocument("activation.md", "text/markdown", []byte("# Activation\n\nPlans activate after payment."))

	require.NoError(t, err)
	assert.Equal(t, "activation.md", doc.Filename)
	assert.Contains(t, doc.Text, "activate after payment")
}

func TestParseDocumentRejectsUnsupportedType(t *testing.T) {
	_, err := ParseDocument("script.sh", "text/x-shellscript", []byte("echo nope"))

	assert.ErrorIs(t, err, ErrUnsupportedDocumentType)
}

func TestParseDocumentRejectsScannedPDF(t *testing.T) {
	_, err := ParseDocument("scan.pdf", "application/pdf", blankPDF(t))

	assert.ErrorIs(t, err, ErrUnsupportedScannedPDF)
}

func TestParseDocumentPreservesPDFPageLocator(t *testing.T) {
	doc, err := ParseDocument("guide.pdf", "application/pdf", textPDF(t, "Activation takes five minutes."))

	require.NoError(t, err)
	require.Len(t, doc.Sections, 1)
	assert.Equal(t, "Page 1", doc.Sections[0].Locator)
	assert.Contains(t, doc.Sections[0].Text, "five minutes")
}

func blankPDF(t *testing.T) []byte {
	t.Helper()
	var body bytes.Buffer
	body.WriteString("%PDF-1.4\n")
	offsets := make([]int, 5)
	writeObject := func(number int, content string) {
		offsets[number] = body.Len()
		fmt.Fprintf(&body, "%d 0 obj\n%s\nendobj\n", number, content)
	}
	writeObject(1, "<< /Type /Catalog /Pages 2 0 R >>")
	writeObject(2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
	writeObject(3, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> /Contents 4 0 R >>")
	writeObject(4, "<< /Length 0 >>\nstream\n\nendstream")
	xref := body.Len()
	body.WriteString("xref\n0 5\n0000000000 65535 f \n")
	for number := 1; number <= 4; number++ {
		fmt.Fprintf(&body, "%010d 00000 n \n", offsets[number])
	}
	fmt.Fprintf(&body, "trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n", xref)
	require.Greater(t, body.Len(), 100)
	return body.Bytes()
}

func textPDF(t *testing.T, text string) []byte {
	t.Helper()
	stream := fmt.Sprintf("BT /F1 12 Tf 72 720 Td (%s) Tj ET", text)
	var body bytes.Buffer
	body.WriteString("%PDF-1.4\n")
	offsets := make([]int, 6)
	writeObject := func(number int, content string) {
		offsets[number] = body.Len()
		fmt.Fprintf(&body, "%d 0 obj\n%s\nendobj\n", number, content)
	}
	writeObject(1, "<< /Type /Catalog /Pages 2 0 R >>")
	writeObject(2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
	writeObject(3, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>")
	writeObject(4, fmt.Sprintf("<< /Length %d >>\nstream\n%s\nendstream", len(stream), stream))
	writeObject(5, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
	xref := body.Len()
	body.WriteString("xref\n0 6\n0000000000 65535 f \n")
	for number := 1; number <= 5; number++ {
		fmt.Fprintf(&body, "%010d 00000 n \n", offsets[number])
	}
	fmt.Fprintf(&body, "trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n", xref)
	return body.Bytes()
}
