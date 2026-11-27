package repositories

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var AgentRunRepository = newAgentRunRepository()

func newAgentRunRepository() *agentRunRepository {
	return &agentRunRepository{}
}

type agentRunRepository struct{}

func (r *agentRunRepository) Get(db *gorm.DB, id int64) *models.AgentRun {
	ret := &models.AgentRun{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *agentRunRepository) TakeByWorkflowRunID(db *gorm.DB, workflowRunID int64) *models.AgentRun {
	if workflowRunID <= 0 {
		return nil
	}
	ret := &models.AgentRun{}
	if err := db.Where("workflow_run_id = ?", workflowRunID).Order("id DESC").First(ret).Error; err != nil {
		return nil
	}
	return ret
}

func (r *agentRunRepository) Create(db *gorm.DB, item *models.AgentRun) error {
	return db.Create(item).Error
}

func (r *agentRunRepository) FindPageByCnd(db *gorm.DB, cnd *sqls.Cnd) (list []models.AgentRun, paging *sqls.Paging) {
	cnd.Find(db, &list)
	return list, &sqls.Paging{Page: cnd.Paging.Page, Limit: cnd.Paging.Limit, Total: cnd.Count(db, &models.AgentRun{})}
}

func (r *agentRunRepository) FindPageByParams(db *gorm.DB, queryParams *params.QueryParams) (list []models.AgentRun, paging *sqls.Paging) {
	return r.FindPageByCnd(db, &queryParams.Cnd)
}

func (r *agentRunRepository) Updates(db *gorm.DB, id int64, columns map[string]any) error {
	return db.Model(&models.AgentRun{}).Where("id = ?", id).Updates(columns).Error
}

func (r *agentRunRepository) FindRecent(db *gorm.DB, aiAgentID int64, limit int) []models.AgentRun {
	if limit <= 0 || limit > 5000 {
		limit = 5000
	}
	query := db.Order("id DESC").Limit(limit)
	if aiAgentID > 0 {
		query = query.Where("ai_agent_id = ?", aiAgentID)
	}
	var items []models.AgentRun
	if err := query.Find(&items).Error; err != nil {
		return nil
	}
	return items
}
