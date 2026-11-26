package services

import (
	"testing"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

func TestKnowledgeDirectoryRejectsThirdLevel(t *testing.T) {
	setupKnowledgeDirectoryTestDB(t)
	operator := knowledgeDirectoryTestOperator()
	kb := createKnowledgeDirectoryTestBase(t, "Document KB", string(enums.KnowledgeBaseTypeDocument))
	parent, err := KnowledgeDirectoryService.CreateDirectory(request.CreateKnowledgeDirectoryRequest{
		KnowledgeBaseID: kb.ID,
		Name:            "一级目录",
	}, operator)
	if err != nil {
		t.Fatalf("create parent: %v", err)
	}
	child, err := KnowledgeDirectoryService.CreateDirectory(request.CreateKnowledgeDirectoryRequest{
		KnowledgeBaseID: kb.ID,
		ParentID:        parent.ID,
		Name:            "二级目录",
	}, operator)
	if err != nil {
		t.Fatalf("create child: %v", err)
	}

	if _, err := KnowledgeDirectoryService.CreateDirectory(request.CreateKnowledgeDirectoryRequest{
		KnowledgeBaseID: kb.ID,
		ParentID:        child.ID,
		Name:            "三级目录",
	}, operator); err == nil {
		t.Fatal("CreateDirectory() error is nil, want third-level rejection")
	}
}

func TestKnowledgeDocumentRejectsDirectoryFromOtherKnowledgeBase(t *testing.T) {
	setupKnowledgeDirectoryTestDB(t)
	operator := knowledgeDirectoryTestOperator()
	kb := createKnowledgeDirectoryTestBase(t, "Document KB", string(enums.KnowledgeBaseTypeDocument))
	otherKB := createKnowledgeDirectoryTestBase(t, "Other KB", string(enums.KnowledgeBaseTypeDocument))
	directory, err := KnowledgeDirectoryService.CreateDirectory(request.CreateKnowledgeDirectoryRequest{
		KnowledgeBaseID: otherKB.ID,
		Name:            "其他目录",
	}, operator)
	if err != nil {
		t.Fatalf("create directory: %v", err)
	}

	_, err = KnowledgeDocumentService.CreateKnowledgeDocument(request.CreateKnowledgeDocumentRequest{
		KnowledgeBaseID: kb.ID,
		DirectoryID:     directory.ID,
		Title:           "Doc",
		Content:         "content",
	}, operator)
	if err == nil {
		t.Fatal("CreateKnowledgeDocument() error is nil, want cross-knowledge-base directory rejection")
	}
}

func TestKnowledgeDirectoryDeleteRejectsAttachedContent(t *testing.T) {
	setupKnowledgeDirectoryTestDB(t)
	operator := knowledgeDirectoryTestOperator()
	kb := createKnowledgeDirectoryTestBase(t, "FAQ KB", string(enums.KnowledgeBaseTypeFAQ))
	directory, err := KnowledgeDirectoryService.CreateDirectory(request.CreateKnowledgeDirectoryRequest{
		KnowledgeBaseID: kb.ID,
		Name:            "FAQ目录",
	}, operator)
	if err != nil {
		t.Fatalf("create directory: %v", err)
	}
	if err := repositories.KnowledgeFAQRepository.Create(sqls.DB(), &models.KnowledgeFAQ{
		KnowledgeBaseID: kb.ID,
		DirectoryID:     directory.ID,
		Question:        "Question",
		Answer:          "Answer",
		Status:          enums.StatusOk,
	}); err != nil {
		t.Fatalf("create faq: %v", err)
	}

	if err := KnowledgeDirectoryService.DeleteDirectory(directory.ID); err == nil {
		t.Fatal("DeleteDirectory() error is nil, want attached content rejection")
	}
}

func TestKnowledgeDirectoryDeleteAllowsSoftDeletedDocument(t *testing.T) {
	setupKnowledgeDirectoryTestDB(t)
	operator := knowledgeDirectoryTestOperator()
	kb := createKnowledgeDirectoryTestBase(t, "Document KB", string(enums.KnowledgeBaseTypeDocument))
	directory, err := KnowledgeDirectoryService.CreateDirectory(request.CreateKnowledgeDirectoryRequest{
		KnowledgeBaseID: kb.ID,
		Name:            "已清空目录",
	}, operator)
	if err != nil {
		t.Fatalf("create directory: %v", err)
	}
	if err := repositories.KnowledgeDocumentRepository.Create(sqls.DB(), &models.KnowledgeDocument{
		KnowledgeBaseID: kb.ID,
		DirectoryID:     directory.ID,
		Title:           "Deleted Doc",
		ContentType:     enums.KnowledgeDocumentContentTypeMarkdown,
		Status:          enums.StatusDeleted,
		IndexStatus:     enums.KnowledgeDocumentIndexStatusPending,
	}); err != nil {
		t.Fatalf("create soft deleted document: %v", err)
	}

	if err := KnowledgeDirectoryService.DeleteDirectory(directory.ID); err != nil {
		t.Fatalf("DeleteDirectory() error = %v, want nil when only soft deleted documents exist", err)
	}
}

func setupKnowledgeDirectoryTestDB(t *testing.T) {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite db: %v", err)
	}
	if err := db.AutoMigrate(&models.KnowledgeBase{}, &models.KnowledgeDirectory{}, &models.KnowledgeDocument{}, &models.KnowledgeFAQ{}); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)
}

func createKnowledgeDirectoryTestBase(t *testing.T, name string, knowledgeType string) *models.KnowledgeBase {
	t.Helper()
	item := &models.KnowledgeBase{
		Name:          name,
		KnowledgeType: knowledgeType,
		Status:        enums.StatusOk,
	}
	if err := repositories.KnowledgeBaseRepository.Create(sqls.DB(), item); err != nil {
		t.Fatalf("create knowledge base: %v", err)
	}
	return item
}

func knowledgeDirectoryTestOperator() *dto.AuthPrincipal {
	return &dto.AuthPrincipal{
		UserID:   1,
		Username: "tester",
	}
}
