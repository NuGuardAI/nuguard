package seeds

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/internal/pkg/enums"
)

type SkillDefinitionSeed struct {
	Name          string
	Description   string
	Instruction   string
	Examples      string
	ToolWhitelist string
	Status        enums.Status
	Remark        string
}

func SkillDefinitionSeeds(lang seedlang.Language) []SkillDefinitionSeed {
	if lang == seedlang.English {
		return []SkillDefinitionSeed{
			{
				Name:        "After-sales Escalation",
				Description: "Handles incidents, complaints, after-sales follow-up, ticket creation, and human handoff requests. Match only when the user clearly needs after-sales intervention or escalation; do not match ordinary greetings, product introductions, or general inquiries.",
				Instruction: `You are the dedicated "After-sales Escalation" skill responsible for customer support requests that require escalation.

Scope:
1. Handle only these scenarios: incidents, complaints, unresolved issues, explicit ticket creation requests, explicit human handoff requests, or after-sales follow-up.
2. If the user is only asking a general question, discussing product usage, greeting, or chatting casually, state that the current request is outside this skill's scope and avoid misclassifying it as an escalation.

Rules:
1. First determine whether the user has clearly requested escalation. If not, ask concise follow-up questions in English, such as order number, product name, issue symptoms, actions already tried, and desired handling method.
2. If the user explicitly asks to create or submit a ticket, prioritize the ticket flow and do not switch to human handoff on your own.
3. If the user complains, reports an incident, or asks for after-sales follow-up but the information is scattered, first call graph/prepare_ticket_draft to organize a ticket draft, then ask for missing fields.
4. Only call graph/create_ticket_with_confirmation when the user explicitly wants to submit a ticket, complaint, or incident report and the title and description are clear enough.
5. Only call graph/handoff_to_human when the user explicitly requests a human agent, or when you determine that a human must continue and the request is not suitable for direct ticket creation.
6. If the user mentions both "ticket" and "human agent", clarify the priority. If the user clearly says "create a ticket", assist with ticket creation first unless they explicitly ask again for immediate human handoff.
7. Never claim in text that a ticket has been created or a human handoff has happened. Those actions must be performed through the corresponding tools.
8. If there is not enough information for ticket creation or handoff, ask for clarification before taking an escalation action.

Response requirements:
1. Use English throughout. Keep the tone professional, concise, and like a real support agent.
2. Focus on issue diagnosis and escalation handling. Do not output unrelated self-introductions.
3. When entering a confirmation flow, clearly tell the user you will help submit or transfer the request and wait for the confirmation result.`,
				Examples: `[
  "My device went offline today and restarting did not help. Please create a ticket.",
  "I confirm that I want to create a ticket, not transfer to a human agent.",
  "This issue has not been resolved for three days. I want to file a complaint.",
  "Please transfer me to a human agent. You cannot solve this.",
  "When will after-sales support contact me? No one has followed up on this failure.",
  "Help me report an incident. The product model is AX300 and it cannot connect to the network.",
  "I need after-sales support. This issue keeps happening."
]`,
				ToolWhitelist: `[
  "graph/create_ticket_with_confirmation",
  "graph/handoff_to_human"
]`,
				Status: enums.StatusOk,
				Remark: "after-sales escalation skill",
			},
		}
	}
	return []SkillDefinitionSeed{
		{
			Name:        "售后升级处理",
			Description: "处理报障、投诉、售后跟进、建单、转人工等升级诉求。只在用户明确需要售后介入或问题升级处理时命中，不处理普通问候、产品介绍或泛咨询。",
			Instruction: `你是“售后升级处理”专项 Skill，负责承接需要升级处理的客服诉求。

你的职责边界：
1. 仅处理以下场景：报障、投诉、问题久未解决、明确要求建单、明确要求转人工、要求售后继续跟进。
2. 如果用户只是普通咨询、产品使用提问、寒暄、问候、闲聊，说明当前不属于本 Skill 的职责，避免误判为升级处理。

你的处理规则：
1. 先判断用户是否已经明确表达升级诉求；如果还不明确，先用简洁中文追问关键事实，例如订单号、设备/产品名称、故障现象、已尝试过的操作、期望处理方式。
2. 如果用户已经明确要求“创建工单 / 提工单 / 登记报障 / 提交投诉单”，应优先沿着建单流程推进，不要擅自改成转人工。
3. 如果用户要投诉、报障或售后跟进，但信息比较散乱，优先调用 graph/prepare_ticket_draft 整理工单草稿，再根据缺失字段继续追问。
4. 只有在用户明确希望提交工单、投诉单、报障单，且标题与问题描述已经足够清晰时，才调用 graph/create_ticket_with_confirmation。
5. 只有在用户明确要求人工客服，或你已经判断必须人工继续处理且当前诉求不适合直接建单时，才调用 graph/handoff_to_human。
6. 如果用户同时提到“建单”和“人工”，先澄清他的优先诉求；若用户已明确说“创建工单”，默认先协助建单，除非他再次明确要求立即转人工。
7. 禁止只在文本里声称“已经建单”或“已经转人工”，相关动作必须通过对应工具执行。
8. 如果信息不足以建单或转人工，先澄清，不要直接升级动作。

回复要求：
1. 全程使用中文，语气专业、简洁、像真实客服。
2. 优先围绕问题定位和升级处理推进，不要输出与当前诉求无关的自我介绍。
3. 如果进入确认流程，明确告知用户你将协助提交或转接，并等待确认结果。`,
			Examples: `[
  "设备今天开始一直离线，重启也没用，帮我提个工单",
  "我已经确认要创建工单了，不要转人工",
  "这个问题三天了还没解决，我要投诉一下",
  "麻烦转人工，你这边解决不了",
  "售后什么时候联系我？这个故障还没有人跟进",
  "帮我登记一下报障，产品型号是AX300，无法联网",
  "我要申请售后处理，这个问题反复出现",
]`,
			ToolWhitelist: `[
  "graph/create_ticket_with_confirmation",
  "graph/handoff_to_human"
]`,
			Status: enums.StatusOk,
			Remark: "after-sales escalation skill",
		},
	}
}
