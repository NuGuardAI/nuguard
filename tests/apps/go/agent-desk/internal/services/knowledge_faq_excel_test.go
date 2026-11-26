package services

import (
	"bytes"
	"encoding/json"
	"testing"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"github.com/xuri/excelize/v2"
	"gorm.io/gorm"
)

func TestKnowledgeFAQExcelTemplateContainsExpectedHeaders(t *testing.T) {
	file, err := KnowledgeFAQService.BuildKnowledgeFAQImportTemplate()
	if err != nil {
		t.Fatalf("BuildKnowledgeFAQImportTemplate() error = %v", err)
	}

	workbook, err := excelize.OpenReader(bytes.NewReader(file.Data))
	if err != nil {
		t.Fatalf("open template workbook: %v", err)
	}
	defer workbook.Close()

	rows, err := workbook.GetRows("FAQ")
	if err != nil {
		t.Fatalf("get FAQ rows: %v", err)
	}
	if len(rows) == 0 {
		t.Fatal("template has no rows")
	}
	want := []string{"标准问题", "答案", "相似问", "备注"}
	for i, value := range want {
		if rows[0][i] != value {
			t.Fatalf("header %d = %q, want %q", i, rows[0][i], value)
		}
	}
}

func TestKnowledgeFAQExcelExportWritesSimilarQuestionsWithLineBreaks(t *testing.T) {
	setupKnowledgeFAQExcelTestDB(t)
	kb := createKnowledgeFAQExcelTestBase(t, enums.StatusOk)
	similarQuestions, _ := json.Marshal([]string{"怎么重置密码", "忘记密码怎么办"})
	if err := repositories.KnowledgeFAQRepository.Create(sqls.DB(), &models.KnowledgeFAQ{
		KnowledgeBaseID:  kb.ID,
		Question:         "如何重置密码？",
		Answer:           "进入个人设置后点击重置密码。",
		SimilarQuestions: string(similarQuestions),
		Status:           enums.StatusOk,
		IndexStatus:      enums.KnowledgeDocumentIndexStatusPending,
		Remark:           "账号",
	}); err != nil {
		t.Fatalf("create faq: %v", err)
	}

	file, err := KnowledgeFAQService.ExportKnowledgeFAQs(kb.ID)
	if err != nil {
		t.Fatalf("ExportKnowledgeFAQs() error = %v", err)
	}
	workbook, err := excelize.OpenReader(bytes.NewReader(file.Data))
	if err != nil {
		t.Fatalf("open export workbook: %v", err)
	}
	defer workbook.Close()

	cell, err := workbook.GetCellValue("FAQ", "C2")
	if err != nil {
		t.Fatalf("get C2: %v", err)
	}
	if cell != "怎么重置密码\n忘记密码怎么办" {
		t.Fatalf("similar questions cell = %q", cell)
	}
}

func TestImportKnowledgeFAQsAppendSkipsExistingQuestion(t *testing.T) {
	setupKnowledgeFAQExcelTestDB(t)
	restore := stubKnowledgeFAQImportIndex(t)
	defer restore()
	kb := createKnowledgeFAQExcelTestBase(t, enums.StatusOk)
	if err := repositories.KnowledgeFAQRepository.Create(sqls.DB(), &models.KnowledgeFAQ{
		KnowledgeBaseID: kb.ID,
		Question:        "如何重置密码？",
		Answer:          "旧答案",
		Status:          enums.StatusOk,
		IndexStatus:     enums.KnowledgeDocumentIndexStatusIndexed,
	}); err != nil {
		t.Fatalf("create faq: %v", err)
	}

	result, err := KnowledgeFAQService.ImportKnowledgeFAQs(request.ImportKnowledgeFAQRequest{
		KnowledgeBaseID: kb.ID,
		Mode:            request.KnowledgeFAQImportModeAppend,
		Filename:        "faq.xlsx",
		Reader: bytes.NewReader(buildKnowledgeFAQExcelWorkbook(t, [][]string{
			{"标准问题", "答案", "相似问", "备注"},
			{"如何重置密码？", "新答案", "重置密码在哪里\n忘记密码", "账号"},
			{"支持哪些渠道？", "支持网页和企业微信。", "", "渠道"},
		})),
	}, knowledgeFAQExcelTestOperator())
	if err != nil {
		t.Fatalf("ImportKnowledgeFAQs() error = %v", err)
	}
	if result.Created != 1 || result.Updated != 0 || result.Skipped != 1 || result.Failed != 0 {
		t.Fatalf("result = %+v", result)
	}

	list := repositories.KnowledgeFAQRepository.Find(sqls.DB(), sqls.NewCnd().Eq("knowledge_base_id", kb.ID))
	if len(list) != 2 {
		t.Fatalf("faq count = %d, want 2", len(list))
	}
	existing := repositories.KnowledgeFAQRepository.Find(sqls.DB(), sqls.NewCnd().Eq("question", "如何重置密码？"))
	if len(existing) != 1 || existing[0].Answer != "旧答案" {
		t.Fatalf("existing faq changed: %+v", existing)
	}
}

