package runtime

import (
	"testing"

	applicationruntime "agent-desk/internal/ai/application/runtime"
	"agent-desk/internal/models"
)

func TestExtractInterruptMessageAndCheckpointError(t *testing.T) {
	if got := extractInterruptMessage(`{"message":"请补充订单号"}`); got != "请补充订单号" {
		t.Fatalf("unexpected interrupt message: %q", got)
	}
	if got := extractInterruptMessage("not-json"); got != "" {
		t.Fatalf("expected empty message for invalid json, got %q", got)
	}

	err := fakeErr("Failed to load from checkpoint: record does not exist")
	if !isCheckpointMissingError(err) {
		t.Fatalf("expected checkpoint missing error to be detected")
	}
	if isCheckpointMissingError(fakeErr("other error")) {
		t.Fatalf("expected unrelated error to be ignored")
	}
}

func TestBuildConversationInterruptStoresWorkflowCheckpointData(t *testing.T) {
	item := buildConversationInterrupt(testConversation(1), testMessage(2), testAIAgent(3), &applicationruntime.RunResult{
		CheckPointData: `{"confirmNodeId":"confirm_1"}`,
		Interrupted:    true,
		WorkflowRunID:  99,
		AgentRunID:     88,
		Interrupts: []applicationruntime.InterruptContextSummary{
			{Type: "human_confirm", ID: "confirm_1", InfoPreview: `{"message":"请确认"}`},
		},
	})
	if item == nil {
		t.Fatalf("expected interrupt item")
	}
	if item.RequestData != `{"confirmNodeId":"confirm_1"}` {
		t.Fatalf("unexpected request data: %q", item.RequestData)
	}
	if item.WorkflowRunID != 99 || item.AgentRunID != 88 || item.WorkflowNodeID != "confirm_1" {
		t.Fatalf("unexpected workflow interrupt identity: run=%d node=%q", item.WorkflowRunID, item.WorkflowNodeID)
	}
}

type fakeErr string

func (e fakeErr) Error() string {
	return string(e)
}

func testConversation(id int64) models.Conversation {
	return models.Conversation{ID: id}
}

func testMessage(id int64) models.Message {
	return models.Message{ID: id}
}

func testAIAgent(id int64) models.AIAgent {
	return models.AIAgent{ID: id}
}
