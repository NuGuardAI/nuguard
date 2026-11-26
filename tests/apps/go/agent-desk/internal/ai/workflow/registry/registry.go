package registry

import "agent-desk/internal/ai/workflow/dsl"

const (
	NodeTypeStart                     = "start"
	NodeTypeConversationUnderstanding = "conversation_understanding"
	NodeTypeReplyPolicy               = "reply_policy"
	NodeTypeKnowledgeRetrieve         = "knowledge_retrieve"
	NodeTypeAnswerabilityGate         = "answerability_gate"
	NodeTypeLLMReply                  = "llm_reply"
	NodeTypeLLM                       = "llm"
	NodeTypeHTTP                      = "http"
	NodeTypeCode                      = "code"
	NodeTypeVariable                  = "variable"
	NodeTypeMultiCondition            = "multi-condition"
	NodeTypeLoop                      = "loop"
	NodeTypeBlockStart                = "block-start"
	NodeTypeBlockEnd                  = "block-end"
	NodeTypeComment                   = "comment"
	NodeTypeContinue                  = "continue"
	NodeTypeBreak                     = "break"
	NodeTypeGroup                     = "group"
	NodeTypeCondition                 = "condition"
	NodeTypeAnalyzeConversation       = "analyze_conversation"
	NodeTypePrepareTicketDraft        = "prepare_ticket_draft"
	NodeTypeHumanConfirm              = "human_confirm"
	NodeTypeCreateTicket              = "create_ticket"
	NodeTypeHandoffToHuman            = "handoff_to_human"
	NodeTypeSendReply                 = "send_reply"
	NodeTypeEnd                       = "end"
)