func TestImportKnowledgeFAQsOverwriteUpdatesExistingQuestion(t *testing.T) {
	setupKnowledgeFAQExcelTestDB(t)
	restore := stubKnowledgeFAQImportIndex(t)
	defer restore()
	kb := createKnowledgeFAQExcelTestBase(t, enums.StatusDisabled)
	if err := repositories.KnowledgeFAQRepository.Create(sqls.DB(), &models.KnowledgeFAQ{
		KnowledgeBaseID: kb.ID,
		Question:        "如何重置密码？",
		Answer:          "旧答案",
		Status:          enums.StatusOk,
		IndexStatus:     enums.KnowledgeDocumentIndexStatusIndexed,
	}); err != nil {
		t.Fatalf("create faq: %v", err)
	}

	result, err := KnowledgeFAQService.ImportKnowledgeFAQs(request.ImportKnowledgeFAQRequest{
		KnowledgeBaseID: kb.ID,
		Mode:            request.KnowledgeFAQImportModeOverwrite,
		Filename:        "faq.xlsx",
		Reader: bytes.NewReader(buildKnowledgeFAQExcelWorkbook(t, [][]string{
			{"标准问题", "答案", "相似问", "备注"},
			{"如何重置密码？", "新答案", "重置密码在哪里\n重置密码在哪里\n忘记密码", "账号"},
			{"支持哪些渠道？", "支持网页和企业微信。", "", "渠道"},
		})),
	}, knowledgeFAQExcelTestOperator())
	if err != nil {
		t.Fatalf("ImportKnowledgeFAQs() error = %v", err)
	}
	if result.Created != 1 || result.Updated != 1 || result.Skipped != 0 || result.Failed != 0 {
		t.Fatalf("result = %+v", result)
	}

	updated := repositories.KnowledgeFAQRepository.Find(sqls.DB(), sqls.NewCnd().Eq("question", "如何重置密码？"))
	if len(updated) != 1 {
		t.Fatalf("updated faq count = %d", len(updated))
	}
	if updated[0].Answer != "新答案" {
		t.Fatalf("updated answer = %q", updated[0].Answer)
	}
	if updated[0].IndexStatus != enums.KnowledgeDocumentIndexStatusPending {
		t.Fatalf("index status = %q", updated[0].IndexStatus)
	}
	var similar []string
	if err := json.Unmarshal([]byte(updated[0].SimilarQuestions), &similar); err != nil {
		t.Fatalf("unmarshal similar questions: %v", err)
	}
	if len(similar) != 2 || similar[0] != "重置密码在哪里" || similar[1] != "忘记密码" {
		t.Fatalf("similar questions = %#v", similar)
	}

	created := repositories.KnowledgeFAQRepository.Find(sqls.DB(), sqls.NewCnd().Eq("question", "支持哪些渠道？"))
	if len(created) != 1 || created[0].Status != enums.StatusDisabled {
		t.Fatalf("created faq = %+v", created)
	}
}

func setupKnowledgeFAQExcelTestDB(t *testing.T) {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite db: %v", err)
	}
	if err := db.AutoMigrate(&models.KnowledgeBase{}, &models.KnowledgeFAQ{}, &models.KnowledgeChunk{}); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)
}

func createKnowledgeFAQExcelTestBase(t *testing.T, status enums.Status) *models.KnowledgeBase {
	t.Helper()
	item := &models.KnowledgeBase{
		Name:          "FAQ",
		KnowledgeType: string(enums.KnowledgeBaseTypeFAQ),
		Status:        status,
	}
	if err := repositories.KnowledgeBaseRepository.Create(sqls.DB(), item); err != nil {
		t.Fatalf("create knowledge base: %v", err)
	}
	return item
}

func buildKnowledgeFAQExcelWorkbook(t *testing.T, rows [][]string) []byte {
	t.Helper()
	workbook := excelize.NewFile()
	defer workbook.Close()
	sheet := workbook.GetSheetName(0)
	if err := workbook.SetSheetName(sheet, "FAQ"); err != nil {
		t.Fatalf("set sheet name: %v", err)
	}
	for rowIndex, row := range rows {
		for colIndex, value := range row {
			cell, err := excelize.CoordinatesToCellName(colIndex+1, rowIndex+1)
			if err != nil {
				t.Fatalf("cell name: %v", err)
			}
			if err := workbook.SetCellValue("FAQ", cell, value); err != nil {
				t.Fatalf("set cell value: %v", err)
			}
		}
	}
	var buf bytes.Buffer
	if err := workbook.Write(&buf); err != nil {
		t.Fatalf("write workbook: %v", err)
	}
	return buf.Bytes()
}

func knowledgeFAQExcelTestOperator() *dto.AuthPrincipal {
	return &dto.AuthPrincipal{UserID: 1, Username: "admin"}
}

func stubKnowledgeFAQImportIndex(t *testing.T) func() {
	t.Helper()
	original := indexKnowledgeFAQAfterImport
	indexed := make([]int64, 0)
	indexKnowledgeFAQAfterImport = func(faqID int64) error {
		indexed = append(indexed, faqID)
		return nil
	}
	return func() {
		if len(indexed) == 0 {
			t.Fatalf("expected imported FAQs to be indexed")
		}
		indexKnowledgeFAQAfterImport = original
	}
}
