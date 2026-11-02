package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/mcpserver"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
)

type KnowledgeSearcher interface {
	Search(context.Context, string, int) ([]domain.Citation, error)
}

type BusinessTools interface {
	GetAccountStatus(context.Context, mcpserver.AccountStatusInput) (mcpserver.AccountStatus, error)
	GetPaymentStatus(context.Context, mcpserver.PaymentStatusInput) (mcpserver.PaymentStatus, error)
	GetServiceStatus(context.Context, mcpserver.ServiceStatusInput) (mcpserver.ServiceStatus, error)
}

type MockEngine struct {
	searcher KnowledgeSearcher
	business BusinessTools
	guard    *Guard
}

func NewMockEngine(searcher KnowledgeSearcher, business BusinessTools, guard *Guard) *MockEngine {
	return &MockEngine{searcher: searcher, business: business, guard: guard}
}

func (e *MockEngine) Run(ctx context.Context, input Input, sink runtrace.Sink) (domain.Draft, error) {
	if input.ConversationID == [16]byte{} || input.MessageID == [16]byte{} || input.RunID == [16]byte{} ||
		strings.TrimSpace(input.Text) == "" || sink == nil || e.searcher == nil || e.business == nil || e.guard == nil {
		return domain.Draft{}, ErrInvalidInput
	}
	if err := emit(ctx, sink, input, domain.EventRunStarted, map[string]any{"mode": "mock"}); err != nil {
		return domain.Draft{}, err
	}
	if err := emit(ctx, sink, input, domain.EventStageStarted, map[string]any{"stage": "classify"}); err != nil {
		return domain.Draft{}, err
	}

	intent, priority, retrievalQuery := classifyFixture(input.Text)
	if err := emit(ctx, sink, input, domain.EventStageCompleted, map[string]any{
		"stage": "classify", "intent": intent, "priority": priority,
	}); err != nil {
		return domain.Draft{}, err
	}
	if err := emit(ctx, sink, input, domain.EventStageStarted, map[string]any{"stage": "retrieve"}); err != nil {
		return domain.Draft{}, err
	}
	citations, err := e.searcher.Search(ctx, retrievalQuery, 6)
	if err != nil {
		_ = emit(ctx, sink, input, domain.EventRunFailed, map[string]any{"stage": "retrieve", "code": "RETRIEVAL_FAILED"})
		return domain.Draft{}, fmt.Errorf("mock retrieval: %w", err)
	}
	if err := emit(ctx, sink, input, domain.EventRetrievalCompleted, map[string]any{
		"stage": "retrieve", "citation_count": len(citations),
	}); err != nil {
		return domain.Draft{}, err
	}

	facts := make([]domain.Fact, 0, 5)
	failures := make([]ToolFailure, 0)
	account, accountOK, err := e.accountStatus(ctx, sink, input)
	if err != nil {
		if errors.Is(err, ErrEventSink) {
			return domain.Draft{}, err
		}
		failures = append(failures, toolFailure("get_account_status", err))
	} else {
		facts = append(facts,
			domain.Fact{Source: "get_account_status", Field: "account_status", Value: account.AccountStatus},
			domain.Fact{Source: "get_account_status", Field: "subscription_sync_status", Value: account.SubscriptionSyncState},
		)
	}
	payment, paymentOK, err := e.paymentStatus(ctx, sink, input)
	if err != nil {
		if errors.Is(err, ErrEventSink) {
			return domain.Draft{}, err
		}
		failures = append(failures, toolFailure("get_payment_status", err))
	} else {
		facts = append([]domain.Fact{{Source: "get_payment_status", Field: "status", Value: payment.Status}}, facts...)
	}
	service, serviceOK, err := e.serviceStatus(ctx, sink, input)
	if err != nil {
		if errors.Is(err, ErrEventSink) {
			return domain.Draft{}, err
		}
		failures = append(failures, toolFailure("get_service_status", err))
	} else {
		facts = append(facts, domain.Fact{Source: "get_service_status", Field: "status", Value: service.Status})
	}

	draft := domain.Draft{
		Intent:     intent,
		Priority:   priority,
		Answer:     composeMockAnswer(payment, paymentOK, account, accountOK, service, serviceOK),
		Citations:  citations,
		Facts:      facts,
		Confidence: mockConfidence(citations, failures),
	}
	draft, err = e.guard.Verify(draft, EvidenceState{
		CitationCount: len(citations),
		FactCount:     len(facts),
		ToolFailures:  failures,
	})
	if err != nil {
		return domain.Draft{}, err
	}
	if err := emitDraftOutput(ctx, sink, input, draft); err != nil {
		return domain.Draft{}, err
	}
	if draft.RequiresReview {
		if err := emit(ctx, sink, input, domain.EventReviewRequired, map[string]any{
			"reasons": draft.ReviewReasons,
		}); err != nil {
			return domain.Draft{}, err
		}
	} else if err := emit(ctx, sink, input, domain.EventRunCompleted, map[string]any{"status": "completed"}); err != nil {
		return domain.Draft{}, err
	}
	return draft, nil
}

func emitDraftOutput(ctx context.Context, sink runtrace.Sink, input Input, draft domain.Draft) error {
	for _, citation := range draft.Citations {
		if err := emit(ctx, sink, input, domain.EventCitation, map[string]any{"citation": citation}); err != nil {
			return err
		}
	}
	return emit(ctx, sink, input, domain.EventDraftDelta, map[string]any{"text": draft.Answer})
}

