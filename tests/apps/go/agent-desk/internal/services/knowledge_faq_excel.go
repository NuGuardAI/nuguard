package services

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"path/filepath"
	"strings"
	"time"

	"agent-desk/internal/ai/rag"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/i18nx"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
	"github.com/xuri/excelize/v2"
)

const (
	knowledgeFAQExcelSheetName   = "FAQ"
	knowledgeFAQExcelContentType = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

var indexKnowledgeFAQAfterImport = func(faqID int64) error {
	return rag.Index.IndexFAQByID(context.Background(), faqID)
}

type knowledgeFAQImportRow struct {
	RowNo            int
	Question         string
	Answer           string
	SimilarQuestions []string
	Remark           string
}

func (s *knowledgeFAQService) BuildKnowledgeFAQImportTemplate() (*response.KnowledgeFAQExportedFile, error) {
	workbook := excelize.NewFile()
	defer workbook.Close()
	if err := writeKnowledgeFAQWorkbookHeader(workbook); err != nil {
		return nil, err
	}
	_ = workbook.SetCellValue(knowledgeFAQExcelSheetName, "A2", "如何重置密码？")
	_ = workbook.SetCellValue(knowledgeFAQExcelSheetName, "B2", "进入个人设置后点击重置密码。")
	_ = workbook.SetCellValue(knowledgeFAQExcelSheetName, "C2", "忘记密码怎么办\n重置密码在哪里")
	_ = workbook.SetCellValue(knowledgeFAQExcelSheetName, "D2", "账号")
	return buildKnowledgeFAQExcelFile("knowledge-faq-import-template.xlsx", workbook)
}

func (s *knowledgeFAQService) ExportKnowledgeFAQs(knowledgeBaseID int64) (*response.KnowledgeFAQExportedFile, error) {
	if _, err := s.requireFAQKnowledgeBase(knowledgeBaseID); err != nil {
		return nil, err
	}
	list := repositories.KnowledgeFAQRepository.FindAllByKnowledgeBaseID(sqls.DB(), knowledgeBaseID)
	workbook := excelize.NewFile()
	defer workbook.Close()
	if err := writeKnowledgeFAQWorkbookHeader(workbook); err != nil {
		return nil, err
	}
	for index, item := range list {
		rowNo := index + 2
		_ = workbook.SetCellValue(knowledgeFAQExcelSheetName, fmt.Sprintf("A%d", rowNo), item.Question)
		_ = workbook.SetCellValue(knowledgeFAQExcelSheetName, fmt.Sprintf("B%d", rowNo), item.Answer)
		_ = workbook.SetCellValue(knowledgeFAQExcelSheetName, fmt.Sprintf("C%d", rowNo), strings.Join(decodeKnowledgeFAQSimilarQuestions(item.SimilarQuestions), "\n"))
		_ = workbook.SetCellValue(knowledgeFAQExcelSheetName, fmt.Sprintf("D%d", rowNo), item.Remark)
	}
	filename := fmt.Sprintf("knowledge-faq-%d-%s.xlsx", knowledgeBaseID, time.Now().Format("20060102150405"))
	return buildKnowledgeFAQExcelFile(filename, workbook)
}

func (s *knowledgeFAQService) ImportKnowledgeFAQs(req request.ImportKnowledgeFAQRequest, operator *dto.AuthPrincipal) (*response.KnowledgeFAQImportResult, error) {
	if operator == nil {
		return nil, errorsx.UnauthorizedI18n("error.auth.expired")
	}
	if req.Mode != request.KnowledgeFAQImportModeAppend && req.Mode != request.KnowledgeFAQImportModeOverwrite {
		return nil, errorsx.InvalidParamI18n("error.e0177")
	}
	if req.Reader == nil {
		return nil, errorsx.InvalidParamI18n("error.e0327")
	}
	if strings.ToLower(filepath.Ext(req.Filename)) != ".xlsx" {
		return nil, errorsx.InvalidParamI18n("error.e0089")
	}
	kb, err := s.requireFAQKnowledgeBase(req.KnowledgeBaseID)
	if err != nil {
		return nil, err
	}
	rows, result, err := parseKnowledgeFAQImportRows(req.Reader, req.Locale)
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return result, nil
	}

	questions := make([]string, 0, len(rows))
	for _, row := range rows {
		questions = append(questions, row.Question)
	}
	existingList := repositories.KnowledgeFAQRepository.FindByKnowledgeBaseIDAndQuestions(sqls.DB(), req.KnowledgeBaseID, questions)
	existingMap := make(map[string]models.KnowledgeFAQ, len(existingList))
	for _, item := range existingList {
		if _, ok := existingMap[item.Question]; !ok {
			existingMap[item.Question] = item
		}
	}

	indexFAQIDs := make([]int64, 0, len(rows))
	err = sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		for _, row := range rows {
			existing, exists := existingMap[row.Question]
			if exists && req.Mode == request.KnowledgeFAQImportModeAppend {
				result.Skipped++
				result.Errors = append(result.Errors, response.KnowledgeFAQImportError{Row: row.RowNo, Message: i18nx.Getf(req.Locale, "error.e0237")})
				continue
			}

			similarQuestions, marshalErr := json.Marshal(row.SimilarQuestions)
			if marshalErr != nil {
				result.Failed++
				result.Errors = append(result.Errors, response.KnowledgeFAQImportError{Row: row.RowNo, Message: i18nx.Getf(req.Locale, "error.e0280")})
				continue
			}

			if exists {
				if updateErr := repositories.KnowledgeFAQRepository.Updates(ctx.Tx, existing.ID, map[string]any{
					"answer":            row.Answer,
					"similar_questions": string(similarQuestions),
					"index_status":      enums.KnowledgeDocumentIndexStatusPending,
					"indexed_at":        nil,
					"index_error":       "",
					"remark":            row.Remark,
					"update_user_id":    operator.UserID,
					"update_user_name":  operator.Username,
					"updated_at":        time.Now(),
				}); updateErr != nil {
					return updateErr
				}
				result.Updated++
				indexFAQIDs = append(indexFAQIDs, existing.ID)
				continue
			}

			item := &models.KnowledgeFAQ{
				KnowledgeBaseID:  req.KnowledgeBaseID,
				Question:         row.Question,
				Answer:           row.Answer,
				SimilarQuestions: string(similarQuestions),
				Status:           kb.Status,
				IndexStatus:      enums.KnowledgeDocumentIndexStatusPending,
				IndexError:       "",
				IndexedAt:        nil,
				Remark:           row.Remark,
				AuditFields:      utils.BuildAuditFields(operator),
			}
			if createErr := repositories.KnowledgeFAQRepository.Create(ctx.Tx, item); createErr != nil {
				return createErr
			}
			result.Created++
			indexFAQIDs = append(indexFAQIDs, item.ID)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}

	for _, faqID := range indexFAQIDs {
		if indexErr := indexKnowledgeFAQAfterImport(faqID); indexErr != nil {
			slog.Error("failed to index imported knowledge faq", "faq_id", faqID, "error", indexErr)
		}
	}
	return result, nil
}

