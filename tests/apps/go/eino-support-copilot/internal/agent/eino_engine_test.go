package agent

import (
	"context"
	"errors"
	"sync"
	"testing"

	"github.com/cloudwego/eino/components/model"
	"github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/schema"
	"github.com/eino-contrib/jsonschema"
	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestEinoEngineExecutesToolLoopAndReturnsGuardedDraft(t *testing.T) {
	validDraft := `{"intent":"billing.subscription_activation","priority":"P1","answer":"Payment is confirmed and activation is processing.","citations":[{"chunk_id":"00000000-0000-0000-0000-000000000001","title":"Activation guide","locator":"Timing","content":"Activation can take fifteen minutes."}],"facts":[{"source":"get_payment_status","field":"status","value":"paid"}],"confidence":0.94,"requires_review":false,"review_reasons":[],"follow_up_question":null}`
	chatModel := &fakeChatModel{responses: []modelResponse{
		{message: schema.AssistantMessage("", []schema.ToolCall{{
			ID: "call-payment", Type: "function",
			Function: schema.FunctionCall{Name: "get_payment_status", Arguments: `{"account_id":"acct_demo_pro"}`},
		}})},
		{message: schema.AssistantMessage(validDraft, nil)},
	}}
	paymentTool := &fakeInvokableTool{name: "get_payment_status", result: `{"status":"paid"}`}
	engine, err := NewEinoEngine(context.Background(), EinoDependencies{
		Model: chatModel, Tools: []tool.BaseTool{paymentTool}, Guard: NewGuard(.70),
	})
	require.NoError(t, err)
	sink := &collectingSink{}

	draft, err := engine.Run(context.Background(), validAgentInput(), sink)

	require.NoError(t, err)
	assert.Equal(t, 2, chatModel.Calls())
	assert.Equal(t, 1, paymentTool.calls)
	assert.Equal(t, domain.PriorityP1, draft.Priority)
	assert.True(t, draft.RequiresReview)
	assert.Contains(t, draft.ReviewReasons, ReasonHighPriority)
	assertEventSubsequence(t, sink.events, []domain.EventType{
		domain.EventRunStarted,
		domain.EventToolStarted,
		domain.EventToolCompleted,
		domain.EventCitation,
		domain.EventDraftDelta,
		domain.EventReviewRequired,
	})
}

func TestEinoEngineRepairsInvalidOutputOnce(t *testing.T) {
	chatModel := &fakeChatModel{responses: []modelResponse{
		{message: schema.AssistantMessage("not-json", nil)},
		{message: schema.AssistantMessage(validP2DraftJSON(), nil)},
	}}
	engine, err := NewEinoEngine(context.Background(), EinoDependencies{Model: chatModel, Guard: NewGuard(.70)})
	require.NoError(t, err)

	draft, err := engine.Run(context.Background(), validAgentInput(), &collectingSink{})

	require.NoError(t, err)
	assert.Equal(t, 2, chatModel.Calls())
	assert.Equal(t, "account.general", draft.Intent)
}

func TestEinoEngineReturnsValidationErrorAfterSingleRepair(t *testing.T) {
	chatModel := &fakeChatModel{responses: []modelResponse{
		{message: schema.AssistantMessage("bad-one", nil)},
		{message: schema.AssistantMessage("bad-two", nil)},
	}}
	engine, err := NewEinoEngine(context.Background(), EinoDependencies{Model: chatModel, Guard: NewGuard(.70)})
	require.NoError(t, err)
	sink := &collectingSink{}

	_, err = engine.Run(context.Background(), validAgentInput(), sink)

	assert.ErrorIs(t, err, ErrOutputValidation)
	assert.Equal(t, 2, chatModel.Calls())
	for _, event := range sink.events {
		assert.NotEqual(t, domain.EventRunCompleted, event.Type)
	}
}

func TestEinoEngineRetriesTransientModelErrorOnce(t *testing.T) {
	chatModel := &fakeChatModel{responses: []modelResponse{
		{err: context.DeadlineExceeded},
		{message: schema.AssistantMessage(validP2DraftJSON(), nil)},
	}}
	engine, err := NewEinoEngine(context.Background(), EinoDependencies{Model: chatModel, Guard: NewGuard(.70)})
	require.NoError(t, err)

	_, err = engine.Run(context.Background(), validAgentInput(), &collectingSink{})

	require.NoError(t, err)
	assert.Equal(t, 2, chatModel.Calls())
}

func TestEinoEngineDoesNotRetryPermanentModelError(t *testing.T) {
	permanentErr := errors.New("invalid credentials")
	chatModel := &fakeChatModel{responses: []modelResponse{{err: permanentErr}}}
	engine, err := NewEinoEngine(context.Background(), EinoDependencies{Model: chatModel, Guard: NewGuard(.70)})
	require.NoError(t, err)

	_, err = engine.Run(context.Background(), validAgentInput(), &collectingSink{})

	assert.Error(t, err)
	assert.Equal(t, 1, chatModel.Calls())
}

func validAgentInput() Input {
	return Input{
		ConversationID: uuid.New(), MessageID: uuid.New(), RunID: uuid.New(), CustomerID: uuid.New(),
		Text: "Check my generated account status",
	}
}

func validP2DraftJSON() string {
	return `{"intent":"account.general","priority":"P2","answer":"The generated account is active.","citations":[{"chunk_id":"00000000-0000-0000-0000-000000000001","title":"Account guide","locator":"Status","content":"Active means available."}],"facts":[{"source":"get_account_status","field":"account_status","value":"active"}],"confidence":0.9,"requires_review":false,"review_reasons":[],"follow_up_question":null}`
}

type modelResponse struct {
	message *schema.Message
	err     error
}

type fakeChatModel struct {
	mu        sync.Mutex
	responses []modelResponse
	calls     int
}

func (m *fakeChatModel) Generate(context.Context, []*schema.Message, ...model.Option) (*schema.Message, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.calls++
	if len(m.responses) == 0 {
		return nil, errors.New("fake model exhausted")
	}
	response := m.responses[0]
	m.responses = m.responses[1:]
	return response.message, response.err
}

func (m *fakeChatModel) Stream(context.Context, []*schema.Message, ...model.Option) (*schema.StreamReader[*schema.Message], error) {
	return nil, errors.New("unexpected streaming model call")
}

func (m *fakeChatModel) Calls() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.calls
}

type fakeInvokableTool struct {
	name   string
	result string
	err    error
	calls  int
}

func (t *fakeInvokableTool) Info(context.Context) (*schema.ToolInfo, error) {
	return &schema.ToolInfo{
		Name: t.name,
		Desc: "Generated read-only test tool",
		ParamsOneOf: schema.NewParamsOneOfByJSONSchema(&jsonschema.Schema{
			Type: "object",
		}),
	}, nil
}

func (t *fakeInvokableTool) InvokableRun(context.Context, string, ...tool.Option) (string, error) {
	t.calls++
	return t.result, t.err
}
