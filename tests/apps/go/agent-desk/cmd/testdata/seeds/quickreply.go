package seeds

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/internal/pkg/enums"
)

type QuickReplySeed struct {
	ID        int64
	GroupName string
	Title     string
	Content   string
	Status    enums.Status
	SortNo    int
}

func QuickReplySeeds(lang seedlang.Language) []QuickReplySeed {
	if lang == seedlang.English {
		return []QuickReplySeed{
			{ID: 1, GroupName: "New Visitor Reception", Title: "First contact greeting", Content: "Hello, welcome to AgentDesk support. I am the consultant assisting you today. Tell me what product, pricing, or integration option you want to learn about, and I will help you assess it quickly.", Status: enums.StatusOk, SortNo: 100},
			{ID: 2, GroupName: "Product Inquiry", Title: "Product capability overview", Content: "We currently support AI Q&A, knowledge base retrieval, human handoff, tag management, quick replies, and agent workspace administration. If you already have a business scenario, I can break down a solution for that scenario.", Status: enums.StatusOk, SortNo: 95},
			{ID: 3, GroupName: "Product Inquiry", Title: "Deployment options", Content: "The system supports both private deployment and cloud deployment. If you have strong data compliance requirements, evaluate private deployment first. If you want to launch quickly, start with the cloud version.", Status: enums.StatusOk, SortNo: 90},
			{ID: 4, GroupName: "Quotation Follow-up", Title: "Information before quotation", Content: "To prepare an accurate quote, please share the expected number of agent seats, average daily conversation volume, whether a knowledge base is needed, and whether private deployment is required. I will organize the information and follow up quickly.", Status: enums.StatusOk, SortNo: 85},
			{ID: 5, GroupName: "Quotation Follow-up", Title: "Quotation sent reminder", Content: "Hello, the solution and quotation have been sent to you. Please review them when convenient. If you want me to walk through feature boundaries, implementation timeline, and delivery approach, I can arrange that directly.", Status: enums.StatusOk, SortNo: 80},
			{ID: 6, GroupName: "Implementation", Title: "Confirm details before troubleshooting", Content: "Got it. I will help troubleshoot first. Please add the time the issue started, affected scope, exact error screenshot, and whether any configuration was changed recently. This will help us locate the cause faster.", Status: enums.StatusOk, SortNo: 75},
			{ID: 7, GroupName: "Implementation", Title: "Configuration effective time", Content: "The configuration has been updated and usually takes effect within 1 to 3 minutes. Please refresh the page and run another test. If anything is still abnormal, I will continue following up.", Status: enums.StatusOk, SortNo: 70},
			{ID: 8, GroupName: "After-sales Support", Title: "Issue escalation notice", Content: "I have recorded this issue and escalated it to the technical team. The current priority is marked as high. We expect to provide the first conclusion today, and I will update you as soon as there is progress.", Status: enums.StatusOk, SortNo: 65},
			{ID: 9, GroupName: "After-sales Support", Title: "Version update notice template", Content: "Hello, a version update is scheduled for Thursday evening. It mainly includes knowledge retrieval optimization and workspace experience improvements. There may be brief fluctuations during the update, and we will prepare rollback plans in advance.", Status: enums.StatusOk, SortNo: 60},
			{ID: 10, GroupName: "Customer Follow-up", Title: "Trial period follow-up", Content: "Hello, I would like to check your trial experience over the past few days. Which features are used most often? Have you encountered anything hard to understand, complex to configure, or unstable in effect?", Status: enums.StatusOk, SortNo: 55},
		}
	}
	return []QuickReplySeed{
		{ID: 1, GroupName: "新客接待", Title: "首次接入欢迎语", Content: "您好，欢迎来到 AgentDesk 客服中心，我是今天为您服务的顾问。您可以直接告诉我您想了解的产品、价格或接入方式，我这边先帮您快速判断。", Status: enums.StatusOk, SortNo: 100},
		{ID: 2, GroupName: "产品咨询", Title: "产品能力概览", Content: "我们当前支持智能问答、知识库检索、会话转人工、标签体系、快捷回复和客服工作台管理。如果您已经有业务场景，我可以按场景给您拆方案。", Status: enums.StatusOk, SortNo: 95},
		{ID: 3, GroupName: "产品咨询", Title: "部署方式说明", Content: "系统支持私有化部署和云端部署两种方式。若您对数据合规要求较高，建议优先评估私有化；如果希望快速上线，可以先从云端版本开始。", Status: enums.StatusOk, SortNo: 90},
		{ID: 4, GroupName: "报价跟进", Title: "报价前信息收集", Content: "为了给您更准确的报价，麻烦提供一下预计坐席人数、日均会话量、是否需要知识库和是否有私有化部署需求，我整理后尽快给您反馈。", Status: enums.StatusOk, SortNo: 85},
		{ID: 5, GroupName: "报价跟进", Title: "报价已发送提醒", Content: "您好，方案和报价单已经发送给您了，您方便的时候可以先看下。若您希望我同步讲解功能边界、实施周期和交付方式，我可以直接给您安排。", Status: enums.StatusOk, SortNo: 80},
		{ID: 6, GroupName: "实施上线", Title: "排查前确认信息", Content: "收到，我先帮您排查。麻烦补充一下问题出现时间、影响范围、具体报错截图，以及最近是否做过配置调整，这样能更快定位。", Status: enums.StatusOk, SortNo: 75},
		{ID: 7, GroupName: "实施上线", Title: "配置生效说明", Content: "配置已经更新完成，通常 1 到 3 分钟内会生效。建议您刷新页面后重新发起一次测试，如果还有异常，我继续跟进处理。", Status: enums.StatusOk, SortNo: 70},
		{ID: 8, GroupName: "售后支持", Title: "问题升级告知", Content: "这个问题我已经记录并升级给技术同学处理，当前优先级已标为高。预计今天内会给您第一轮结论，有进展我会第一时间同步。", Status: enums.StatusOk, SortNo: 65},
		{ID: 9, GroupName: "售后支持", Title: "版本更新通知模板", Content: "您好，本周四晚间会进行一次版本更新，主要涉及知识库检索优化和工作台体验改进。更新期间可能出现短时波动，我们会提前做好回滚预案。", Status: enums.StatusOk, SortNo: 60},
		{ID: 10, GroupName: "回访运营", Title: "试用期回访", Content: "您好，想跟您确认一下这几天的试用体验。当前最常用的是哪几个功能？有没有遇到理解成本高、配置复杂或效果不稳定的地方？", Status: enums.StatusOk, SortNo: 55},
	}
}