func writeKnowledgeFAQWorkbookHeader(workbook *excelize.File) error {
	sheet := workbook.GetSheetName(0)
	if sheet == "" {
		sheet = knowledgeFAQExcelSheetName
		workbook.NewSheet(sheet)
	} else if sheet != knowledgeFAQExcelSheetName {
		if err := workbook.SetSheetName(sheet, knowledgeFAQExcelSheetName); err != nil {
			return err
		}
	}
	headers := []string{"标准问题", "答案", "相似问", "备注"}
	for index, header := range headers {
		cell, err := excelize.CoordinatesToCellName(index+1, 1)
		if err != nil {
			return err
		}
		if err := workbook.SetCellValue(knowledgeFAQExcelSheetName, cell, header); err != nil {
			return err
		}
	}
	style, err := workbook.NewStyle(&excelize.Style{
		Font:      &excelize.Font{Bold: true},
		Alignment: &excelize.Alignment{WrapText: true, Vertical: "top"},
	})
	if err == nil {
		_ = workbook.SetCellStyle(knowledgeFAQExcelSheetName, "A1", "D1", style)
	}
	_ = workbook.SetColWidth(knowledgeFAQExcelSheetName, "A", "A", 36)
	_ = workbook.SetColWidth(knowledgeFAQExcelSheetName, "B", "B", 60)
	_ = workbook.SetColWidth(knowledgeFAQExcelSheetName, "C", "C", 40)
	_ = workbook.SetColWidth(knowledgeFAQExcelSheetName, "D", "D", 24)
	return nil
}

func buildKnowledgeFAQExcelFile(filename string, workbook *excelize.File) (*response.KnowledgeFAQExportedFile, error) {
	var buf bytes.Buffer
	if err := workbook.Write(&buf); err != nil {
		return nil, err
	}
	return &response.KnowledgeFAQExportedFile{
		Filename:    filename,
		ContentType: knowledgeFAQExcelContentType,
		Data:        buf.Bytes(),
	}, nil
}