func DefaultRegistry() *Registry {
	return NewRegistry(
		NodeSpec{
			Type:        NodeTypeStart,
			Title:       "Start",
			Description: "Conversation workflow entry.",
			Icon:        "PlayCircleIcon",
			RiskLevel:   NodeRiskLevelLow,
			OutputSchema: []VariableSpec{
				output("conversationId", "会话 ID", VariableTypeInteger, "当前客户会话的内部编号。"),
				output("messageId", "消息 ID", VariableTypeInteger, "客户本轮消息的内部编号。"),
				output("aiAgentId", "AI Agent ID", VariableTypeInteger, "当前处理会话的 AI Agent 编号。"),
				output("userMessage", "用户消息", VariableTypeString, "客户本轮发送的原始消息内容。"),
			},
		},
		NodeSpec{
			Type:        NodeTypeConversationUnderstanding,
			Title:       "Conversation Understanding",
			Description: "Classify customer message intent and answer scope before retrieval.",
			Icon:        "MessageCircleIcon",
			RiskLevel:   NodeRiskLevelLow,
			InputSchema: []VariableSpec{
				requiredInput("userMessage", "用户消息", VariableTypeString, "客户本轮发送的原始消息内容。"),
			},
			OutputSchema: []VariableSpec{
				output("normalizedMessage", "规范化消息", VariableTypeString, "经过清洗和规范化后的客户消息。"),
				enumOutput("messageIntent", "消息意图", "客户消息的意图分类。", []VariableValueOption{
					valueOption("unknown", "未知意图", "系统暂时无法判断客户意图。"),
					valueOption("greeting", "打招呼", "客户在问候或开始对话。"),
					valueOption("thanks", "表达感谢", "客户在表示感谢。"),
					valueOption("end_conversation", "结束会话", "客户表示问题已处理或准备结束。"),
					valueOption("confirmation", "确认操作", "客户对上一步操作进行确认。"),
					valueOption("handoff_request", "要求人工", "客户明确要求转人工处理。"),
					valueOption("complaint", "投诉升级", "客户表达投诉、举报、起诉等升级风险。"),
					valueOption("ticket_request", "要求建单", "客户希望创建或跟进工单。"),
					valueOption("ambiguous_question", "问题不明确", "客户问题缺少必要上下文，需要追问。"),
					valueOption("business_question", "业务问题", "客户问题适合进入知识库检索。"),
				}),
				enumOutput("answerScope", "回复策略", "系统建议采用的回复处理范围。", []VariableValueOption{
					valueOption("direct_reply", "直接回复客户", "无需检索知识库或转人工，可以直接生成回复。"),
					valueOption("needs_clarification", "追问补充信息", "当前信息不足，需要客户补充。"),
					valueOption("needs_handoff", "转人工处理", "需要人工客服介入。"),
					valueOption("needs_ticket", "创建工单", "需要进入工单处理流程。"),
					valueOption("needs_knowledge", "检索知识库", "需要先检索知识库再回答。"),
				}),
				output("confidence", "置信度", VariableTypeNumber, "意图和回复策略判断的置信度。"),
				output("riskSignals", "风险信号", VariableTypeStringArray, "识别到的投诉、升级、人工介入等风险线索。"),
				output("reason", "判断原因", VariableTypeString, "本次意图和回复策略判断的原因说明。"),
			},
			DefaultInputs: map[string]dsl.Value{
				"userMessage": dsl.RefValue("start_1", "userMessage"),
			},
		},
		NodeSpec{
			Type:        NodeTypeReplyPolicy,
			Title:       "Reply Policy",
			Description: "Decide the next customer-service action from understanding output and agent policy.",
			Icon:        "ShieldCheckIcon",
			RiskLevel:   NodeRiskLevelLow,
			InputSchema: []VariableSpec{
				requiredInput("messageIntent", "消息意图", VariableTypeString, "上游理解节点识别出的客户消息意图。"),
				requiredInput("answerScope", "回复策略", VariableTypeString, "上游理解节点建议采用的回复处理范围。"),
				optionalInput("userMessage", "用户消息", VariableTypeString, "客户本轮发送的原始消息内容。"),
				optionalInput("riskSignals", "风险信号", VariableTypeStringArray, "上游识别到的风险线索列表。"),
				optionalInput("answerability", "可回答性", VariableTypeString, "知识库结果是否足够支撑回答的判断。"),
			},
			OutputSchema: []VariableSpec{
				enumOutput("action", "处理策略", "回复策略节点选择的下一步处理动作。", []VariableValueOption{
					valueOption("direct_reply", "直接回复客户", "直接发送策略节点生成的回复。"),
					valueOption("clarify", "追问补充信息", "先让客户补充必要信息。"),
					valueOption("end_conversation", "结束会话", "发送结束语并结束本轮处理。"),
					valueOption("handoff_to_human", "转人工", "进入人工接待流程。"),
					valueOption("prepare_ticket", "创建工单", "整理工单草稿并等待确认。"),
					valueOption("retrieve_knowledge", "检索知识库", "进入知识检索和 AI 回复流程。"),
					valueOption("knowledge_fallback", "知识库兜底", "知识库结果不足，发送兜底回复。"),
				}),
				output("replyText", "回复内容", VariableTypeString, "可直接发送给客户的回复文本。"),
				output("reason", "策略原因", VariableTypeString, "选择当前处理策略的原因说明。"),
				output("requiresFlow", "需要继续流程", VariableTypeBoolean, "是否需要继续执行后续工作流节点。"),
				enumOutput("targetFlow", "目标流程", "建议继续执行的业务流程。", []VariableValueOption{
					valueOption("handoff_to_human", "转人工流程", "继续执行转人工节点。"),
					valueOption("prepare_ticket", "工单流程", "继续执行工单草稿和确认节点。"),
					valueOption("knowledge", "知识库流程", "继续执行知识检索节点。"),
				}),
				enumOutput("finalReplySource", "回复来源", "最终回复内容的来源类别。", []VariableValueOption{
					valueOption("direct_reply", "策略直接回复", "由回复策略节点直接生成回复。"),
					valueOption("clarification", "追问回复", "用于追问客户补充信息。"),
					valueOption("handoff_notice", "转人工提示", "用于提示客户已进入人工处理。"),
					valueOption("ticket_result", "工单结果", "用于提示建单结果。"),
					valueOption("knowledge_answer", "知识库回答", "用于发送基于知识库生成的回复。"),
					valueOption("knowledge_fallback", "知识库兜底", "用于知识库信息不足时的兜底回复。"),
				}),
			},
		},
		NodeSpec{
			Type:        NodeTypeKnowledgeRetrieve,
			Title:       "Knowledge Retrieve",
			Description: "Retrieve knowledge for the current user message.",
			Icon:        "BookOpenIcon",
			RiskLevel:   NodeRiskLevelLow,
			ConfigSchema: map[string]any{
				"knowledgeBaseIds": map[string]any{
					"type":        string(VariableTypeIntegerArray),
					"label":       "知识库",
					"required":    true,
					"description": "本节点检索时使用的知识库列表，按顺序表示优先级。",
				},
			},
			InputSchema: []VariableSpec{
				requiredInput("query", "检索问题", VariableTypeString, "用于检索知识库的客户问题或查询文本。"),
			},
			OutputSchema: []VariableSpec{
				output("items", "知识条目", VariableTypeObjectArray, "从知识库命中的原始知识条目列表。"),
				output("summary", "检索摘要", VariableTypeString, "对本次知识检索结果的简短摘要。"),
			},
			DefaultInputs: map[string]dsl.Value{
				"query": dsl.RefValue("start_1", "userMessage"),
			},
		},
		NodeSpec{
			Type:        NodeTypeAnswerabilityGate,
			Title:       "Answerability Gate",
			Description: "Decide whether retrieved knowledge is enough to answer.",
			Icon:        "HelpCircleIcon",
			RiskLevel:   NodeRiskLevelLow,
			InputSchema: []VariableSpec{
				requiredInput("userMessage", "用户消息", VariableTypeString, "客户本轮发送的原始消息内容。"),
				requiredInput("knowledgeItems", "知识条目", VariableTypeObjectArray, "上游知识检索节点命中的知识条目列表。"),
			},
			OutputSchema: []VariableSpec{
				enumOutput("answerability", "可回答性", "知识库结果是否足够支撑回答的判断。", []VariableValueOption{
					valueOption("answerable", "可以回答", "检索结果足够支撑回答。"),
					valueOption("unanswerable", "无法回答", "检索结果不足，应该走兜底或追问。"),
				}),
				output("reason", "判断原因", VariableTypeString, "可回答性判断的原因说明。"),
			},
		},
		NodeSpec{
			Type:        NodeTypeLLMReply,
			Title:       "LLM Reply",
			Description: "Generate a reply or structured analysis with the configured model.",
			Icon:        "BotIcon",
			RiskLevel:   NodeRiskLevelMedium,
			InputSchema: []VariableSpec{
				requiredInput("userMessage", "用户消息", VariableTypeString, "客户本轮发送的原始消息内容。"),
				optionalInput("knowledgeItems", "知识条目", VariableTypeObjectArray, "可用于生成回复的知识库检索结果。"),
			},
			OutputSchema: []VariableSpec{
				output("replyText", "回复内容", VariableTypeString, "大模型生成的客户可见回复文本。"),
			},
		},
		NodeSpec{
			Type:        NodeTypeCondition,
			Title:       "Condition",
			Description: "Route by controlled workflow variables.",
			Icon:        "GitBranchIcon",
			RiskLevel:   NodeRiskLevelLow,
			OutputSchema: []VariableSpec{
				output("matched", "是否命中", VariableTypeBoolean, "条件节点是否命中了某个条件分支。"),
			},
		},
		NodeSpec{
			Type:        NodeTypeAnalyzeConversation,
			Title:       "Analyze Conversation",
			Description: "Analyze intent, risk, and recommended next action.",
			Icon:        "SearchIcon",
			RiskLevel:   NodeRiskLevelLow,
			InputSchema: []VariableSpec{
				requiredInput("userMessage", "用户消息", VariableTypeString, "客户本轮发送的原始消息内容。"),
			},
			OutputSchema: []VariableSpec{
				output("intent", "用户意图", VariableTypeString, "从会话中识别出的客户意图。"),
				output("riskLevel", "风险等级", VariableTypeString, "本轮会话的风险等级判断。"),
				output("needTicket", "需要工单", VariableTypeBoolean, "是否建议进入工单处理流程。"),
				output("needHumanHandoff", "需要转人工", VariableTypeBoolean, "是否建议转人工客服处理。"),
			},
		},
		NodeSpec{
			Type:        NodeTypePrepareTicketDraft,
			Title:       "Prepare Ticket Draft",
			Description: "Build a ticket draft from conversation context.",
			Icon:        "ClipboardListIcon",
			RiskLevel:   NodeRiskLevelMedium,
			InputSchema: []VariableSpec{
				requiredInput("issue", "问题摘要", VariableTypeString, "需要整理进工单的客户问题摘要。"),
			},
			OutputSchema: []VariableSpec{
				output("ticketDraft", "工单草稿", VariableTypeObject, "根据会话内容整理出的待确认工单草稿。"),
				output("ready", "草稿就绪", VariableTypeBoolean, "工单草稿是否已具备创建所需的关键信息。"),
				output("title", "工单标题", VariableTypeString, "工单草稿标题。"),
				output("description", "工单描述", VariableTypeString, "工单草稿描述。"),
				output("missingFields", "缺失字段", VariableTypeStringArray, "仍需客户补充的字段列表。"),
				output("followUpQuestions", "追问问题", VariableTypeStringArray, "用于补齐工单信息的追问问题。"),
			},
		},
		NodeSpec{
			Type:          NodeTypeHumanConfirm,
			Title:         "Human Confirm",
			Description:   "Interrupt and wait for explicit user confirmation.",
			Icon:          "UserCheckIcon",
			RiskLevel:     NodeRiskLevelMedium,
			Interruptible: true,
			InputSchema: []VariableSpec{
				requiredInput("prompt", "确认提示", VariableTypeString, "发送给客户用于确认操作的提示文本。"),
			},
			OutputSchema: []VariableSpec{
				output("confirmed", "已确认", VariableTypeBoolean, "客户是否明确确认继续执行。"),
				output("responseText", "确认回复", VariableTypeString, "客户针对确认提示给出的回复文本。"),
			},
		},
		NodeSpec{
			Type:                            NodeTypeCreateTicket,
			Title:                           "Create Ticket",
			Description:                     "Create a ticket from a confirmed draft.",
			Icon:                            "TicketIcon",
			RiskLevel:                       NodeRiskLevelHigh,
			RequiresConfirmationPredecessor: true,
			InputSchema: []VariableSpec{
				requiredInput("ticketDraft", "工单草稿", VariableTypeObject, "已经由客户确认的工单草稿内容。"),
				requiredInput("confirmed", "已确认", VariableTypeBoolean, "客户是否已确认创建工单。"),
				optionalInput("tagIds", "工单标签", VariableTypeIntegerArray, "创建工单时附加的标签 ID 列表。"),
				optionalInput("assigneeId", "处理人", VariableTypeInteger, "创建工单后默认指派的客服用户 ID。"),
			},
			OutputSchema: []VariableSpec{
				output("ticketId", "工单 ID", VariableTypeInteger, "创建成功后的工单内部编号。"),
				output("ticketNo", "工单编号", VariableTypeString, "创建成功后的客户可见工单编号。"),
				output("created", "已创建", VariableTypeBoolean, "工单是否已经成功创建。"),
				output("message", "结果消息", VariableTypeString, "发送给客户的工单创建结果说明。"),
			},
		},
		NodeSpec{
			Type:                            NodeTypeHandoffToHuman,
			Title:                           "Handoff To Human",
			Description:                     "Transfer the conversation to human support.",
			Icon:                            "HeadphonesIcon",
			RiskLevel:                       NodeRiskLevelHigh,
			RequiresConfirmationPredecessor: true,
			InputSchema: []VariableSpec{
				requiredInput("reason", "转人工原因", VariableTypeString, "触发转人工处理的业务原因。"),
				requiredInput("confirmed", "已确认", VariableTypeBoolean, "客户是否已确认转人工。"),
			},
			OutputSchema: []VariableSpec{
				output("handoffId", "转人工记录 ID", VariableTypeInteger, "本次转人工操作的内部记录编号。"),
				output("reason", "转人工原因", VariableTypeString, "本次转人工处理的原因说明。"),
				enumOutput("decision", "转人工结果", "转人工分配或排队结果。", []VariableValueOption{
					valueOption("assigned", "已分配客服", "已成功分配给人工客服。"),
					valueOption("team_pool", "团队队列等待", "暂未分配到客服，进入团队等待队列。"),
					valueOption("global_pool", "全局队列等待", "非服务时间或无可用团队，进入全局等待队列。"),
					valueOption("off_hours", "非服务时间", "当前不在人工客服服务时间内。"),
					valueOption("cancelled", "已取消转人工", "由于未确认或条件不满足，未执行转人工。"),
				}),
				output("teamId", "客服组 ID", VariableTypeInteger, "已分配或等待中的客服组编号。"),
				output("assigneeId", "客服 ID", VariableTypeInteger, "已分配的人工客服用户编号。"),
				output("message", "转人工提示", VariableTypeString, "发送给客户的转人工结果提示。"),
			},
		},
		NodeSpec{
			Type:        NodeTypeSendReply,
			Title:       "Send Reply",
			Description: "Return or commit customer-visible reply text.",
			Icon:        "SendIcon",
			RiskLevel:   NodeRiskLevelLow,
			InputSchema: []VariableSpec{
				requiredInput("replyText", "回复内容", VariableTypeString, "将发送或返回给客户的最终回复文本。"),
			},
			OutputSchema: []VariableSpec{
				output("sent", "已发送", VariableTypeBoolean, "回复是否已经成功发送或返回。"),
				output("replyMessageId", "回复消息 ID", VariableTypeInteger, "发送成功后的回复消息编号。"),
			},
		},
		NodeSpec{
			Type:        NodeTypeEnd,
			Title:       "End",
			Description: "End workflow execution.",
			Icon:        "FlagIcon",
			RiskLevel:   NodeRiskLevelLow,
			OutputSchema: []VariableSpec{
				output("status", "结束状态", VariableTypeString, "工作流执行结束时的状态。"),
			},
		},
		NodeSpec{
			Type:        NodeTypeLLM,
			Title:       "LLM",
			Description: "Call the large language model and generate responses.",
			RiskLevel:   NodeRiskLevelLow,
			OutputSchema: []VariableSpec{
				output("result", "Result", VariableTypeString, "The generated model response."),
			},
		},
		officialNodeSpec(NodeTypeHTTP, "HTTP", "Send an HTTP request."),
		officialNodeSpec(NodeTypeCode, "Code", "Run JavaScript code."),
		officialNodeSpec(NodeTypeVariable, "Variable", "Assign workflow variables."),
		officialNodeSpec(NodeTypeMultiCondition, "Multi Condition", "Route through multiple condition branches."),
		officialNodeSpec(NodeTypeLoop, "Loop", "Iterate over an array in a sub-canvas."),
		officialNodeSpec(NodeTypeBlockStart, "Block Start", "Start a container block."),
		officialNodeSpec(NodeTypeBlockEnd, "Block End", "End a container block."),
		officialNodeSpec(NodeTypeComment, "Comment", "Add a canvas annotation."),
		officialNodeSpec(NodeTypeContinue, "Continue", "Continue the current loop."),
		officialNodeSpec(NodeTypeBreak, "Break", "Break the current loop."),
		officialNodeSpec(NodeTypeGroup, "Group", "Group related workflow nodes."),
	)
}

func officialNodeSpec(nodeType string, title string, description string) NodeSpec {
	return NodeSpec{
		Type:        nodeType,
		Title:       title,
		Description: description,
		Icon:        "",
		RiskLevel:   NodeRiskLevelLow,
	}
}

func requiredInput(name string, label string, variableType VariableType, description string) VariableSpec {
	return VariableSpec{Name: name, Label: label, Type: variableType, Required: true, Description: description}
}

func optionalInput(name string, label string, variableType VariableType, description string) VariableSpec {
	return VariableSpec{Name: name, Label: label, Type: variableType, Description: description}
}

func output(name string, label string, variableType VariableType, description string) VariableSpec {
	return VariableSpec{Name: name, Label: label, Type: variableType, Description: description}
}

func enumOutput(name string, label string, description string, options []VariableValueOption) VariableSpec {
	return VariableSpec{
		Name:         name,
		Label:        label,
		Type:         VariableTypeString,
		Description:  description,
		Operators:    []string{"eq", "neq"},
		ValueOptions: options,
	}
}

func valueOption(value any, label string, description string) VariableValueOption {
	return VariableValueOption{Value: value, Label: label, Description: description}
}
