package workflow

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"agent-desk/internal/ai/workflow/dsl"
	workflowregistry "agent-desk/internal/ai/workflow/registry"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/services"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func mustMarshalWorkflowTestConfig(value any) json.RawMessage {
	raw, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return raw
}

func TestExecutorRoutesByConditionNodeBranch(t *testing.T) {
	executor := NewExecutor()
	result, err := executor.Execute(context.Background(), Input{
		Definition: conditionalReplyDefinition(),
		UserMessage: models.Message{
			Content: "vip",
		},
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	if result.ReplyText != "VIP reply" {
		t.Fatalf("unexpected reply: %q", result.ReplyText)
	}
	assertPath(t, result.NodePath, []string{"start_1", "condition_1", "vip_reply", "send_vip", "end_1"})
}

func TestExecutorRoutesOfficialFlowGramConditionPorts(t *testing.T) {
	definition := officialFlowGramConditionDefinition()
	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition:  definition,
		UserMessage: models.Message{Content: "hello FlowGram"},
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	assertPath(t, result.NodePath, []string{"start_0", "condition_0", "matched_end"})

	result, err = NewExecutor().Execute(context.Background(), Input{
		Definition:  definition,
		UserMessage: models.Message{Content: "goodbye"},
	})
	if err != nil {
		t.Fatalf("execute workflow else branch: %v", err)
	}
	assertPath(t, result.NodePath, []string{"start_0", "condition_0", "else_end"})
}

func officialFlowGramConditionDefinition() dsl.Definition {
	return dsl.Definition{
		Nodes: []dsl.Node{
			{ID: "start_0", Type: workflowregistry.NodeTypeStart, Data: dsl.NodeData{Title: "Start"}},
			{
				ID:   "condition_0",
				Type: workflowregistry.NodeTypeCondition,
				Data: dsl.NodeData{
					Title: "Condition",
					Extra: map[string]json.RawMessage{
						"conditions": mustMarshalWorkflowTestConfig([]dsl.FlowGramConditionItem{
							{
								Key: "if_0",
								Value: dsl.FlowGramCondition{
									Left:     dsl.RefValue("start_0", "query"),
									Operator: "contains",
									Right:    dsl.ConstantValue("hello"),
								},
							},
						}),
					},
				},
			},
			{ID: "matched_end", Type: workflowregistry.NodeTypeEnd, Data: dsl.NodeData{Title: "End"}},
			{ID: "else_end", Type: workflowregistry.NodeTypeEnd, Data: dsl.NodeData{Title: "End"}},
		},
		Edges: []dsl.Edge{
			{SourceNodeID: "start_0", TargetNodeID: "condition_0"},
			{SourceNodeID: "condition_0", TargetNodeID: "matched_end", SourcePortID: "if_0"},
			{SourceNodeID: "condition_0", TargetNodeID: "else_end", SourcePortID: "else"},
		},
	}
}

func TestExecutorConditionNodeTraceExplainsMatchedEdge(t *testing.T) {
	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition: conditionalReplyDefinition(),
		UserMessage: models.Message{
			Content: "vip",
		},
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}

	trace := findNodeTrace(result.NodeTraces, "condition_1")
	if trace == nil {
		t.Fatalf("expected condition node trace, got %#v", result.NodeTraces)
	}
	for _, want := range []string{
		`"selectedEdgeId":"edge_condition_vip"`,
		`"selectedBranchId":"vip"`,
		`"selectedTargetNodeId":"vip_reply"`,
		`"operator":"eq"`,
		`"leftValue":"vip"`,
		`"matched":true`,
	} {
		if !strings.Contains(trace.OutputPreview, want) {
			t.Fatalf("expected condition trace output to contain %s, got %s", want, trace.OutputPreview)
		}
	}
}

func TestExecutorConditionNodeTraceExplainsDefaultEdge(t *testing.T) {
	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition: conditionalReplyDefinition(),
		UserMessage: models.Message{
			Content: "normal",
		},
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}

	trace := findNodeTrace(result.NodeTraces, "condition_1")
	if trace == nil {
		t.Fatalf("expected condition node trace, got %#v", result.NodeTraces)
	}
	for _, want := range []string{
		`"selectedEdgeId":"edge_condition_default"`,
		`"selectedBranchId":"default"`,
		`"selectedTargetNodeId":"normal_reply"`,
		`"reason":"no condition branch matched; selected default branch"`,
		`"leftValue":"normal"`,
		`"matched":false`,
	} {
		if !strings.Contains(trace.OutputPreview, want) {
			t.Fatalf("expected condition trace output to contain %s, got %s", want, trace.OutputPreview)
		}
	}
}

