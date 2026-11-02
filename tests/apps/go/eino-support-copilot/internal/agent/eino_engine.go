package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"strings"
	"time"

	"github.com/cloudwego/eino/adk"
	"github.com/cloudwego/eino/components/model"
	"github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/compose"
	"github.com/cloudwego/eino/schema"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
)

type EinoDependencies struct {
	Model model.BaseChatModel
	Tools []tool.BaseTool
	Guard *Guard
	Close func() error
}

type EinoEngine struct {
	runner *adk.Runner
	guard  *Guard
	close  func() error
}

func NewEinoEngine(ctx context.Context, dependencies EinoDependencies) (*EinoEngine, error) {
	if dependencies.Model == nil || dependencies.Guard == nil {
		return nil, ErrInvalidInput
	}
	tools := make([]tool.BaseTool, 0, len(dependencies.Tools))
	for _, item := range dependencies.Tools {
		if item == nil {
			return nil, ErrInvalidInput
		}
		tools = append(tools, &runtimeTool{inner: item})
	}
	agentInstance, err := adk.NewChatModelAgent(ctx, &adk.ChatModelAgentConfig{
		Name:          "support-copilot",
		Description:   "Resolve generated SaaS support questions with cited knowledge and read-only tools",
		Instruction:   systemPrompt,
		Model:         dependencies.Model,
		MaxIterations: 6,
		ModelRetryConfig: &adk.ModelRetryConfig{
			MaxRetries: 1,
			IsRetryAble: func(_ context.Context, err error) bool {
				return isTransientModelError(err)
			},
			BackoffFunc: func(context.Context, int) time.Duration { return 0 },
		},
		ToolsConfig: adk.ToolsConfig{ToolsNodeConfig: compose.ToolsNodeConfig{
			Tools:               tools,
			ExecuteSequentially: true,
		}},
	})
	if err != nil {
		return nil, fmt.Errorf("create Eino ChatModelAgent: %w", err)
	}
	return &EinoEngine{
		runner: adk.NewRunner(ctx, adk.RunnerConfig{Agent: agentInstance, EnableStreaming: false}),
		guard:  dependencies.Guard,
		close:  dependencies.Close,
	}, nil
}

func (e *EinoEngine) Run(ctx context.Context, input Input, sink runtrace.Sink) (domain.Draft, error) {
	if input.ConversationID == [16]byte{} || input.MessageID == [16]byte{} || input.RunID == [16]byte{} ||
		input.Text == "" || sink == nil {
		return domain.Draft{}, ErrInvalidInput
	}
	if err := emit(ctx, sink, input, domain.EventRunStarted, map[string]any{"mode": "openai-compatible"}); err != nil {
		return domain.Draft{}, err
	}
	runCtx := context.WithValue(ctx, runtimeContextKey{}, runtimeContext{input: input, sink: sink})
	prompt := input.Text
	var draft domain.Draft
	var parseErr error
	for attempt := 0; attempt < 2; attempt++ {
		content, err := e.query(runCtx, prompt)
		if err != nil {
			_ = emit(ctx, sink, input, domain.EventRunFailed, map[string]any{"code": "MODEL_RUNTIME_FAILED"})
			return domain.Draft{}, fmt.Errorf("run Eino agent: %w", err)
		}
		draft, parseErr = decodeDraft(content)
		if parseErr == nil {
			break
		}
		prompt = repairPromptPrefix + input.Text
	}
	if parseErr != nil {
		_ = emit(ctx, sink, input, domain.EventRunFailed, map[string]any{"code": "OUTPUT_VALIDATION_FAILED"})
		return domain.Draft{}, fmt.Errorf("%w: %v", ErrOutputValidation, parseErr)
	}

	draft, err := e.guard.Verify(draft, EvidenceState{
		CitationCount: len(draft.Citations),
		FactCount:     len(draft.Facts),
	})
	if err != nil {
		return domain.Draft{}, err
	}
	if err := emitDraftOutput(ctx, sink, input, draft); err != nil {
		return domain.Draft{}, err
	}
	if draft.RequiresReview {
		if err := emit(ctx, sink, input, domain.EventReviewRequired, map[string]any{"reasons": draft.ReviewReasons}); err != nil {
			return domain.Draft{}, err
		}
	} else if err := emit(ctx, sink, input, domain.EventRunCompleted, map[string]any{"status": "completed"}); err != nil {
		return domain.Draft{}, err
	}
	return draft, nil
}

