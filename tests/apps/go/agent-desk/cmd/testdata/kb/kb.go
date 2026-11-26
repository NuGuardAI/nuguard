package kb

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/cmd/testdata/seeds"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"
	"encoding/json"
	"time"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

type InitResult struct {
	FAQKnowledgeBaseID int64
	TotalFAQs          int
	CreatedFAQs        int
	UpdatedFAQs        int
}

func Init(lang seedlang.Language) (*InitResult, error) {
	faqSeeds := seeds.KnowledgeFAQSeeds(lang)
	result := &InitResult{
		TotalFAQs: len(faqSeeds),
	}
	err := sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		faqKnowledgeBase, ensureFAQErr := ensureFAQKnowledgeBase(ctx.Tx, lang)
		if ensureFAQErr != nil {
			return ensureFAQErr
		}
		result.FAQKnowledgeBaseID = faqKnowledgeBase.ID

		for _, faq := range faqSeeds {
			created, upsertErr := upsertKnowledgeFAQ(ctx.Tx, faqKnowledgeBase.ID, faq)
			if upsertErr != nil {
				return upsertErr
			}
			if created {
				result.CreatedFAQs++
			} else {
				result.UpdatedFAQs++
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}

	return result, nil
}

func ensureFAQKnowledgeBase(db *gorm.DB, lang seedlang.Language) (*models.KnowledgeBase, error) {
	now := time.Now()
	seed := seeds.FAQKnowledgeBaseSeed(lang)
	item := repositories.KnowledgeBaseRepository.FindOne(db, sqls.NewCnd().Eq("name", seed.Name))
	if item == nil {
		item = &models.KnowledgeBase{
			Name:                  seed.Name,
			Description:           seed.Description,
			KnowledgeType:         string(enums.KnowledgeBaseTypeFAQ),
			Status:                enums.StatusOk,
			DefaultTopK:           8,
			DefaultScoreThreshold: 0.35,
			DefaultRerankLimit:    5,
			ChunkProvider:         string(enums.KnowledgeChunkProviderFAQ),
			ChunkTargetTokens:     0,
			ChunkMaxTokens:        0,
			ChunkOverlapTokens:    0,
			AnswerMode:            int(enums.KnowledgeAnswerModeStrict),
			Remark:                seed.Remark,
			AuditFields: models.AuditFields{
				CreatedAt:      now,
				CreateUserID:   constants.SystemAuditUserID,
				CreateUserName: constants.SystemAuditUserName,
				UpdatedAt:      now,
				UpdateUserID:   constants.SystemAuditUserID,
				UpdateUserName: constants.SystemAuditUserName,
			},
		}
		if err := repositories.KnowledgeBaseRepository.Create(db, item); err != nil {
			return nil, err
		}
		return item, nil
	}

	err := repositories.KnowledgeBaseRepository.Updates(db, item.ID, map[string]any{
		"description":             seed.Description,
		"knowledge_type":          string(enums.KnowledgeBaseTypeFAQ),
		"status":                  enums.StatusOk,
		"default_top_k":           8,
		"default_score_threshold": 0.35,
		"default_rerank_limit":    5,
		"chunk_provider":          string(enums.KnowledgeChunkProviderFAQ),
		"chunk_target_tokens":     0,
		"chunk_max_tokens":        0,
		"chunk_overlap_tokens":    0,
		"answer_mode":             int(enums.KnowledgeAnswerModeStrict),
		"remark":                  seed.Remark,
		"update_user_id":          constants.SystemAuditUserID,
		"update_user_name":        constants.SystemAuditUserName,
		"updated_at":              now,
	})
	if err != nil {
		return nil, err
	}
	return repositories.KnowledgeBaseRepository.Get(db, item.ID), nil
}

func upsertKnowledgeFAQ(db *gorm.DB, knowledgeBaseID int64, seed seeds.KnowledgeFAQSeed) (bool, error) {
	now := time.Now()
	similarQuestions, err := json.Marshal(seed.SimilarQuestions)
	if err != nil {
		return false, err
	}

	item := repositories.KnowledgeFAQRepository.Find(db, sqls.NewCnd().
		Eq("knowledge_base_id", knowledgeBaseID).
		Eq("question", seed.Question))
	if len(item) == 0 {
		faq := &models.KnowledgeFAQ{
			KnowledgeBaseID:  knowledgeBaseID,
			Question:         seed.Question,
			Answer:           seed.Answer,
			SimilarQuestions: string(similarQuestions),
			Status:           enums.StatusOk,
			IndexStatus:      enums.KnowledgeDocumentIndexStatusPending,
			Remark:           seed.Remark,
			AuditFields: models.AuditFields{
				CreatedAt:      now,
				CreateUserID:   constants.SystemAuditUserID,
				CreateUserName: constants.SystemAuditUserName,
				UpdatedAt:      now,
				UpdateUserID:   constants.SystemAuditUserID,
				UpdateUserName: constants.SystemAuditUserName,
			},
		}
		if err := repositories.KnowledgeFAQRepository.Create(db, faq); err != nil {
			return false, err
		}
		return true, nil
	}

	if err := repositories.KnowledgeFAQRepository.Updates(db, item[0].ID, map[string]any{
		"answer":            seed.Answer,
		"similar_questions": string(similarQuestions),
		"status":            enums.StatusOk,
		"index_status":      enums.KnowledgeDocumentIndexStatusPending,
		"indexed_at":        nil,
		"index_error":       "",
		"remark":            seed.Remark,
		"update_user_id":    constants.SystemAuditUserID,
		"update_user_name":  constants.SystemAuditUserName,
		"updated_at":        now,
	}); err != nil {
		return false, err
	}
	return false, nil
}
