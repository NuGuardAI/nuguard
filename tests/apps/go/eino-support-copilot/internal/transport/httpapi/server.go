package httpapi

import (
	"context"
	"net/http"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/conversation"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/evaluation"
	"github.com/guiyangyuan/eino-support-copilot/internal/knowledge"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
	"github.com/guiyangyuan/eino-support-copilot/internal/ticket"
)

type KnowledgeService interface {
	Upload(context.Context, knowledge.Upload) (domain.KnowledgeDocument, error)
	List(context.Context) ([]domain.KnowledgeDocument, error)
}

type ConversationService interface {
	Create(context.Context, uuid.UUID, string) (domain.Conversation, error)
	List(context.Context, uuid.UUID) ([]domain.Conversation, error)
	CanAccess(context.Context, uuid.UUID, uuid.UUID) error
	Send(context.Context, uuid.UUID, uuid.UUID, string, runtrace.Sink) (conversation.SendResult, error)
}

type TicketService interface {
	CreateFromConversation(context.Context, uuid.UUID) (domain.Ticket, error)
	GetDetail(context.Context, uuid.UUID) (ticket.Detail, error)
	List(context.Context, ticket.ListFilter) ([]domain.Ticket, error)
	Review(context.Context, ticket.ReviewCommand) (domain.TicketReview, error)
}

type RunService interface {
	Get(context.Context, uuid.UUID, uuid.UUID, string) (domain.AgentRun, error)
	Replay(context.Context, uuid.UUID, uuid.UUID, string) (domain.AgentRun, error)
}

type EvaluationService interface {
	Start(context.Context) (evaluation.RunResult, error)
	Get(context.Context, uuid.UUID) (evaluation.RunResult, error)
}

type IdentityResolver interface {
	Resolve(context.Context, string) (uuid.UUID, string, error)
}

type Dependencies struct {
	Mode          string
	Knowledge     KnowledgeService
	Conversations ConversationService
	Tickets       TicketService
	Runs          RunService
	Evaluations   EvaluationService
	Identity      IdentityResolver
}

func NewServer(deps Dependencies) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{
			"status": "ok",
			"mode":   deps.Mode,
		})
	})
	mux.HandleFunc("POST /api/v1/knowledge/documents", uploadKnowledgeDocument(deps.Knowledge))
	mux.HandleFunc("GET /api/v1/knowledge/documents", listKnowledgeDocuments(deps.Knowledge))
	mux.HandleFunc("POST /api/v1/conversations", createConversation(deps.Conversations, deps.Identity))
	mux.HandleFunc("GET /api/v1/conversations", listConversations(deps.Conversations, deps.Identity))
	mux.HandleFunc("POST /api/v1/conversations/{id}/messages:stream", streamConversationMessage(deps.Conversations, deps.Identity))
	mux.HandleFunc("POST /api/v1/conversations/{id}/tickets", createTicket(deps.Tickets, deps.Conversations, deps.Identity))
	mux.HandleFunc("GET /api/v1/tickets", listTickets(deps.Tickets, deps.Identity))
	mux.HandleFunc("GET /api/v1/tickets/{id}", getTicket(deps.Tickets, deps.Identity))
	mux.HandleFunc("POST /api/v1/tickets/{id}/review", reviewTicket(deps.Tickets, deps.Identity))
	mux.HandleFunc("GET /api/v1/runs/{id}", getRun(deps.Runs, deps.Identity))
	mux.HandleFunc("POST /api/v1/runs/{action}", replayRun(deps.Runs, deps.Identity))
	mux.HandleFunc("POST /api/v1/evaluations", startEvaluation(deps.Evaluations, deps.Identity))
	mux.HandleFunc("GET /api/v1/evaluations/{id}", getEvaluation(deps.Evaluations, deps.Identity))
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		WriteError(w, r, http.StatusNotFound, "NOT_FOUND", "route not found", false)
	})

	return withRequestID(mux)
}
