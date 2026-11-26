package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"
	"strings"
	"time"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
)

var AgentTeamService = newAgentTeamService()

func newAgentTeamService() *agentTeamService {
	return &agentTeamService{}
}

type agentTeamService struct {
}

func (s *agentTeamService) Get(id int64) *models.AgentTeam {
	return repositories.AgentTeamRepository.Get(sqls.DB(), id)
}

func (s *agentTeamService) Take(where ...interface{}) *models.AgentTeam {
	return repositories.AgentTeamRepository.Take(sqls.DB(), where...)
}

func (s *agentTeamService) Find(cnd *sqls.Cnd) []models.AgentTeam {
	return repositories.AgentTeamRepository.Find(sqls.DB(), cnd)
}

func (s *agentTeamService) FindOne(cnd *sqls.Cnd) *models.AgentTeam {
	return repositories.AgentTeamRepository.FindOne(sqls.DB(), cnd)
}

func (s *agentTeamService) FindPageByParams(params *params.QueryParams) (list []models.AgentTeam, paging *sqls.Paging) {
	return repositories.AgentTeamRepository.FindPageByParams(sqls.DB(), params)
}

func (s *agentTeamService) FindPageByCnd(cnd *sqls.Cnd) (list []models.AgentTeam, paging *sqls.Paging) {
	return repositories.AgentTeamRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *agentTeamService) Count(cnd *sqls.Cnd) int64 {
	return repositories.AgentTeamRepository.Count(sqls.DB(), cnd)
}

func (s *agentTeamService) FindByIds(ids []int64) []models.AgentTeam {
	return repositories.AgentTeamRepository.FindByIds(sqls.DB(), ids)
}

func (s *agentTeamService) Create(t *models.AgentTeam) error {
	return repositories.AgentTeamRepository.Create(sqls.DB(), t)
}

func (s *agentTeamService) Update(t *models.AgentTeam) error {
	return repositories.AgentTeamRepository.Update(sqls.DB(), t)
}

func (s *agentTeamService) Updates(id int64, columns map[string]interface{}) error {
	return repositories.AgentTeamRepository.Updates(sqls.DB(), id, columns)
}

func (s *agentTeamService) UpdateColumn(id int64, name string, value interface{}) error {
	return repositories.AgentTeamRepository.UpdateColumn(sqls.DB(), id, name, value)
}

func (s *agentTeamService) Delete(id int64) {
	repositories.AgentTeamRepository.Delete(sqls.DB(), id)
}

func (s *agentTeamService) CreateAgentTeam(req request.CreateAgentTeamRequest, operator *dto.AuthPrincipal) (*models.AgentTeam, error) {
	if operator == nil {
		return nil, errorsx.UnauthorizedI18n("error.auth.expired")
	}
	item, err := s.buildTeamModel(0, req.Name, req.LeaderUserID, req.Status, req.Description, req.Remark)
	if err != nil {
		return nil, err
	}
	item.AuditFields = utils.BuildAuditFields(operator)
	if err := repositories.AgentTeamRepository.Create(sqls.DB(), item); err != nil {
		return nil, err
	}
	return item, nil
}

func (s *agentTeamService) UpdateAgentTeam(req request.UpdateAgentTeamRequest, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	current := s.Get(req.ID)
	if current == nil || current.Status == enums.StatusDeleted {
		return errorsx.InvalidParamI18n("error.e0169")
	}
	item, err := s.buildTeamModel(req.ID, req.Name, req.LeaderUserID, req.Status, req.Description, req.Remark)
	if err != nil {
		return err
	}
	now := time.Now()
	return repositories.AgentTeamRepository.Updates(sqls.DB(), req.ID, map[string]any{
		"name":             item.Name,
		"leader_user_id":   item.LeaderUserID,
		"status":           item.Status,
		"description":      item.Description,
		"remark":           item.Remark,
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       now,
	})
}

func (s *agentTeamService) DeleteAgentTeam(id int64, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	current := s.Get(id)
	if current == nil || current.Status == enums.StatusDeleted {
		return errorsx.InvalidParamI18n("error.e0169")
	}
	if AgentProfileService.Take("team_id = ?", id) != nil {
		return errorsx.ForbiddenI18n("error.e0167")
	}
	if AgentTeamScheduleService.Take("team_id = ?", id) != nil {
		return errorsx.ForbiddenI18n("error.e0168")
	}
	if AIAgentService.Take(
		"(team_ids = ? OR team_ids LIKE ? OR team_ids LIKE ? OR team_ids LIKE ?) AND status <> ?",
		utils.JoinInt64s([]int64{id}),
		utils.JoinInt64s([]int64{id})+",%",
		"%,"+utils.JoinInt64s([]int64{id}),
		"%,"+utils.JoinInt64s([]int64{id})+",%",
		enums.StatusDeleted,
	) != nil {
		return errorsx.ForbiddenI18n("error.e0166")
	}
	return repositories.AgentTeamRepository.Updates(sqls.DB(), id, map[string]any{
		"status":           enums.StatusDeleted,
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       time.Now(),
	})
}

func (s *agentTeamService) buildTeamModel(id int64, name string, leaderUserID int64, status int, description, remark string) (*models.AgentTeam, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return nil, errorsx.InvalidParamI18n("error.e0170")
	}
	if exists := s.Take("name = ? AND status <> ? AND id <> ?", name, enums.StatusDeleted, id); exists != nil {
		return nil, errorsx.InvalidParamI18n("error.e0171")
	}
	if leaderUserID > 0 && UserService.Get(leaderUserID) == nil {
		return nil, errorsx.InvalidParamI18n("error.e0294")
	}
	if status != 0 && status != 1 {
		return nil, errorsx.InvalidParamI18n("error.e0174")
	}
	return &models.AgentTeam{
		Name:         name,
		LeaderUserID: leaderUserID,
		Status:       enums.Status(status),
		Description:  strings.TrimSpace(description),
		Remark:       strings.TrimSpace(remark),
	}, nil
}
