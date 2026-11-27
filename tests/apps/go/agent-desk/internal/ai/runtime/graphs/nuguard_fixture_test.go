// NuGuard fixture: deterministic authorization/confirmation probes; no model is called.
package graphs

import (
	"context"
	"strings"
	"testing"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/openidentity"
	"agent-desk/internal/services"
)

func TestNuGuardCustomerCannotSendToAnotherConversation(t *testing.T) {
	db := setupHandoffGraphTestDB(t)
	if err := db.AutoMigrate(&models.CustomerIdentity{}); err != nil {
		t.Fatal(err)
	}
	identity := models.CustomerIdentity{CustomerID: 1, ExternalSource: enums.ExternalSourceExternal, ExternalID: "fixture-ada"}
	if err := db.Create(&identity).Error; err != nil {
		t.Fatal(err)
	}
	caller := openidentity.ExternalUser{ExternalSource: enums.ExternalSourceExternal, ExternalID: "fixture-ada"}
	for _, customerID := range []int64{1, 2} {
		conversation := models.Conversation{CustomerID: customerID, Status: enums.IMConversationStatusAIServing}
		if err := db.Create(&conversation).Error; err != nil {
			t.Fatal(err)
		}
		var before, after int64
		if err := db.Model(&models.Message{}).Count(&before).Error; err != nil {
			t.Fatal(err)
		}
		validated, err := services.MessageService.ValidateConversationSender(conversation.ID, enums.IMSenderTypeCustomer, nil, &caller)
		if customerID == 1 && (err != nil || validated == nil) {
			t.Fatalf("legitimate owner rejected: %v", err)
		}
		if customerID == 2 && (err == nil || validated != nil) {
			t.Fatalf("other customer's conversation accepted: %v", err)
		}
		if err := db.Model(&models.Message{}).Count(&after).Error; err != nil {
			t.Fatal(err)
		}
		if before != after {
			t.Fatalf("validation mutated messages: %d -> %d", before, after)
		}
		t.Logf("direct_service_probe: caller_customer=1 target_customer=%d allowed=%t messages_before=%d messages_after=%d model_calls=0", customerID, err == nil, before, after)
	}
}

func TestNuGuardTicketArgumentsCannotOverrideConversation(t *testing.T) {
	graph := NewCreateTicketGraph(models.Conversation{ID: 101, CustomerID: 1}, models.AIAgent{})
	request, err := graph.buildCreateRequest(`{"title":"fixture","description":"control","conversationId":202,"conversation_id":202,"customerId":2,"customer_id":2}`)
	if err != nil {
		t.Fatal(err)
	}
	if request.ConversationID != 101 {
		t.Fatalf("model arguments changed bound conversation: %d", request.ConversationID)
	}
	t.Logf("direct_graph_probe: requested_conversation=202 bound_conversation=%d; model_calls=0", request.ConversationID)
}

func TestNuGuardTicketRequiresConfirmationWithoutDatabaseWrite(t *testing.T) {
	db := setupHandoffGraphTestDB(t)
	if err := db.AutoMigrate(&models.Ticket{}); err != nil {
		t.Fatal(err)
	}
	var before, after int64
	if err := db.Model(&models.Ticket{}).Count(&before).Error; err != nil {
		t.Fatal(err)
	}
	graph := NewCreateTicketGraph(models.Conversation{ID: 101, CustomerID: 1}, models.AIAgent{})
	response, err := graph.Run(context.Background(), `{"title":"fixture","description":"create without confirmation","confirmed":true}`)
	if err == nil || !strings.Contains(err.Error(), InterruptTypeTicketCreationConfirmation) {
		t.Fatalf("expected confirmation interrupt, response=%q error=%v", response, err)
	}
	if response != "" {
		t.Fatalf("unexpected pre-confirmation response: %q", response)
	}
	if err := db.Model(&models.Ticket{}).Count(&after).Error; err != nil {
		t.Fatal(err)
	}
	if before != after {
		t.Fatalf("ticket count changed without confirmation: %d -> %d", before, after)
	}
	t.Logf("direct_graph_probe: response=%q confirmation_interrupt=true tickets_before=%d tickets_after=%d model_calls=0", response, before, after)
}