func TestExecutorUsesDefaultEdgeWhenConditionDoesNotMatch(t *testing.T) {
	executor := NewExecutor()
	result, err := executor.Execute(context.Background(), Input{
		Definition: conditionalReplyDefinition(),
		UserMessage: models.Message{
			Content: "normal",
		},
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	if result.ReplyText != "Normal reply" {
		t.Fatalf("unexpected reply: %q", result.ReplyText)
	}
	assertPath(t, result.NodePath, []string{"start_1", "condition_1", "normal_reply", "send_normal", "end_1"})
}

func TestExecutorHandoffToHumanRunsRealDispatchAction(t *testing.T) {
	db := setupWorkflowExecutorHandoffDB(t)
	aiAgent := createWorkflowExecutorHandoffAIAgent(t, db, "1")
	createWorkflowExecutorHandoffTeam(t, db, 1, "售后支持组")
	createWorkflowExecutorHandoffActiveSchedule(t, db, 1)
	createWorkflowExecutorHandoffAgentProfile(t, db, 101, 1)
	conversation := createWorkflowExecutorHandoffConversation(t, db, aiAgent.ID)
	userMessage := createWorkflowExecutorCustomerMessage(t, db, conversation.ID, "需要人工处理")

	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition:   handoffWorkflowDefinition(),
		Conversation: conversation,
		UserMessage:  userMessage,
		AIAgent:      aiAgent,
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	if strings.TrimSpace(result.ReplyText) != "" {
		t.Fatalf("expected workflow handoff node to avoid duplicate reply text, got %q", result.ReplyText)
	}
	assertPath(t, result.NodePath, []string{"start_1", "handoff_1", "handoff_route_1", "assigned_end"})

	current := services.ConversationService.Get(conversation.ID)
	if current.Status != enums.IMConversationStatusActive {
		t.Fatalf("expected active conversation, got status=%d", current.Status)
	}
	if current.CurrentAssigneeID != 101 || current.CurrentTeamID != 1 {
		t.Fatalf("unexpected assignment: assignee=%d team=%d", current.CurrentAssigneeID, current.CurrentTeamID)
	}
	if current.HandoffAt == nil || current.HandoffReason != "需要人工处理" {
		t.Fatalf("expected handoff metadata, got at=%v reason=%q", current.HandoffAt, current.HandoffReason)
	}

	notice := services.MessageService.FindOne(sqls.NewCnd().Eq("conversation_id", conversation.ID).Eq("sender_type", enums.IMSenderTypeAI).Desc("id"))
	if notice == nil || strings.TrimSpace(notice.Content) == "" {
		t.Fatalf("expected handoff service to send ai notice, got %+v", notice)
	}
}

func TestExecutorResumeSkipsHandoffWhenConfirmationCancelled(t *testing.T) {
	db := setupWorkflowExecutorHandoffDB(t)
	aiAgent := createWorkflowExecutorHandoffAIAgent(t, db, "1")
	createWorkflowExecutorHandoffTeam(t, db, 1, "售后支持组")
	createWorkflowExecutorHandoffActiveSchedule(t, db, 1)
	createWorkflowExecutorHandoffAgentProfile(t, db, 101, 1)
	conversation := createWorkflowExecutorHandoffConversation(t, db, aiAgent.ID)
	userMessage := createWorkflowExecutorCustomerMessage(t, db, conversation.ID, "需要人工处理")
	input := Input{
		Definition:   handoffAfterConfirmationWorkflowDefinition(),
		Conversation: conversation,
		UserMessage:  userMessage,
		AIAgent:      aiAgent,
	}

	interrupted, err := NewExecutor().Execute(context.Background(), input)
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	if !interrupted.Interrupted {
		t.Fatalf("expected workflow to interrupt before handoff")
	}

	result, err := NewExecutor().Resume(context.Background(), input, interrupted.CheckPointData, "取消")
	if err != nil {
		t.Fatalf("resume workflow: %v", err)
	}
	if result.Interrupted {
		t.Fatalf("expected cancelled resume to complete")
	}
	assertPath(t, result.NodePath, []string{"handoff_1", "end_1"})

	current := services.ConversationService.Get(conversation.ID)
	if current.Status != enums.IMConversationStatusAIServing {
		t.Fatalf("expected conversation to remain ai serving, got status=%d", current.Status)
	}
	if current.CurrentAssigneeID != 0 || current.CurrentTeamID != 0 || current.HandoffAt != nil {
		t.Fatalf("expected no handoff side effect, got assignee=%d team=%d handoffAt=%v", current.CurrentAssigneeID, current.CurrentTeamID, current.HandoffAt)
	}
	if count := services.MessageService.Count(sqls.NewCnd().Eq("conversation_id", conversation.ID).Eq("sender_type", enums.IMSenderTypeAI)); count != 0 {
		t.Fatalf("expected no handoff notice message, got %d", count)
	}
}

func TestExecutorAnalyzeConversationOutputsBranchVariables(t *testing.T) {
	db := setupWorkflowExecutorHandoffDB(t)
	aiAgent := createWorkflowExecutorHandoffAIAgent(t, db, "1")
	conversation := createWorkflowExecutorHandoffConversation(t, db, aiAgent.ID)
	userMessage := createWorkflowExecutorCustomerMessage(t, db, conversation.ID, "你们重复扣费了，我要投诉并转人工")

	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition:   analyzeConversationWorkflowDefinition(),
		Conversation: conversation,
		UserMessage:  userMessage,
		AIAgent:      aiAgent,
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	assertPath(t, result.NodePath, []string{"start_1", "analyze_1", "analyze_route_1", "handoff_end"})
}

func TestExecutorPrepareTicketDraftOutputsDraftVariable(t *testing.T) {
	db := setupWorkflowExecutorHandoffDB(t)
	aiAgent := createWorkflowExecutorHandoffAIAgent(t, db, "1")
	conversation := createWorkflowExecutorHandoffConversation(t, db, aiAgent.ID)
	userMessage := createWorkflowExecutorCustomerMessage(t, db, conversation.ID, "订单支付失败，请帮我登记工单")

	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition:   prepareTicketDraftWorkflowDefinition(),
		Conversation: conversation,
		UserMessage:  userMessage,
		AIAgent:      aiAgent,
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	assertPath(t, result.NodePath, []string{"start_1", "draft_1", "draft_route_1", "ready_end"})
}

func TestExecutorPrepareTicketDraftRoutesIncompleteDraftToFollowUp(t *testing.T) {
	db := setupWorkflowExecutorHandoffDB(t)
	aiAgent := createWorkflowExecutorHandoffAIAgent(t, db, "1")
	conversation := createWorkflowExecutorHandoffConversation(t, db, aiAgent.ID)
	userMessage := createWorkflowExecutorCustomerMessage(t, db, conversation.ID, "")

	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition:   ticketDraftReadyWorkflowDefinition(),
		Conversation: conversation,
		UserMessage:  userMessage,
		AIAgent:      aiAgent,
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	if result.Interrupted {
		t.Fatalf("expected incomplete draft to avoid confirmation interrupt")
	}
	if !strings.Contains(result.ReplyText, "Please provide") {
		t.Fatalf("expected follow-up questions in reply, got %q", result.ReplyText)
	}
	assertPath(t, result.NodePath, []string{"start_1", "draft_1", "draft_route_1", "followup_1", "send_followup_1", "end_1"})
}

func TestExecutorTicketConfirmationPromptIncludesDraftTitleAndDescription(t *testing.T) {
	db := setupWorkflowExecutorHandoffDB(t)
	aiAgent := createWorkflowExecutorHandoffAIAgent(t, db, "1")
	conversation := createWorkflowExecutorHandoffConversation(t, db, aiAgent.ID)
	userMessage := createWorkflowExecutorCustomerMessage(t, db, conversation.ID, "订单支付失败，请帮我登记工单")

	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition:   ticketDraftReadyWorkflowDefinition(),
		Conversation: conversation,
		UserMessage:  userMessage,
		AIAgent:      aiAgent,
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	if !result.Interrupted {
		t.Fatalf("expected ready draft to interrupt for confirmation")
	}
	if len(result.Interrupts) != 1 {
		t.Fatalf("expected one interrupt, got %#v", result.Interrupts)
	}
	prompt := result.Interrupts[0].InfoPreview
	if !strings.Contains(prompt, "订单支付失败") || !strings.Contains(prompt, "Issue: 订单支付失败") {
		t.Fatalf("expected confirmation prompt to include draft title and description, got %q", prompt)
	}
	assertPath(t, result.NodePath, []string{"start_1", "draft_1", "draft_route_1", "prompt_1", "confirm_1"})
}

func TestExecutorPolicyFirstWorkflowRoutesGreetingToDirectReply(t *testing.T) {
	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition: policyFirstWorkflowDefinition(),
		UserMessage: models.Message{
			Content: "<p>你好。</p>",
		},
		AIAgent: models.AIAgent{
			FallbackMessage: "我暂时没有找到足够准确的信息。",
		},
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	if result.ReplyText != "您好，请问有什么可以帮您？" {
		t.Fatalf("expected greeting reply, got %q", result.ReplyText)
	}
	if result.RetrieverCount != 0 {
		t.Fatalf("expected greeting to skip retrieval, got retriever count %d", result.RetrieverCount)
	}
	assertPath(t, result.NodePath, []string{"start_1", "understanding_1", "policy_1", "policy_route_1", "send_direct_1", "end_1"})

	understandingTrace := findNodeTrace(result.NodeTraces, "understanding_1")
	if understandingTrace == nil || !strings.Contains(understandingTrace.OutputPreview, `"messageIntent":"greeting"`) || !strings.Contains(understandingTrace.OutputPreview, `"answerScope":"direct_reply"`) {
		t.Fatalf("expected understanding trace to audit greeting/direct_reply, got %#v", understandingTrace)
	}
	policyTrace := findNodeTrace(result.NodeTraces, "policy_1")
	if policyTrace == nil || !strings.Contains(policyTrace.OutputPreview, `"action":"direct_reply"`) || !strings.Contains(policyTrace.OutputPreview, `"finalReplySource":"direct_reply"`) {
		t.Fatalf("expected policy trace to audit direct reply, got %#v", policyTrace)
	}
}

func TestExecutorPolicyFirstWorkflowRoutesBusinessQuestionToKnowledge(t *testing.T) {
	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition: policyFirstWorkflowDefinition(),
		UserMessage: models.Message{
			Content: "你们价格是多少？",
		},
		AIAgent: models.AIAgent{
			FallbackMessage: "我暂时没有找到足够准确的信息。",
		},
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	assertPath(t, result.NodePath, []string{"start_1", "understanding_1", "policy_1", "policy_route_1", "retrieve_end"})
}

func TestExecutorKnowledgeRetrieveRequiresNodeKnowledgeBases(t *testing.T) {
	_, err := NewExecutor().Execute(context.Background(), Input{
		Definition: knowledgeRetrieveWorkflowDefinition(nil),
		UserMessage: models.Message{
			Content: "产品价格",
		},
		AIAgent: models.AIAgent{
			KnowledgeIDs: "1,2",
		},
	})
	if err == nil {
		t.Fatalf("expected knowledge retrieve without node knowledge bases to fail")
	}
	if !strings.Contains(err.Error(), "knowledge retrieve node requires knowledgeBaseIds") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestExecutorLLMReplyUsesAgentFallbackWhenDeclaredKnowledgeIsEmpty(t *testing.T) {
	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition: emptyKnowledgeReplyDefinition(),
		UserMessage: models.Message{
			Content: "产品功能",
		},
		AIAgent: models.AIAgent{
			FallbackMode:    enums.AIAgentFallbackModeNoAnswer,
			FallbackMessage: "我暂时没有找到足够准确的信息。你可以补充更具体的问题，我再继续帮你查。",
			SystemPrompt:    "不要编造事实。",
		},
		AIConfig: models.AIConfig{
			ModelName: "should-not-be-called",
		},
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	if result.ReplyText != "我暂时没有找到足够准确的信息。你可以补充更具体的问题，我再继续帮你查。" {
		t.Fatalf("expected fallback reply, got %q", result.ReplyText)
	}
	assertPath(t, result.NodePath, []string{"start_1", "reply_1", "send_1", "end_1"})
}

func TestExecutorHumanConfirmInterruptsWithCheckpoint(t *testing.T) {
	result, err := NewExecutor().Execute(context.Background(), Input{
		Definition: humanConfirmWorkflowDefinition(),
		Conversation: models.Conversation{
			ID: 11,
		},
		UserMessage: models.Message{
			ID:      22,
			Content: "创建工单",
		},
		AIAgent: models.AIAgent{
			ID: 33,
		},
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	if !result.Interrupted {
		t.Fatalf("expected workflow to interrupt")
	}
	if result.CheckPointID == "" {
		t.Fatalf("expected checkpoint id")
	}
	if len(result.Interrupts) != 1 {
		t.Fatalf("expected one interrupt, got %#v", result.Interrupts)
	}
	if result.Interrupts[0].Type != "human_confirm" || result.Interrupts[0].ID != "confirm_1" {
		t.Fatalf("unexpected interrupt summary: %#v", result.Interrupts[0])
	}
	if !strings.Contains(result.Interrupts[0].InfoPreview, "请确认创建工单") {
		t.Fatalf("expected confirmation prompt, got %q", result.Interrupts[0].InfoPreview)
	}
	assertPath(t, result.NodePath, []string{"start_1", "prompt_1", "confirm_1"})
}

func TestExecutorResumeHumanConfirmContinuesWithConfirmedVariable(t *testing.T) {
	executor := NewExecutor()
	input := Input{
		Definition: humanConfirmWorkflowDefinition(),
		Conversation: models.Conversation{
			ID: 11,
		},
		UserMessage: models.Message{
			ID:      22,
			Content: "创建工单",
		},
		AIAgent: models.AIAgent{
			ID: 33,
		},
	}
	interrupted, err := executor.Execute(context.Background(), input)
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	result, err := executor.Resume(context.Background(), input, interrupted.CheckPointData, "确认")
	if err != nil {
		t.Fatalf("resume workflow: %v", err)
	}
	if result.Interrupted {
		t.Fatalf("expected workflow resume to complete")
	}
	assertPath(t, result.NodePath, []string{"confirm_route_1", "end_1"})
}

func TestExecutorResumeCreatesTicketAfterHumanConfirmation(t *testing.T) {
	db := setupWorkflowExecutorHandoffDB(t)
	aiAgent := createWorkflowExecutorHandoffAIAgent(t, db, "1")
	conversation := createWorkflowExecutorHandoffConversation(t, db, aiAgent.ID)
	userMessage := createWorkflowExecutorCustomerMessage(t, db, conversation.ID, "订单支付失败，请帮我登记工单")
	executor := NewExecutor()

	interrupted, err := executor.Execute(context.Background(), Input{
		Definition:   createTicketWorkflowDefinition(),
		Conversation: conversation,
		UserMessage:  userMessage,
		AIAgent:      aiAgent,
	})
	if err != nil {
		t.Fatalf("execute workflow: %v", err)
	}
	if !interrupted.Interrupted {
		t.Fatalf("expected workflow to interrupt before creating ticket")
	}

	result, err := executor.Resume(context.Background(), Input{
		Definition:   createTicketWorkflowDefinition(),
		Conversation: conversation,
		UserMessage:  userMessage,
		AIAgent:      aiAgent,
	}, interrupted.CheckPointData, "确认")
	if err != nil {
		t.Fatalf("resume workflow: %v", err)
	}
	if result.Interrupted {
		t.Fatalf("expected workflow to complete")
	}
	assertPath(t, result.NodePath, []string{"confirm_route_1", "create_ticket_1", "end_1"})

	var ticket models.Ticket
	if err := db.First(&ticket, "conversation_id = ?", conversation.ID).Error; err != nil {
		t.Fatalf("expected created ticket: %v", err)
	}
	if ticket.Title == "" || !strings.Contains(ticket.Description, "订单支付失败") {
		t.Fatalf("unexpected ticket: %+v", ticket)
	}

	trace := findNodeTrace(result.NodeTraces, "create_ticket_1")
	if trace == nil || !strings.Contains(trace.OutputPreview, "工单已创建") {
		t.Fatalf("expected create_ticket output to include customer-visible result message, got %#v", trace)
	}
	// Replaying the same confirmation checkpoint must reuse the completed
	// business-tool invocation rather than creating a second ticket.
	if _, err := executor.Resume(context.Background(), Input{
		Definition: createTicketWorkflowDefinition(), Conversation: conversation, UserMessage: userMessage, AIAgent: aiAgent,
	}, interrupted.CheckPointData, "确认"); err != nil {
		t.Fatalf("replay workflow resume: %v", err)
	}
	var ticketCount int64
	if err := db.Model(&models.Ticket{}).Where("conversation_id = ?", conversation.ID).Count(&ticketCount).Error; err != nil || ticketCount != 1 {
		t.Fatalf("ticket count after replay = %d, err=%v", ticketCount, err)
	}
}

func TestExecutorDebugResumeDoesNotCreateTicket(t *testing.T) {
	db := setupWorkflowExecutorHandoffDB(t)
	aiAgent := createWorkflowExecutorHandoffAIAgent(t, db, "1")
	conversation := createWorkflowExecutorHandoffConversation(t, db, aiAgent.ID)
	userMessage := createWorkflowExecutorCustomerMessage(t, db, conversation.ID, "订单支付失败，请帮我登记工单")
	executor := NewExecutor()
	definition := createTicketWorkflowDefinition()

	interrupted, err := executor.Execute(context.Background(), Input{Definition: definition, Conversation: conversation, UserMessage: userMessage, AIAgent: aiAgent, Debug: true})
	if err != nil || !interrupted.Interrupted {
		t.Fatalf("debug execute = %#v, err=%v", interrupted, err)
	}
	result, err := executor.Resume(context.Background(), Input{Definition: definition, Conversation: conversation, UserMessage: userMessage, AIAgent: aiAgent, Debug: true}, interrupted.CheckPointData, "确认")
	if err != nil || result.Interrupted {
		t.Fatalf("debug resume = %#v, err=%v", result, err)
	}
	var ticketCount int64
	if err := db.Model(&models.Ticket{}).Where("conversation_id = ?", conversation.ID).Count(&ticketCount).Error; err != nil || ticketCount != 0 {
		t.Fatalf("debug ticket count = %d, err=%v", ticketCount, err)
	}
	trace := findNodeTrace(result.NodeTraces, "create_ticket_1")
	if trace == nil || !strings.Contains(trace.OutputPreview, "调试运行不会创建工单") {
		t.Fatalf("expected debug write skip trace, got %#v", trace)
	}
}

func findNodeTrace(items []NodeTrace, nodeID string) *NodeTrace {
	for i := range items {
		if items[i].NodeID == nodeID {
			return &items[i]
		}
	}
	return nil
}

func emptyKnowledgeReplyDefinition() dsl.Definition {
	return wfTestDefinition(
		[]dsl.Node{
			wfTestNode("start_1", workflowregistry.NodeTypeStart, "Start", nil, nil),
			wfTestNode("reply_1", workflowregistry.NodeTypeLLMReply, "Reply", map[string]dsl.Value{
				"userMessage":    dsl.RefValue("start_1", "userMessage"),
				"knowledgeItems": dsl.RefValue("missing_retrieve", "items"),
			}, nil),
			wfTestNode("send_1", workflowregistry.NodeTypeSendReply, "Send", wfTestInputs("replyText", "reply_1", "replyText"), nil),
			wfTestNode("end_1", workflowregistry.NodeTypeEnd, "End", nil, nil),
		},
		[]dsl.Edge{
			wfTestEdge("start_1", "reply_1", "edge_start_reply"),
			wfTestEdge("reply_1", "send_1", "edge_reply_send"),
			wfTestEdge("send_1", "end_1", "edge_send_end"),
		},
	)
}

func policyFirstWorkflowDefinition() dsl.Definition {
	return wfTestDefinition(
		[]dsl.Node{
			wfTestNode("start_1", workflowregistry.NodeTypeStart, "Start", nil, nil),
			wfTestNode("understanding_1", workflowregistry.NodeTypeConversationUnderstanding, "Understanding", wfTestInputs("userMessage", "start_1", "userMessage"), nil),
			wfTestNode("policy_1", workflowregistry.NodeTypeReplyPolicy, "Policy", map[string]dsl.Value{
				"userMessage":    dsl.RefValue("start_1", "userMessage"),
				"messageIntent":  dsl.RefValue("understanding_1", "messageIntent"),
				"answerScope":    dsl.RefValue("understanding_1", "answerScope"),
				"riskSignals":    dsl.RefValue("understanding_1", "riskSignals"),
				"knowledgeItems": dsl.RefValue("retrieve_1", "items"),
			}, nil),
			wfTestNode("policy_route_1", workflowregistry.NodeTypeCondition, "Policy Route", nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				wfTestConditionBranch("direct", "Direct", "send_direct_1", "policy_1", "action", "eq", "direct_reply"),
				wfTestConditionBranch("knowledge", "Knowledge", "retrieve_end", "policy_1", "action", "eq", "retrieve_knowledge"),
				{ID: "default", Name: "Default", TargetNodeID: "end_1", Default: true},
			}}),
			wfTestNode("send_direct_1", workflowregistry.NodeTypeSendReply, "Send Direct", wfTestInputs("replyText", "policy_1", "replyText"), nil),
			wfTestNode("retrieve_end", workflowregistry.NodeTypeEnd, "Retrieve", nil, nil),
			wfTestNode("end_1", workflowregistry.NodeTypeEnd, "End", nil, nil),
		},
		[]dsl.Edge{
			wfTestEdge("start_1", "understanding_1", "edge_start_understanding"),
			wfTestEdge("understanding_1", "policy_1", "edge_understanding_policy"),
			wfTestEdge("policy_1", "policy_route_1", "edge_policy_route"),
			wfTestEdge("policy_route_1", "send_direct_1", "edge_policy_direct"),
			wfTestEdge("policy_route_1", "retrieve_end", "edge_policy_knowledge"),
			wfTestEdge("policy_route_1", "end_1", "edge_policy_default"),
			wfTestEdge("send_direct_1", "end_1", "edge_send_direct_end"),
		},
	)
}

func conditionalReplyDefinition() dsl.Definition {
	return wfTestDefinition(
		[]dsl.Node{
			wfTestNode("start_1", workflowregistry.NodeTypeStart, "Start", nil, nil),
			wfTestNode("condition_1", workflowregistry.NodeTypeCondition, "Route", nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				wfTestConditionBranch("vip", "VIP", "vip_reply", "start_1", "userMessage", "eq", "vip"),
				{ID: "default", Name: "Default", TargetNodeID: "normal_reply", Default: true},
			}}),
			wfTestNode("vip_reply", workflowregistry.NodeTypeLLMReply, "VIP", nil, map[string]any{"staticReply": "VIP reply"}),
			wfTestNode("normal_reply", workflowregistry.NodeTypeLLMReply, "Normal", nil, map[string]any{"staticReply": "Normal reply"}),
			wfTestNode("send_vip", workflowregistry.NodeTypeSendReply, "Send VIP", wfTestInputs("replyText", "vip_reply", "replyText"), nil),
			wfTestNode("send_normal", workflowregistry.NodeTypeSendReply, "Send Normal", wfTestInputs("replyText", "normal_reply", "replyText"), nil),
			wfTestNode("end_1", workflowregistry.NodeTypeEnd, "End", nil, nil),
		},
		[]dsl.Edge{
			wfTestEdge("start_1", "condition_1", "edge_start_condition"),
			wfTestEdge("condition_1", "vip_reply", "edge_condition_vip"),
			wfTestEdge("condition_1", "normal_reply", "edge_condition_default"),
			wfTestEdge("vip_reply", "send_vip", "edge_vip_send"),
			wfTestEdge("normal_reply", "send_normal", "edge_normal_send"),
			wfTestEdge("send_vip", "end_1", "edge_send_vip_end"),
			wfTestEdge("send_normal", "end_1", "edge_send_normal_end"),
		},
	)
}

func knowledgeRetrieveWorkflowDefinition(config any) dsl.Definition {
	return wfTestDefinition(
		[]dsl.Node{
			wfTestNode("start_1", workflowregistry.NodeTypeStart, "Start", nil, nil),
			wfTestNode("retrieve_1", workflowregistry.NodeTypeKnowledgeRetrieve, "Retrieve", wfTestInputs("query", "start_1", "userMessage"), config),
			wfTestNode("end_1", workflowregistry.NodeTypeEnd, "End", nil, nil),
		},
		[]dsl.Edge{
			wfTestEdge("start_1", "retrieve_1", "edge_start_retrieve"),
			wfTestEdge("retrieve_1", "end_1", "edge_retrieve_end"),
		},
	)
}

func ticketDraftReadyWorkflowDefinition() dsl.Definition {
	return wfTestDefinition(
		[]dsl.Node{
			wfTestNode("start_1", workflowregistry.NodeTypeStart, "Start", nil, nil),
			wfTestNode("draft_1", workflowregistry.NodeTypePrepareTicketDraft, "Draft", wfTestInputs("issue", "start_1", "userMessage"), nil),
			wfTestNode("draft_route_1", workflowregistry.NodeTypeCondition, "Draft Route", nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				wfTestConditionBranch("ready", "Ready", "prompt_1", "draft_1", "ready", "is_true", nil),
				{ID: "default", Name: "Need More Info", TargetNodeID: "followup_1", Default: true},
			}}),
			wfTestNode("prompt_1", workflowregistry.NodeTypeLLMReply, "Prompt", map[string]dsl.Value{
				"userMessage":       dsl.RefValue("start_1", "userMessage"),
				"ticketTitle":       dsl.RefValue("draft_1", "title"),
				"ticketDescription": dsl.RefValue("draft_1", "description"),
			}, map[string]any{"staticReply": "请确认创建工单：\n标题：{{ticketTitle}}\n描述：{{ticketDescription}}"}),
			wfTestNode("confirm_1", workflowregistry.NodeTypeHumanConfirm, "Confirm", wfTestInputs("prompt", "prompt_1", "replyText"), nil),
			wfTestNode("followup_1", workflowregistry.NodeTypeLLMReply, "Follow Up", map[string]dsl.Value{
				"userMessage":       dsl.RefValue("start_1", "userMessage"),
				"followUpQuestions": dsl.RefValue("draft_1", "followUpQuestions"),
			}, map[string]any{"staticReply": "{{followUpQuestions}}"}),
			wfTestNode("send_followup_1", workflowregistry.NodeTypeSendReply, "Send Follow Up", wfTestInputs("replyText", "followup_1", "replyText"), nil),
			wfTestNode("end_1", workflowregistry.NodeTypeEnd, "End", nil, nil),
		},
		[]dsl.Edge{
			wfTestEdge("start_1", "draft_1", "edge_start_draft"),
			wfTestEdge("draft_1", "draft_route_1", "edge_draft_route"),
			wfTestEdge("draft_route_1", "prompt_1", "ready"),
			wfTestEdge("draft_route_1", "followup_1", "default"),
			wfTestEdge("prompt_1", "confirm_1", "edge_prompt_confirm"),
			wfTestEdge("followup_1", "send_followup_1", "edge_followup_send"),
			wfTestEdge("send_followup_1", "end_1", "edge_followup_end"),
		},
	)
}