func (e *EinoEngine) query(ctx context.Context, prompt string) (string, error) {
	iterator := e.runner.Query(ctx, prompt)
	var last string
	for {
		event, ok := iterator.Next()
		if !ok {
			break
		}
		if event.Err != nil {
			return "", event.Err
		}
		if event.Output == nil || event.Output.MessageOutput == nil {
			continue
		}
		variant := event.Output.MessageOutput
		if variant.IsStreaming {
			message, err := variant.GetMessage()
			if err != nil {
				return "", err
			}
			if message != nil && message.Role == schema.Assistant && message.Content != "" {
				last = message.Content
			}
			continue
		}
		message := variant.Message
		if message != nil && message.Role == schema.Assistant && message.Content != "" {
			last = message.Content
		}
	}
	if last == "" {
		return "", errors.New("Eino agent returned no final assistant message")
	}
	return last, nil
}

func (e *EinoEngine) Close() error {
	if e.close != nil {
		return e.close()
	}
	return nil
}

func decodeDraft(content string) (domain.Draft, error) {
	decoder := json.NewDecoder(bytes.NewBufferString(content))
	decoder.DisallowUnknownFields()
	var draft domain.Draft
	if err := decoder.Decode(&draft); err != nil {
		return domain.Draft{}, err
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return domain.Draft{}, errors.New("multiple JSON values are not allowed")
	}
	return draft, nil
}

func isTransientModelError(err error) bool {
	if errors.Is(err, context.DeadlineExceeded) {
		return true
	}
	var networkError net.Error
	return errors.As(err, &networkError) && (networkError.Timeout() || networkError.Temporary())
}

type runtimeContextKey struct{}

type runtimeContext struct {
	input Input
	sink  runtrace.Sink
}

type runtimeTool struct {
	inner tool.BaseTool
}

func (t *runtimeTool) Info(ctx context.Context) (*schema.ToolInfo, error) {
	return t.inner.Info(ctx)
}

func (t *runtimeTool) InvokableRun(ctx context.Context, arguments string, opts ...tool.Option) (string, error) {
	runtimeState, ok := ctx.Value(runtimeContextKey{}).(runtimeContext)
	if !ok {
		return "", ErrInvalidInput
	}
	info, err := t.inner.Info(ctx)
	if err != nil {
		return "", err
	}
	if err := emit(ctx, runtimeState.sink, runtimeState.input, domain.EventToolStarted, map[string]any{
		"tool": info.Name, "arguments": json.RawMessage(arguments),
	}); err != nil {
		return "", eventSinkError(err)
	}
	invokable, ok := t.inner.(tool.InvokableTool)
	if !ok {
		return "", fmt.Errorf("tool %s is not invokable", info.Name)
	}
	result, err := invokable.InvokableRun(ctx, arguments, opts...)
	if err != nil {
		if emitErr := emit(ctx, runtimeState.sink, runtimeState.input, domain.EventToolFailed, map[string]any{
			"tool": info.Name, "code": publicToolErrorCode(err),
		}); emitErr != nil {
			return "", eventSinkError(emitErr)
		}
		return "", err
	}
	if err := emit(ctx, runtimeState.sink, runtimeState.input, domain.EventToolCompleted, map[string]any{
		"tool": info.Name, "result_bytes": len(result),
	}); err != nil {
		return "", eventSinkError(err)
	}
	return result, nil
}

func publicToolErrorCode(err error) string {
	if strings.Contains(err.Error(), "TEMPORARY_UNAVAILABLE") {
		return "TEMPORARY_UNAVAILABLE"
	}
	return "TOOL_ERROR"
}
