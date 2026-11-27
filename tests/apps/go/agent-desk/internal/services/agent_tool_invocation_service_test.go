package services

import (
	"strings"
	"testing"

	"agent-desk/internal/models"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func TestAgentToolInvocationServiceReusesCompletedInvocation(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+strings.ReplaceAll(t.Name(), "/", "_")+"?mode=memory&cache=shared"), &gorm.Config{NamingStrategy: schema.NamingStrategy{TablePrefix: "t_", SingularTable: true}})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.AgentToolInvocation{}); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)

	first, err := AgentToolInvocationService.Claim(10, 20, "graph/create_ticket_with_confirmation", "message:30:node:create")
	if err != nil || first == nil || first.Item == nil || first.Completed {
		t.Fatalf("first claim = %#v, err=%v", first, err)
	}
	if err := AgentToolInvocationService.Complete(first.Item, `{"ticketId":40}`); err != nil {
		t.Fatalf("complete invocation: %v", err)
	}
	second, err := AgentToolInvocationService.Claim(10, 20, "graph/create_ticket_with_confirmation", "message:30:node:create")
	if err != nil || second == nil || !second.Completed || second.Item.ResultData != `{"ticketId":40}` {
		t.Fatalf("second claim = %#v, err=%v", second, err)
	}
}

func TestAgentToolInvocationServiceAllowsFailedInvocationRetry(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+strings.ReplaceAll(t.Name(), "/", "_")+"?mode=memory&cache=shared"), &gorm.Config{NamingStrategy: schema.NamingStrategy{TablePrefix: "t_", SingularTable: true}})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.AgentToolInvocation{}); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)

	first, err := AgentToolInvocationService.Claim(11, 21, "graph/handoff_to_human", "message:31:node:handoff")
	if err != nil {
		t.Fatalf("first claim: %v", err)
	}
	if err := AgentToolInvocationService.Fail(first.Item, errTestToolInvocation); err != nil {
		t.Fatalf("fail invocation: %v", err)
	}
	second, err := AgentToolInvocationService.Claim(11, 21, "graph/handoff_to_human", "message:31:node:handoff")
	if err != nil || second == nil || second.Completed || second.Item.Status != agentToolInvocationStatusRunning || second.Item.ErrorMessage != "" {
		t.Fatalf("retry claim = %#v, err=%v", second, err)
	}
}

var errTestToolInvocation = &toolInvocationTestError{}

type toolInvocationTestError struct{}

func (e *toolInvocationTestError) Error() string { return "tool failed" }