func createTicketWorkflowDefinition() dsl.Definition {
	return wfTestDefinition(
		[]dsl.Node{
			wfTestNode("start_1", workflowregistry.NodeTypeStart, "Start", nil, nil),
			wfTestNode("draft_1", workflowregistry.NodeTypePrepareTicketDraft, "Draft", wfTestInputs("issue", "start_1", "userMessage"), nil),
			wfTestNode("prompt_1", workflowregistry.NodeTypeLLMReply, "Prompt", nil, map[string]any{"staticReply": "请确认创建工单"}),
			wfTestNode("confirm_1", workflowregistry.NodeTypeHumanConfirm, "Confirm", wfTestInputs("prompt", "prompt_1", "replyText"), nil),
			wfTestNode("confirm_route_1", workflowregistry.NodeTypeCondition, "Confirm Route", nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				wfTestConditionBranch("yes", "Yes", "create_ticket_1", "confirm_1", "confirmed", "is_true", nil),
				{ID: "default", Name: "Cancel", TargetNodeID: "cancel_end", Default: true},
			}}),
			wfTestNode("create_ticket_1", workflowregistry.NodeTypeCreateTicket, "Create Ticket", map[string]dsl.Value{
				"ticketDraft": dsl.RefValue("draft_1", "ticketDraft"),
				"confirmed":   dsl.RefValue("confirm_1", "confirmed"),
			}, nil),
			wfTestNode("end_1", workflowregistry.NodeTypeEnd, "End", nil, nil),
			wfTestNode("cancel_end", workflowregistry.NodeTypeEnd, "Cancel", nil, nil),
		},
		[]dsl.Edge{
			wfTestEdge("start_1", "draft_1", "edge_start_draft"),
			wfTestEdge("draft_1", "prompt_1", "edge_draft_prompt"),
			wfTestEdge("prompt_1", "confirm_1", "edge_prompt_confirm"),
			wfTestEdge("confirm_1", "confirm_route_1", "edge_confirm_route"),
			wfTestEdge("confirm_route_1", "create_ticket_1", "edge_confirm_create"),
			wfTestEdge("confirm_route_1", "cancel_end", "edge_confirm_cancel"),
			wfTestEdge("create_ticket_1", "end_1", "edge_create_end"),
		},
	)
}

