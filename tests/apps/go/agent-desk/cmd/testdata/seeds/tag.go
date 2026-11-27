package seeds

import "agent-desk/cmd/testdata/seedlang"

type TagSeed struct {
	ID       int64
	ParentID int64
	Name     string
	SortNo   int
}

func TagSeeds(lang seedlang.Language) []TagSeed {
	if lang == seedlang.English {
		return []TagSeed{
			{1, 0, "Pre-sales", 1},
			{2, 1, "AgentDesk", 1},
			{3, 2, "Product Inquiry", 1},
			{4, 2, "Purchase Intent", 1},
			{5, 0, "After-sales", 2},
			{6, 5, "AgentDesk", 1},
			{7, 6, "Issue Feedback", 1},
			{8, 6, "Product Deployment", 2},
			{9, 6, "Feature Request", 3},
		}
	}
	return []TagSeed{
		{1, 0, "售前", 1},
		{2, 1, "AgentDesk", 1},
		{3, 2, "产品咨询", 1},
		{4, 2, "购买意向", 1},
		{5, 0, "售后", 2},
		{6, 5, "AgentDesk", 1},
		{7, 6, "问题反馈", 1},
		{8, 6, "产品部署", 2},
		{9, 6, "需求工单", 3},
	}
}
