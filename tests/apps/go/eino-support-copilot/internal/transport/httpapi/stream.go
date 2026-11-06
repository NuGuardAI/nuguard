package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
)

type StreamOperation func(context.Context, runtrace.Sink) error
type CancelOperation func(context.Context) error

func WriteEvent(writer io.Writer, event domain.AgentEvent) error {
	data, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("encode stream event: %w", err)
	}
	if _, err := fmt.Fprintf(
		writer,
		"id: %s\nevent: %s\ndata: %s\n\n",
		event.EventID,
		event.Type,
		data,
	); err != nil {
		return fmt.Errorf("write stream event: %w", err)
	}
	return nil
}

func ServeEventStream(
	w http.ResponseWriter,
	r *http.Request,
	next runtrace.Sink,
	operation StreamOperation,
	onCancel CancelOperation,
) error {
	flusher, ok := w.(http.Flusher)
	if !ok {
		return errors.New("response writer does not support streaming")
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	w.Header().Set("Connection", "keep-alive")

	sink := &responseStreamSink{next: next, writer: w, flusher: flusher}
	err := operation(r.Context(), sink)
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		if onCancel == nil {
			return nil
		}
		cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(r.Context()), 5*time.Second)
		defer cancel()
		return onCancel(cleanupCtx)
	}
	return err
}

type responseStreamSink struct {
	next    runtrace.Sink
	writer  io.Writer
	flusher http.Flusher
}

func (s *responseStreamSink) Emit(ctx context.Context, event domain.AgentEvent) error {
	if s.next != nil {
		if err := s.next.Emit(ctx, event); err != nil {
			return err
		}
	}
	if err := WriteEvent(s.writer, event); err != nil {
		return err
	}
	s.flusher.Flush()
	return nil
}