func humanConfirmWorkflowDefinition() dsl.Definition {
	return wfTestDefinition(
		[]dsl.Node{
			wfTestNode("start_1", workflowregistry.NodeTypeStart, "Start", nil, nil),
			wfTestNode("prompt_1", workflowregistry.NodeTypeLLMReply, "Prompt", nil, map[string]any{"staticReply": "请确认创建工单"}),
			wfTestNode("confirm_1", workflowregistry.NodeTypeHumanConfirm, "Confirm", wfTestInputs("prompt", "prompt_1", "replyText"), nil),
			wfTestNode("confirm_route_1", workflowregistry.NodeTypeCondition, "Confirm Route", nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				wfTestConditionBranch("yes", "Yes", "end_1", "confirm_1", "confirmed", "is_true", nil),
				{ID: "default", Name: "Cancel", TargetNodeID: "cancel_end", Default: true},
			}}),
			wfTestNode("end_1", workflowregistry.NodeTypeEnd, "End", nil, nil),
			wfTestNode("cancel_end", workflowregistry.NodeTypeEnd, "Cancel", nil, nil),
		},
		[]dsl.Edge{
			wfTestEdge("start_1", "prompt_1", "edge_start_prompt"),
			wfTestEdge("prompt_1", "confirm_1", "edge_prompt_confirm"),
			wfTestEdge("confirm_1", "confirm_route_1", "edge_confirm_route"),
			wfTestEdge("confirm_route_1", "end_1", "edge_confirm_yes"),
			wfTestEdge("confirm_route_1", "cancel_end", "edge_confirm_cancel"),
		},
	)
}

