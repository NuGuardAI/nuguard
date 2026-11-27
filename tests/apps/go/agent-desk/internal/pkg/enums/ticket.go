package enums

type TicketStatus string

const (
	TicketStatusPending    TicketStatus = "pending"
	TicketStatusInProgress TicketStatus = "in_progress"
	TicketStatusDone       TicketStatus = "done"
)

var TicketStatusValues = []TicketStatus{
	TicketStatusPending,
	TicketStatusInProgress,
	TicketStatusDone,
}

var ticketStatusLabelMap = map[TicketStatus]string{
	TicketStatusPending:    "待处理",
	TicketStatusInProgress: "处理中",
	TicketStatusDone:       "已处理",
}

func GetTicketStatusLabel(status TicketStatus) string {
	return ticketStatusLabelMap[status]
}

func IsValidTicketStatus(status string) bool {
	for _, item := range TicketStatusValues {
		if string(item) == status {
			return true
		}
	}
	return false
}

type TicketSource string

const (
	TicketSourceManual       TicketSource = "manual"
	TicketSourceConversation TicketSource = "conversation"
)

var TicketSourceValues = []TicketSource{
	TicketSourceManual,
	TicketSourceConversation,
}

func IsValidTicketSource(source string) bool {
	for _, item := range TicketSourceValues {
		if string(item) == source {
			return true
		}
	}
	return false
}
