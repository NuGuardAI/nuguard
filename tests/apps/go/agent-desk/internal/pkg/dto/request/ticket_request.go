package request

type CreateTicketRequest struct {
	Title             string  `json:"title"`
	Description       string  `json:"description"`
	Source            string  `json:"source"`
	Channel           string  `json:"channel"`
	CustomerID        int64   `json:"customerId"`
	ConversationID    int64   `json:"conversationId"`
	TagIDs            []int64 `json:"tagIds"`
	CurrentAssigneeID int64   `json:"currentAssigneeId"`
}

type CreateTicketFromConversationRequest struct {
	ConversationID    int64   `json:"conversationId"`
	Title             string  `json:"title"`
	Description       string  `json:"description"`
	TagIDs            []int64 `json:"tagIds"`
	CurrentAssigneeID int64   `json:"currentAssigneeId"`
}

type UpdateTicketRequest struct {
	TicketID          int64   `json:"ticketId"`
	Title             string  `json:"title"`
	Description       string  `json:"description"`
	TagIDs            []int64 `json:"tagIds"`
	CurrentAssigneeID int64   `json:"currentAssigneeId"`
}

type LinkTicketCustomerRequest struct {
	TicketID   int64 `json:"ticketId"`
	CustomerID int64 `json:"customerId"`
}

type AssignTicketRequest struct {
	TicketID int64  `json:"ticketId"`
	ToUserID int64  `json:"toUserId"`
	Reason   string `json:"reason"`
}

type ChangeTicketStatusRequest struct {
	TicketID int64  `json:"ticketId"`
	Status   string `json:"status"`
}

type CreateTicketProgressRequest struct {
	TicketID int64  `json:"ticketId"`
	Content  string `json:"content"`
}

type SaveTicketViewRequest struct {
	ID      int64          `json:"id"`
	Name    string         `json:"name"`
	Filters map[string]any `json:"filters"`
}

type DeleteTicketViewRequest struct {
	ID int64 `json:"id"`
}