func prepareTicketDraftWorkflowDefinition() dsl.Definition {
	return wfTestDefinition(
		[]dsl.Node{
			wfTestNode("start_1", workflowregistry.NodeTypeStart, "Start", nil, nil),
			wfTestNode("draft_1", workflowregistry.NodeTypePrepareTicketDraft, "Draft", wfTestInputs("issue", "start_1", "userMessage"), nil),
			wfTestNode("draft_route_1", workflowregistry.NodeTypeCondition, "Draft Route", nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				wfTestConditionBranch("ready", "Ready", "ready_end", "draft_1", "ticketDraft", "exists", nil),
				{ID: "default", Name: "Default", TargetNodeID: "default_end", Default: true},
			}}),
			wfTestNode("ready_end", workflowregistry.NodeTypeEnd, "Ready", nil, nil),
			wfTestNode("default_end", workflowregistry.NodeTypeEnd, "Default", nil, nil),
		},
		[]dsl.Edge{
			wfTestEdge("start_1", "draft_1", "edge_start_draft"),
			wfTestEdge("draft_1", "draft_route_1", "edge_draft_route"),
			wfTestEdge("draft_route_1", "ready_end", "edge_draft_ready"),
			wfTestEdge("draft_route_1", "default_end", "edge_draft_default"),
		},
	)
}