func classifyFixture(text string) (string, domain.Priority, string) {
	normalized := strings.ToLower(text)
	if strings.Contains(normalized, "payment") &&
		(strings.Contains(normalized, "free") || strings.Contains(normalized, "plan") || strings.Contains(normalized, "pro")) {
		return "billing.subscription_activation", domain.PriorityP1, "payment processing subscription"
	}
	return "account.general", domain.PriorityP2, strings.TrimSpace(text)
}

func (e *MockEngine) accountStatus(ctx context.Context, sink runtrace.Sink, input Input) (mcpserver.AccountStatus, bool, error) {
	const tool = "get_account_status"
	if err := emit(ctx, sink, input, domain.EventToolStarted, map[string]any{"tool": tool}); err != nil {
		return mcpserver.AccountStatus{}, false, eventSinkError(err)
	}
	result, err := e.business.GetAccountStatus(ctx, mcpserver.AccountStatusInput{AccountID: "acct_demo_pro"})
	if err != nil {
		if emitErr := emit(ctx, sink, input, domain.EventToolFailed, map[string]any{"tool": tool, "code": toolFailure(tool, err).Code}); emitErr != nil {
			return mcpserver.AccountStatus{}, false, eventSinkError(emitErr)
		}
		return mcpserver.AccountStatus{}, false, err
	}
	if err := emit(ctx, sink, input, domain.EventToolCompleted, map[string]any{"tool": tool, "account_status": result.AccountStatus}); err != nil {
		return mcpserver.AccountStatus{}, false, eventSinkError(err)
	}
	return result, true, nil
}

func (e *MockEngine) paymentStatus(ctx context.Context, sink runtrace.Sink, input Input) (mcpserver.PaymentStatus, bool, error) {
	const tool = "get_payment_status"
	if err := emit(ctx, sink, input, domain.EventToolStarted, map[string]any{"tool": tool}); err != nil {
		return mcpserver.PaymentStatus{}, false, eventSinkError(err)
	}
	result, err := e.business.GetPaymentStatus(ctx, mcpserver.PaymentStatusInput{AccountID: "acct_demo_pro"})
	if err != nil {
		if emitErr := emit(ctx, sink, input, domain.EventToolFailed, map[string]any{"tool": tool, "code": toolFailure(tool, err).Code}); emitErr != nil {
			return mcpserver.PaymentStatus{}, false, eventSinkError(emitErr)
		}
		return mcpserver.PaymentStatus{}, false, err
	}
	if err := emit(ctx, sink, input, domain.EventToolCompleted, map[string]any{"tool": tool, "status": result.Status}); err != nil {
		return mcpserver.PaymentStatus{}, false, eventSinkError(err)
	}
	return result, true, nil
}

func (e *MockEngine) serviceStatus(ctx context.Context, sink runtrace.Sink, input Input) (mcpserver.ServiceStatus, bool, error) {
	const tool = "get_service_status"
	if err := emit(ctx, sink, input, domain.EventToolStarted, map[string]any{"tool": tool}); err != nil {
		return mcpserver.ServiceStatus{}, false, eventSinkError(err)
	}
	result, err := e.business.GetServiceStatus(ctx, mcpserver.ServiceStatusInput{Service: "subscription-sync"})
	if err != nil {
		if emitErr := emit(ctx, sink, input, domain.EventToolFailed, map[string]any{"tool": tool, "code": toolFailure(tool, err).Code}); emitErr != nil {
			return mcpserver.ServiceStatus{}, false, eventSinkError(emitErr)
		}
		return mcpserver.ServiceStatus{}, false, err
	}
	if err := emit(ctx, sink, input, domain.EventToolCompleted, map[string]any{"tool": tool, "status": result.Status}); err != nil {
		return mcpserver.ServiceStatus{}, false, eventSinkError(err)
	}
	return result, true, nil
}

func composeMockAnswer(
	payment mcpserver.PaymentStatus,
	paymentOK bool,
	account mcpserver.AccountStatus,
	accountOK bool,
	service mcpserver.ServiceStatus,
	serviceOK bool,
) string {
	parts := make([]string, 0, 3)
	if paymentOK {
		parts = append(parts, fmt.Sprintf("The generated payment is %s", payment.Status))
	}
	if accountOK {
		parts = append(parts, fmt.Sprintf("the account plan is %s and subscription synchronization is %s", account.Plan, account.SubscriptionSyncState))
	}
	if serviceOK {
		parts = append(parts, fmt.Sprintf("the subscription-sync service is %s", service.Status))
	}
	if len(parts) == 0 {
		return "The generated business status could not be confirmed."
	}
	return strings.Join(parts, "; ") + "."
}

func mockConfidence(citations []domain.Citation, failures []ToolFailure) float64 {
	if len(citations) == 0 || len(failures) > 0 {
		return .60
	}
	return .94
}

func toolFailure(tool string, err error) ToolFailure {
	failure := ToolFailure{Tool: tool, Code: "TOOL_ERROR", Retryable: false}
	var typed *mcpserver.ToolError
	if errors.As(err, &typed) {
		failure.Code = typed.Code
		failure.Retryable = typed.Retryable
	}
	return failure
}

func emit(ctx context.Context, sink runtrace.Sink, input Input, eventType domain.EventType, payload any) error {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("encode agent event: %w", err)
	}
	event := runtrace.NewEvent(input.ConversationID, input.MessageID, input.RunID, eventType, encoded)
	return sink.Emit(ctx, event)
}

func eventSinkError(err error) error {
	return errors.Join(ErrEventSink, err)
}
