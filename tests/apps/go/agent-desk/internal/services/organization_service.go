package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
)

var OrganizationService = newOrganizationService()

func newOrganizationService() *organizationService {
	return &organizationService{}
}

type organizationService struct{}

func (s *organizationService) GetUserOrganizations(userID int64) (*response.UserOrganizationListResponse, error) {
	user := repositories.UserRepository.Get(sqls.DB(), userID)
	if user == nil {
		return nil, errorsx.InvalidAccountI18n("error.e0260")
	}

	memberships := repositories.OrganizationMemberRepository.Find(sqls.DB(), sqls.NewCnd().Eq("user_id", userID).Eq("status", enums.StatusOk))
	if len(memberships) == 0 {
		return &response.UserOrganizationListResponse{
			CurrentOrganizationID: user.ActiveOrgID,
			Organizations:         []response.OrganizationResponse{},
		}, nil
	}

	orgIDs := make([]int64, 0, len(memberships))
	roleMap := make(map[int64]string, len(memberships))
	for _, m := range memberships {
		orgIDs = append(orgIDs, m.OrganizationID)
		roleMap[m.OrganizationID] = m.Role
	}

	orgs := repositories.OrganizationRepository.Find(sqls.DB(), sqls.NewCnd().In("id", orgIDs).Eq("status", enums.StatusOk))

	resList := make([]response.OrganizationResponse, 0, len(orgs))
	for _, org := range orgs {
		resList = append(resList, response.OrganizationResponse{
			ID:        org.ID,
			Code:      org.Code,
			Name:      org.Name,
			Logo:      org.Logo,
			Plan:      org.Plan,
			Status:    org.Status,
			Role:      roleMap[org.ID],
			IsActive:  org.ID == user.ActiveOrgID,
			CreatedAt: org.CreatedAt,
		})
	}

	return &response.UserOrganizationListResponse{
		CurrentOrganizationID: user.ActiveOrgID,
		Organizations:         resList,
	}, nil
}

func (s *organizationService) SwitchActiveOrganization(userID int64, orgID int64) (*models.Organization, error) {
	user := repositories.UserRepository.Get(sqls.DB(), userID)
	if user == nil {
		return nil, errorsx.InvalidAccountI18n("error.e0260")
	}

	member := repositories.OrganizationMemberRepository.GetByOrgAndUser(sqls.DB(), orgID, userID)
	if member == nil || member.Status != enums.StatusOk {
		return nil, errorsx.ForbiddenI18n("error.e0225")
	}

	org := repositories.OrganizationRepository.Get(sqls.DB(), orgID)
	if org == nil || org.Status != enums.StatusOk {
		return nil, errorsx.InvalidParam("organization not found or disabled")
	}

	if err := repositories.UserRepository.UpdateColumn(sqls.DB(), userID, "active_org_id", orgID); err != nil {
		return nil, err
	}

	return org, nil
}

func (s *organizationService) GetActiveOrganization(principal *dto.AuthPrincipal) *models.Organization {
	if principal == nil || principal.UserID <= 0 {
		return nil
	}
	user := repositories.UserRepository.Get(sqls.DB(), principal.UserID)
	if user == nil || user.ActiveOrgID <= 0 {
		return nil
	}
	return repositories.OrganizationRepository.Get(sqls.DB(), user.ActiveOrgID)
}