func analyzeConversationWorkflowDefinition() dsl.Definition {
	return wfTestDefinition(
		[]dsl.Node{
			wfTestNode("start_1", workflowregistry.NodeTypeStart, "Start", nil, nil),
			wfTestNode("analyze_1", workflowregistry.NodeTypeAnalyzeConversation, "Analyze", wfTestInputs("userMessage", "start_1", "userMessage"), nil),
			wfTestNode("analyze_route_1", workflowregistry.NodeTypeCondition, "Analyze Route", nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				wfTestConditionBranch("handoff", "Handoff", "handoff_end", "analyze_1", "needHumanHandoff", "is_true", nil),
				{ID: "default", Name: "Default", TargetNodeID: "default_end", Default: true},
			}}),
			wfTestNode("handoff_end", workflowregistry.NodeTypeEnd, "Handoff", nil, nil),
			wfTestNode("default_end", workflowregistry.NodeTypeEnd, "Default", nil, nil),
		},
		[]dsl.Edge{
			wfTestEdge("start_1", "analyze_1", "edge_start_analyze"),
			wfTestEdge("analyze_1", "analyze_route_1", "edge_analyze_route"),
			wfTestEdge("analyze_route_1", "handoff_end", "edge_analyze_handoff"),
			wfTestEdge("analyze_route_1", "default_end", "edge_analyze_default"),
		},
	)
}