func parseKnowledgeFAQImportRows(reader io.Reader, locale string) ([]knowledgeFAQImportRow, *response.KnowledgeFAQImportResult, error) {
	workbook, err := excelize.OpenReader(reader)
	if err != nil {
		return nil, nil, errorsx.InvalidParamI18n("error.e0023")
	}
	defer workbook.Close()
	sheet := knowledgeFAQExcelSheetName
	if index, _ := workbook.GetSheetIndex(sheet); index < 0 {
		sheets := workbook.GetSheetList()
		if len(sheets) == 0 {
			return nil, nil, errorsx.InvalidParamI18n("error.e0022")
		}
		sheet = sheets[0]
	}
	table, err := workbook.GetRows(sheet)
	if err != nil {
		return nil, nil, errorsx.InvalidParamI18n("error.e0024")
	}
	if len(table) == 0 {
		return nil, nil, errorsx.InvalidParamI18n("error.e0022")
	}
	headerMap := buildKnowledgeFAQHeaderMap(table[0])
	if _, ok := headerMap["question"]; !ok {
		return nil, nil, errorsx.InvalidParamI18n("error.e0297")
	}
	if _, ok := headerMap["answer"]; !ok {
		return nil, nil, errorsx.InvalidParamI18n("error.e0298")
	}

	result := &response.KnowledgeFAQImportResult{Errors: make([]response.KnowledgeFAQImportError, 0)}
	rows := make([]knowledgeFAQImportRow, 0, len(table)-1)
	seen := make(map[string]int)
	for index := 1; index < len(table); index++ {
		current := table[index]
		if isKnowledgeFAQExcelRowEmpty(current) {
			continue
		}
		result.Total++
		rowNo := index + 1
		row := knowledgeFAQImportRow{
			RowNo:            rowNo,
			Question:         getKnowledgeFAQExcelCell(current, headerMap["question"]),
			Answer:           getKnowledgeFAQExcelCell(current, headerMap["answer"]),
			SimilarQuestions: parseKnowledgeFAQSimilarQuestions(getKnowledgeFAQExcelCell(current, headerMap["similarQuestions"])),
			Remark:           getKnowledgeFAQExcelCell(current, headerMap["remark"]),
		}
		if row.Question == "" {
			result.Failed++
			result.Errors = append(result.Errors, response.KnowledgeFAQImportError{Row: rowNo, Message: i18nx.Getf(locale, "error.e0340")})
			continue
		}
		if len([]rune(row.Question)) > 500 {
			result.Failed++
			result.Errors = append(result.Errors, response.KnowledgeFAQImportError{Row: rowNo, Message: i18nx.Getf(locale, "error.e0341")})
			continue
		}
		if row.Answer == "" {
			result.Failed++
			result.Errors = append(result.Errors, response.KnowledgeFAQImportError{Row: rowNo, Message: i18nx.Getf(locale, "error.e0292")})
			continue
		}
		if firstRow, exists := seen[row.Question]; exists {
			result.Failed++
			result.Errors = append(result.Errors, response.KnowledgeFAQImportError{Row: rowNo, Message: i18nx.Getf(locale, "error.knowledgeFAQImport.duplicateQuestionInFile", firstRow)})
			continue
		}
		seen[row.Question] = rowNo
		rows = append(rows, row)
	}
	return rows, result, nil
}

func buildKnowledgeFAQHeaderMap(headerRow []string) map[string]int {
	accepted := map[string]string{
		"标准问题":             "question",
		"问题":               "question",
		"question":         "question",
		"答案":               "answer",
		"answer":           "answer",
		"相似问":              "similarQuestions",
		"相似问题":             "similarQuestions",
		"similarquestions": "similarQuestions",
		"备注":               "remark",
		"remark":           "remark",
	}
	result := make(map[string]int)
	for index, header := range headerRow {
		key := strings.ToLower(strings.TrimSpace(strings.TrimPrefix(header, "\uFEFF")))
		if field, ok := accepted[key]; ok {
			if _, exists := result[field]; !exists {
				result[field] = index
			}
		}
	}
	return result
}

func getKnowledgeFAQExcelCell(row []string, index int) string {
	if index < 0 || index >= len(row) {
		return ""
	}
	return strings.TrimSpace(row[index])
}

func isKnowledgeFAQExcelRowEmpty(row []string) bool {
	for _, cell := range row {
		if strings.TrimSpace(cell) != "" {
			return false
		}
	}
	return true
}

func parseKnowledgeFAQSimilarQuestions(value string) []string {
	lines := strings.Split(strings.ReplaceAll(value, "\r\n", "\n"), "\n")
	items := make([]string, 0, len(lines))
	seen := make(map[string]struct{}, len(lines))
	for _, line := range lines {
		item := strings.TrimSpace(strings.ReplaceAll(line, "\r", ""))
		if item == "" {
			continue
		}
		if _, ok := seen[item]; ok {
			continue
		}
		seen[item] = struct{}{}
		items = append(items, item)
	}
	return items
}

func decodeKnowledgeFAQSimilarQuestions(value string) []string {
	var items []string
	if strings.TrimSpace(value) == "" {
		return nil
	}
	if err := json.Unmarshal([]byte(value), &items); err != nil {
		return nil
	}
	return items
}
