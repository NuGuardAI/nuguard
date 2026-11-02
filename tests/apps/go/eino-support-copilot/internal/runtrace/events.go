package runtrace

import (
	"context"
	"encoding/json"
	"time"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
)

type Sink interface {
	Emit(context.Context, domain.AgentEvent) error
}

type Subscriber interface {
	Emit(context.Context, domain.AgentEvent) error
}

type SubscriberFunc func(context.Context, domain.AgentEvent) error

func (f SubscriberFunc) Emit(ctx context.Context, event domain.AgentEvent) error {
	return f(ctx, event)
}

type StepAppender interface {
	AppendStep(context.Context, domain.AgentEvent) error
}

type PersistentSink struct {
	appender   StepAppender
	subscriber Subscriber
}

func NewPersistentSink(appender StepAppender, subscriber Subscriber) *PersistentSink {
	return &PersistentSink{appender: appender, subscriber: subscriber}
}

func (s *PersistentSink) Emit(ctx context.Context, event domain.AgentEvent) error {
	if err := s.appender.AppendStep(ctx, event); err != nil {
		return err
	}
	if s.subscriber != nil {
		return s.subscriber.Emit(ctx, event)
	}
	return nil
}

func NewEvent(
	conversationID uuid.UUID,
	messageID uuid.UUID,
	runID uuid.UUID,
	eventType domain.EventType,
	payload json.RawMessage,
) domain.AgentEvent {
	if len(payload) == 0 {
		payload = json.RawMessage(`{}`)
	}
	return domain.AgentEvent{
		EventID:        uuid.New(),
		ConversationID: conversationID,
		MessageID:      messageID,
		RunID:          runID,
		Type:           eventType,
		Timestamp:      time.Now().UTC(),
		Payload:        payload,
	}
}