func handoffWorkflowDefinition() dsl.Definition {
	return wfTestDefinition(
		[]dsl.Node{
			wfTestNode("start_1", workflowregistry.NodeTypeStart, "Start", nil, nil),
			wfTestNode("handoff_1", workflowregistry.NodeTypeHandoffToHuman, "Handoff", wfTestInputs("reason", "start_1", "userMessage"), nil),
			wfTestNode("handoff_route_1", workflowregistry.NodeTypeCondition, "Handoff Route", nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				wfTestConditionBranch("assigned", "Assigned", "assigned_end", "handoff_1", "decision", "eq", string(services.HandoffDecisionAssigned)),
				{ID: "default", Name: "Default", TargetNodeID: "default_end", Default: true},
			}}),
			wfTestNode("assigned_end", workflowregistry.NodeTypeEnd, "Assigned", nil, nil),
			wfTestNode("default_end", workflowregistry.NodeTypeEnd, "Default", nil, nil),
		},
		[]dsl.Edge{
			wfTestEdge("start_1", "handoff_1", "edge_start_handoff"),
			wfTestEdge("handoff_1", "handoff_route_1", "edge_handoff_route"),
			wfTestEdge("handoff_route_1", "assigned_end", "edge_handoff_assigned"),
			wfTestEdge("handoff_route_1", "default_end", "edge_handoff_default"),
		},
	)
}

func handoffAfterConfirmationWorkflowDefinition() dsl.Definition {
	return wfTestDefinition(
		[]dsl.Node{
			wfTestNode("start_1", workflowregistry.NodeTypeStart, "Start", nil, nil),
			wfTestNode("prompt_1", workflowregistry.NodeTypeLLMReply, "Prompt", nil, map[string]any{"staticReply": "请确认转人工"}),
			wfTestNode("confirm_1", workflowregistry.NodeTypeHumanConfirm, "Confirm", wfTestInputs("prompt", "prompt_1", "replyText"), nil),
			wfTestNode("handoff_1", workflowregistry.NodeTypeHandoffToHuman, "Handoff", map[string]dsl.Value{
				"reason":    dsl.RefValue("start_1", "userMessage"),
				"confirmed": dsl.RefValue("confirm_1", "confirmed"),
			}, nil),
			wfTestNode("end_1", workflowregistry.NodeTypeEnd, "End", nil, nil),
		},
		[]dsl.Edge{
			wfTestEdge("start_1", "prompt_1", "edge_start_prompt"),
			wfTestEdge("prompt_1", "confirm_1", "edge_prompt_confirm"),
			wfTestEdge("confirm_1", "handoff_1", "edge_confirm_handoff"),
			wfTestEdge("handoff_1", "end_1", "edge_handoff_end"),
		},
	)
}

func wfTestDefinition(nodes []dsl.Node, edges []dsl.Edge) dsl.Definition {
	return dsl.Definition{SchemaVersion: dsl.SchemaVersion, Nodes: nodes, Edges: edges}
}

func wfTestNode(id string, nodeType string, title string, inputs map[string]dsl.Value, config any) dsl.Node {
	return dsl.Node{
		ID:   id,
		Type: nodeType,
		Meta: dsl.NodeMeta{Position: dsl.Position{X: 0, Y: 0}},
		Data: dsl.NodeData{
			Title:        title,
			InputsValues: inputs,
			Config:       mustMarshalWorkflowTestConfig(config),
		},
	}
}

