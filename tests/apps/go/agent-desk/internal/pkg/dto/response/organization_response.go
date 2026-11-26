package response

import (
	"agent-desk/internal/pkg/enums"
	"time"
)

type OrganizationResponse struct {
	ID        int64        `json:"id"`
	Code      string       `json:"code"`
	Name      string       `json:"name"`
	Logo      string       `json:"logo"`
	Plan      string       `json:"plan"`
	Status    enums.Status `json:"status"`
	Role      string       `json:"role,omitempty"`
	IsActive  bool         `json:"isActive"`
	CreatedAt time.Time    `json:"createdAt"`
}

type UserOrganizationListResponse struct {
	CurrentOrganizationID int64                  `json:"currentOrganizationId"`
	Organizations         []OrganizationResponse `json:"organizations"`
}
