package rag

import (
	"context"
	"fmt"
	"strings"
)

func (s *retrieve) SelectContextResults(results []RetrieveResult, maxTokens int) []RetrieveResult {
	if len(results) == 0 {
		return nil
	}

	normalizedResults := normalizeContextResults(results)
	selected := make([]RetrieveResult, 0, len(normalizedResults))
	totalTokens := 0
	documentUsage := make(map[int64]int)

	for _, item := range normalizedResults {
		if documentUsage[item.DocumentID] >= 2 {
			continue
		}
		chunkText := buildContextChunkText(item)
		estimatedTokens := len(chunkText) / 2
		if totalTokens+estimatedTokens > maxTokens {
			break
		}
		selected = append(selected, item)
		totalTokens += estimatedTokens
		documentUsage[item.DocumentID]++
	}
	return selected
}

func (s *retrieve) BuildContext(_ context.Context, results []RetrieveResult, maxTokens int) string {
	if len(results) == 0 {
		return ""
	}

	normalizedResults := s.SelectContextResults(results, maxTokens)
	var builder strings.Builder
	for _, r := range normalizedResults {
		builder.WriteString(buildContextChunkText(r))
	}

	return builder.String()
}

func normalizeContextResults(results []RetrieveResult) []RetrieveResult {
	if len(results) == 0 {
		return nil
	}

	merged := mergeAdjacentResults(results)
	return dedupeSectionResults(merged)
}

func dedupeSectionResults(results []RetrieveResult) []RetrieveResult {
	seen := make(map[string]struct{})
	deduped := make([]RetrieveResult, 0, len(results))
	for _, item := range results {
		key := buildSectionKey(item)
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		deduped = append(deduped, item)
	}
	return deduped
}

func mergeAdjacentResults(results []RetrieveResult) []RetrieveResult {
	if len(results) == 0 {
		return nil
	}

	merged := make([]RetrieveResult, 0, len(results))
	for _, item := range results {
		if len(merged) == 0 {
			merged = append(merged, item)
			continue
		}

		last := &merged[len(merged)-1]
		if canMergeContextResult(*last, item) {
			last.Content = strings.TrimSpace(last.Content + "\n" + item.Content)
			if item.Score > last.Score {
				last.Score = item.Score
			}
			continue
		}
		merged = append(merged, item)
	}
	return merged
}

func canMergeContextResult(left, right RetrieveResult) bool {
	if left.FaqID > 0 || right.FaqID > 0 {
		return false
	}
	if left.DocumentID != right.DocumentID {
		return false
	}
	if left.SectionPath == "" || right.SectionPath == "" {
		return false
	}
	if left.SectionPath != right.SectionPath {
		return false
	}
	return right.ChunkNo == left.ChunkNo+1
}

func buildSectionKey(item RetrieveResult) string {
	if item.FaqID > 0 {
		return fmt.Sprintf("faq:%d", item.FaqID)
	}
	sectionPath := strings.TrimSpace(item.SectionPath)
	if sectionPath != "" {
		return fmt.Sprintf("%d|%s", item.DocumentID, sectionPath)
	}
	title := strings.TrimSpace(item.Title)
	if title != "" {
		return fmt.Sprintf("%d|%s", item.DocumentID, title)
	}
	return fmt.Sprintf("%d|chunk:%d", item.DocumentID, item.ChunkNo)
}

func buildContextChunkText(item RetrieveResult) string {
	if item.FaqID > 0 {
		title := strings.TrimSpace(item.FaqQuestion)
		if title == "" {
			title = strings.TrimSpace(item.Title)
		}
		if title == "" {
			title = fmt.Sprintf("FAQ#%d", item.FaqID)
		}
		return fmt.Sprintf("【FAQ：%s】\n%s\n\n", title, item.Content)
	}
	title := strings.TrimSpace(item.DocumentTitle)
	if title == "" {
		title = fmt.Sprintf("文档#%d", item.DocumentID)
	}
	if item.SectionPath != "" {
		return fmt.Sprintf("【文档：%s｜章节：%s】\n%s\n\n", title, item.SectionPath, item.Content)
	}
	if item.Title != "" {
		return fmt.Sprintf("【文档：%s｜标题：%s】\n%s\n\n", title, item.Title, item.Content)
	}
	return fmt.Sprintf("【文档：%s】\n%s\n\n", title, item.Content)
}