func wfTestInputs(name string, nodeID string, field string) map[string]dsl.Value {
	return map[string]dsl.Value{name: dsl.RefValue(nodeID, field)}
}

func wfTestEdge(source string, target string, id string) dsl.Edge {
	return dsl.Edge{SourceNodeID: source, TargetNodeID: target, SourcePortID: id}
}

func wfTestConditionBranch(id string, name string, targetNodeID string, nodeID string, field string, operator string, right any) dsl.ConditionBranch {
	return dsl.ConditionBranch{
		ID:           id,
		Name:         name,
		TargetNodeID: targetNodeID,
		Condition: &dsl.Condition{
			Left:     &dsl.Value{Type: dsl.ValueTypeRef, Content: []string{nodeID, field}},
			Operator: operator,
			Right:    right,
		},
	}
}

func setupWorkflowExecutorHandoffDB(t *testing.T) *gorm.DB {
	t.Helper()
	dbName := strings.NewReplacer("/", "_", " ", "_").Replace(t.Name())
	db, err := gorm.Open(sqlite.Open("file:"+dbName+"?mode=memory&cache=shared"), &gorm.Config{
		NamingStrategy: schema.NamingStrategy{
			TablePrefix:   "t_",
			SingularTable: true,
		},
	})
	if err != nil {
		t.Fatalf("open sqlite error = %v", err)
	}
	t.Cleanup(func() {
		sqlDB, err := db.DB()
		if err == nil {
			_ = sqlDB.Close()
		}
	})
	if err := db.AutoMigrate(
		&models.User{},
		&models.Customer{},
		&models.CustomerIdentity{},
		&models.AIAgent{},
		&models.AgentTeam{},
		&models.AgentTeamSchedule{},
		&models.AgentProfile{},
		&models.Channel{},
		&models.Conversation{},
		&models.ConversationAssignment{},
		&models.ConversationEventLog{},
		&models.ConversationReadState{},
		&models.Message{},
		&models.ChannelMessageOutbox{},
		&models.AgentToolInvocation{},
		&models.Ticket{},
		&models.TicketNoSequence{},
		&models.TicketTag{},
		&models.TicketProgress{},
	); err != nil {
		t.Fatalf("auto migrate error = %v", err)
	}
	sqls.SetDB(db)
	return db
}

func createWorkflowExecutorHandoffAIAgent(t *testing.T, db *gorm.DB, teamIDs string) models.AIAgent {
	t.Helper()
	item := models.AIAgent{
		Name:        "测试AI",
		ServiceMode: enums.IMConversationServiceModeAIFirst,
		TeamIDs:     teamIDs,
		Status:      enums.StatusOk,
	}
	if err := db.Create(&item).Error; err != nil {
		t.Fatalf("create ai agent error = %v", err)
	}
	return item
}

func createWorkflowExecutorHandoffTeam(t *testing.T, db *gorm.DB, id int64, name string) {
	t.Helper()
	if err := db.Create(&models.AgentTeam{ID: id, Name: name, Status: enums.StatusOk}).Error; err != nil {
		t.Fatalf("create team error = %v", err)
	}
}

func createWorkflowExecutorHandoffActiveSchedule(t *testing.T, db *gorm.DB, teamID int64) {
	t.Helper()
	now := time.Now()
	if err := db.Create(&models.AgentTeamSchedule{
		TeamID:  teamID,
		StartAt: now.Add(-time.Hour),
		EndAt:   now.Add(time.Hour),
		Status:  enums.StatusOk,
	}).Error; err != nil {
		t.Fatalf("create schedule error = %v", err)
	}
}

func createWorkflowExecutorHandoffAgentProfile(t *testing.T, db *gorm.DB, userID int64, teamID int64) {
	t.Helper()
	if err := db.Create(&models.User{
		ID:       userID,
		Username: "agent",
		Nickname: "客服",
		Status:   enums.StatusOk,
	}).Error; err != nil {
		t.Fatalf("create user error = %v", err)
	}
	if err := db.Create(&models.AgentProfile{
		UserID:             userID,
		TeamID:             teamID,
		AgentCode:          "A001",
		DisplayName:        "客服",
		ServiceStatus:      enums.ServiceStatusIdle,
		MaxConcurrentCount: 3,
		AutoAssignEnabled:  true,
		Status:             enums.StatusOk,
	}).Error; err != nil {
		t.Fatalf("create profile error = %v", err)
	}
}

func createWorkflowExecutorHandoffConversation(t *testing.T, db *gorm.DB, aiAgentID int64) models.Conversation {
	t.Helper()
	now := time.Now()
	if err := db.FirstOrCreate(&models.Customer{
		ID:     1,
		Name:   "测试访客",
		Status: enums.StatusOk,
	}).Error; err != nil {
		t.Fatalf("create customer error = %v", err)
	}
	item := models.Conversation{
		AIAgentID:     aiAgentID,
		ChannelID:     1,
		CustomerID:    1,
		CustomerName:  "测试访客",
		Status:        enums.IMConversationStatusAIServing,
		ServiceMode:   enums.IMConversationServiceModeAIFirst,
		LastMessageAt: now,
		LastActiveAt:  now,
	}
	if err := db.Create(&item).Error; err != nil {
		t.Fatalf("create conversation error = %v", err)
	}
	return item
}

func createWorkflowExecutorCustomerMessage(t *testing.T, db *gorm.DB, conversationID int64, content string) models.Message {
	t.Helper()
	now := time.Now()
	item := models.Message{
		ConversationID: conversationID,
		ClientMsgID:    "customer-message",
		SenderType:     enums.IMSenderTypeCustomer,
		MessageType:    enums.IMMessageTypeText,
		Content:        content,
		SendStatus:     enums.IMMessageStatusSent,
		SentAt:         &now,
	}
	if err := db.Create(&item).Error; err != nil {
		t.Fatalf("create message error = %v", err)
	}
	return item
}

func assertPath(t *testing.T, got []string, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("unexpected path length: got %#v want %#v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("unexpected path: got %#v want %#v", got, want)
		}
	}
}
