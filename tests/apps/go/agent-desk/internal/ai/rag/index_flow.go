package rag

import (
	"fmt"
	"strings"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
)

func (s *index) loadDocumentByID(documentID int64) (*models.KnowledgeDocument, error) {
	document := repositories.KnowledgeDocumentRepository.Get(sqls.DB(), documentID)
	if document == nil {
		return nil, fmt.Errorf("document not found: %d", documentID)
	}
	return document, nil
}

func loadKnowledgeDirectoryPath(directoryID int64) string {
	if directoryID <= 0 {
		return ""
	}
	item := repositories.KnowledgeDirectoryRepository.Get(sqls.DB(), directoryID)
	if item == nil {
		return ""
	}
	if item.ParentID <= 0 {
		return item.Name
	}
	parent := repositories.KnowledgeDirectoryRepository.Get(sqls.DB(), item.ParentID)
	if parent == nil {
		return item.Name
	}
	return parent.Name + " / " + item.Name
}

func joinKnowledgeSectionPath(parts ...string) string {
	ret := make([]string, 0, len(parts))
	for _, item := range parts {
		item = strings.TrimSpace(item)
		if item != "" {
			ret = append(ret, item)
		}
	}
	return strings.Join(ret, " / ")
}

func (s *index) loadFAQByID(faqID int64) (*models.KnowledgeFAQ, error) {
	faq := repositories.KnowledgeFAQRepository.Get(sqls.DB(), faqID)
	if faq == nil {
		return nil, fmt.Errorf("faq not found: %d", faqID)
	}
	return faq, nil
}

func (s *index) loadDocumentKnowledgeBase(document models.KnowledgeDocument) (*models.KnowledgeBase, error) {
	knowledgeBase := repositories.KnowledgeBaseRepository.Get(sqls.DB(), document.KnowledgeBaseID)
	if knowledgeBase == nil {
		return nil, fmt.Errorf("knowledge base not found: %d", document.KnowledgeBaseID)
	}
	return knowledgeBase, nil
}

func (s *index) loadFAQKnowledgeBase(faq models.KnowledgeFAQ) (*models.KnowledgeBase, error) {
	knowledgeBase := repositories.KnowledgeBaseRepository.Get(sqls.DB(), faq.KnowledgeBaseID)
	if knowledgeBase == nil {
		return nil, fmt.Errorf("knowledge base not found: %d", faq.KnowledgeBaseID)
	}
	if knowledgeBase.KnowledgeType != string(enums.KnowledgeBaseTypeFAQ) {
		return nil, fmt.Errorf("knowledge base %d is not faq type", knowledgeBase.ID)
	}
	return knowledgeBase, nil
}
