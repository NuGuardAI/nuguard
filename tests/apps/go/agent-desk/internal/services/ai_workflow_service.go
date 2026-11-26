package services

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"time"

	"agent-desk/internal/ai/workflow/dsl"
	workflowregistry "agent-desk/internal/ai/workflow/registry"
	workflowvalidator "agent-desk/internal/ai/workflow/validator"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/httpx/params"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
)

var AIWorkflowService = newAIWorkflowService()

func newAIWorkflowService() *aiWorkflowService {
	return &aiWorkflowService{
		registry: workflowregistry.DefaultRegistry(),
	}
}

type aiWorkflowService struct {
	registry *workflowregistry.Registry
}

type AIWorkflowRunAuditItem struct {
	Run      models.AIWorkflowRun
	Workflow *models.AIWorkflow
	Version  *models.AIWorkflowVersion
	Agent    *models.AIAgent
}

type AIWorkflowTemplate struct {
	Code        string
	Name        string
	Description string
	Definition  dsl.Definition
}

type AIWorkflowUsageItem struct {
	Binding models.AIAgentWorkflowBinding
	Agent   *models.AIAgent
	Version *models.AIWorkflowVersion
}

func (s *aiWorkflowService) Get(id int64) *models.AIWorkflow {
	if id <= 0 {
		return nil
	}
	return repositories.AIWorkflowRepository.Get(sqls.DB(), id)
}

func (s *aiWorkflowService) GetVersion(id int64) *models.AIWorkflowVersion {
	if id <= 0 {
		return nil
	}
	return repositories.AIWorkflowVersionRepository.Get(sqls.DB(), id)
}

func (s *aiWorkflowService) FindPageByCnd(cnd *sqls.Cnd) (list []models.AIWorkflow, paging *sqls.Paging) {
	return repositories.AIWorkflowRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *aiWorkflowService) FindVersionPageByParams(params *params.QueryParams) (list []models.AIWorkflowVersion, paging *sqls.Paging) {
	return repositories.AIWorkflowVersionRepository.FindPageByParams(sqls.DB(), params)
}

func (s *aiWorkflowService) FindRunPageByCnd(cnd *sqls.Cnd) (list []models.AIWorkflowRun, paging *sqls.Paging) {
	return repositories.AIWorkflowRunRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *aiWorkflowService) BuildRunAuditItems(list []models.AIWorkflowRun) []AIWorkflowRunAuditItem {
	ret := make([]AIWorkflowRunAuditItem, 0, len(list))
	if len(list) == 0 {
		return ret
	}
	workflowIDs := make([]int64, 0, len(list))
	versionIDs := make([]int64, 0, len(list))
	agentIDs := make([]int64, 0, len(list))
	for _, item := range list {
		workflowIDs = appendNonZeroInt64(workflowIDs, item.WorkflowID)
		versionIDs = appendNonZeroInt64(versionIDs, item.WorkflowVersionID)
		agentIDs = appendNonZeroInt64(agentIDs, item.AIAgentID)
	}
	var workflows []models.AIWorkflow
	if len(workflowIDs) > 0 {
		workflows = repositories.AIWorkflowRepository.Find(sqls.DB(), sqls.NewCnd().In("id", workflowIDs))
	}
	var versions []models.AIWorkflowVersion
	if len(versionIDs) > 0 {
		versions = repositories.AIWorkflowVersionRepository.Find(sqls.DB(), sqls.NewCnd().In("id", versionIDs))
	}
	var agents []models.AIAgent
	if len(agentIDs) > 0 {
		agents = repositories.AIAgentRepository.Find(sqls.DB(), sqls.NewCnd().In("id", agentIDs))
	}
	workflowByID := make(map[int64]*models.AIWorkflow, len(workflows))
	for i := range workflows {
		item := workflows[i]
		workflowByID[item.ID] = &item
	}
	versionByID := make(map[int64]*models.AIWorkflowVersion, len(versions))
	for i := range versions {
		item := versions[i]
		versionByID[item.ID] = &item
	}
	agentByID := make(map[int64]*models.AIAgent, len(agents))
	for i := range agents {
		item := agents[i]
		agentByID[item.ID] = &item
	}
	for _, run := range list {
		ret = append(ret, AIWorkflowRunAuditItem{
			Run:      run,
			Workflow: workflowByID[run.WorkflowID],
			Version:  versionByID[run.WorkflowVersionID],
			Agent:    agentByID[run.AIAgentID],
		})
	}
	return ret
}

func (s *aiWorkflowService) GetRunDetail(id int64) (*models.AIWorkflowRun, []models.AIWorkflowNodeRun) {
	if id <= 0 {
		return nil, nil
	}
	run := repositories.AIWorkflowRunRepository.Get(sqls.DB(), id)
	if run == nil {
		return nil, nil
	}
	nodes := repositories.AIWorkflowNodeRunRepository.Find(sqls.DB(), sqls.NewCnd().Eq("workflow_run_id", id).Asc("id"))
	return run, nodes
}

func appendNonZeroInt64(list []int64, value int64) []int64 {
	if value <= 0 {
		return list
	}
	for _, item := range list {
		if item == value {
			return list
		}
	}
	return append(list, value)
}

func (s *aiWorkflowService) ListNodeSpecs() []workflowregistry.NodeSpec {
	return s.registry.List()
}

func (s *aiWorkflowService) DefaultAgentWorkflowDefinition() dsl.Definition {
	return defaultAgentWorkflowDefinition()
}

func (s *aiWorkflowService) ListWorkflowTemplates() []AIWorkflowTemplate {
	return []AIWorkflowTemplate{
		{Code: "ticket-with-confirmation", Name: "创建工单", Description: "整理工单草稿，经客户确认后创建工单。", Definition: ticketWithConfirmationWorkflowDefinition()},
		{Code: "identity-confirmation", Name: "身份确认", Description: "在执行后续业务前收集客户的明确确认。", Definition: identityConfirmationWorkflowDefinition()},
		{Code: "complaint-escalation", Name: "投诉升级", Description: "投诉场景经客户确认后转入人工客服处理。", Definition: complaintEscalationWorkflowDefinition()},
		{Code: "refund-request-preparation", Name: "退款申请准备", Description: "整理退款诉求，确认后转人工继续核验和处理。", Definition: refundRequestPreparationWorkflowDefinition()},
	}
}

func ticketWithConfirmationWorkflowDefinition() dsl.Definition {
	return dsl.Definition{SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			workflowNode("start_1", workflowregistry.NodeTypeStart, "开始", 180, 180, nil, nil),
			workflowNode("draft_1", workflowregistry.NodeTypePrepareTicketDraft, "整理工单草稿", 600, 180, workflowInputs("issue", "start_1", "userMessage"), nil),
			workflowNode("ready_route_1", workflowregistry.NodeTypeCondition, "草稿分流", 1020, 180, nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				workflowConditionBranch("ready", "草稿完整", "prompt_1", "draft_1", "ready", "is_true", nil),
				{ID: "default", Name: "补充信息", TargetNodeID: "followup_1", Default: true},
			}}),
			workflowNode("prompt_1", workflowregistry.NodeTypeLLMReply, "建单确认", 1440, 100, map[string]dsl.Value{"userMessage": dsl.RefValue("start_1", "userMessage"), "ticketTitle": dsl.RefValue("draft_1", "title"), "ticketDescription": dsl.RefValue("draft_1", "description")}, map[string]any{"staticReply": "我已整理工单草稿：{{ticketTitle}}。请确认是否创建。"}),
			workflowNode("confirm_1", workflowregistry.NodeTypeHumanConfirm, "确认建单", 1860, 100, workflowInputs("prompt", "prompt_1", "replyText"), nil),
			workflowNode("confirm_route_1", workflowregistry.NodeTypeCondition, "确认分流", 2280, 100, nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				workflowConditionBranch("confirmed", "已确认", "create_1", "confirm_1", "confirmed", "is_true", nil),
				{ID: "default", Name: "取消", TargetNodeID: "cancel_1", Default: true},
			}}),
			workflowNode("create_1", workflowregistry.NodeTypeCreateTicket, "创建工单", 2700, 20, map[string]dsl.Value{"ticketDraft": dsl.RefValue("draft_1", "ticketDraft"), "confirmed": dsl.RefValue("confirm_1", "confirmed")}, nil),
			workflowNode("followup_1", workflowregistry.NodeTypeLLMReply, "补充信息", 1440, 330, map[string]dsl.Value{"userMessage": dsl.RefValue("start_1", "userMessage"), "followUpQuestions": dsl.RefValue("draft_1", "followUpQuestions")}, map[string]any{"staticReply": "创建工单前还需要补充：{{followUpQuestions}}"}),
			workflowNode("cancel_1", workflowregistry.NodeTypeLLMReply, "取消提示", 2700, 200, workflowInputs("userMessage", "start_1", "userMessage"), map[string]any{"staticReply": "已取消创建工单。"}),
			workflowNode("send_result_1", workflowregistry.NodeTypeSendReply, "发送建单结果", 3120, 20, workflowInputs("replyText", "create_1", "message"), nil),
			workflowNode("send_followup_1", workflowregistry.NodeTypeSendReply, "发送补充提示", 1860, 330, workflowInputs("replyText", "followup_1", "replyText"), nil),
			workflowNode("send_cancel_1", workflowregistry.NodeTypeSendReply, "发送取消提示", 3120, 200, workflowInputs("replyText", "cancel_1", "replyText"), nil),
			workflowNode("end_1", workflowregistry.NodeTypeEnd, "结束", 3540, 180, nil, nil),
		},
		Edges: []dsl.Edge{
			workflowEdge("start_1", "draft_1"), workflowEdge("draft_1", "ready_route_1"), workflowPortEdge("ready_route_1", "prompt_1", "ready"), workflowPortEdge("ready_route_1", "followup_1", "default"),
			workflowEdge("prompt_1", "confirm_1"), workflowEdge("confirm_1", "confirm_route_1"), workflowPortEdge("confirm_route_1", "create_1", "confirmed"), workflowPortEdge("confirm_route_1", "cancel_1", "default"),
			workflowEdge("create_1", "send_result_1"), workflowEdge("send_result_1", "end_1"), workflowEdge("followup_1", "send_followup_1"), workflowEdge("send_followup_1", "end_1"), workflowEdge("cancel_1", "send_cancel_1"), workflowEdge("send_cancel_1", "end_1"),
		},
	}
}

func (s *aiWorkflowService) ValidateDefinition(def dsl.Definition) workflowvalidator.Result {
	return workflowvalidator.ValidateDefinition(def, s.registry)
}

func (s *aiWorkflowService) CreateWorkflow(req request.CreateAIWorkflowRequest, operator *dto.AuthPrincipal) (*models.AIWorkflow, error) {
	if operator == nil {
		return nil, errorsx.UnauthorizedI18n("error.auth.expired")
	}
	name := strings.TrimSpace(req.Name)
	if name == "" {
		return nil, errorsx.InvalidParam("workflow name is required")
	}
	definition, err := marshalDefinition(req.Definition)
	if err != nil {
		return nil, err
	}
	item := &models.AIWorkflow{Name: name, Description: strings.TrimSpace(req.Description), Status: enums.StatusOk, DraftDefinition: definition, AuditFields: utils.BuildAuditFields(operator)}
	if err := repositories.AIWorkflowRepository.Create(sqls.DB(), item); err != nil {
		return nil, err
	}
	return item, nil
}

func (s *aiWorkflowService) UpdateWorkflow(req request.UpdateAIWorkflowRequest, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	if s.Get(req.ID) == nil {
		return errorsx.InvalidParamI18n("error.e0002")
	}
	name := strings.TrimSpace(req.Name)
	if name == "" {
		return errorsx.InvalidParam("workflow name is required")
	}
	definition, err := marshalDefinition(req.Definition)
	if err != nil {
		return err
	}
	return repositories.AIWorkflowRepository.Updates(sqls.DB(), req.ID, map[string]interface{}{
		"name":             name,
		"description":      strings.TrimSpace(req.Description),
		"draft_definition": definition,
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       time.Now(),
	})
}

func (s *aiWorkflowService) DeleteWorkflow(id int64, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	if s.Get(id) == nil {
		return errorsx.InvalidParamI18n("error.e0002")
	}
	if repositories.AIAgentWorkflowBindingRepository.CountByWorkflowID(sqls.DB(), id) > 0 {
		return errorsx.InvalidParam("workflow is still associated with an agent")
	}
	return repositories.AIWorkflowRepository.Updates(sqls.DB(), id, map[string]interface{}{
		"status":           enums.StatusDeleted,
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       time.Now(),
	})
}

func (s *aiWorkflowService) RestoreVersion(req request.RestoreAIWorkflowVersionRequest, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	workflow := s.Get(req.WorkflowID)
	version := s.GetVersion(req.WorkflowVersionID)
	if workflow == nil || version == nil || version.WorkflowID != workflow.ID {
		return errorsx.InvalidParamI18n("error.e0002")
	}
	return repositories.AIWorkflowRepository.Updates(sqls.DB(), workflow.ID, map[string]any{"draft_definition": version.Definition, "update_user_id": operator.UserID, "update_user_name": operator.Username, "updated_at": time.Now()})
}

func (s *aiWorkflowService) ListUsage(workflowID int64) []AIWorkflowUsageItem {
	bindings := repositories.AIAgentWorkflowBindingRepository.FindByWorkflowID(sqls.DB(), workflowID)
	ret := make([]AIWorkflowUsageItem, 0, len(bindings))
	for _, binding := range bindings {
		ret = append(ret, AIWorkflowUsageItem{Binding: binding, Agent: repositories.AIAgentRepository.Get(sqls.DB(), binding.AIAgentID), Version: repositories.AIWorkflowVersionRepository.Get(sqls.DB(), binding.WorkflowVersionID)})
	}
	return ret
}

func (s *aiWorkflowService) PublishWorkflow(req request.PublishAIWorkflowRequest, operator *dto.AuthPrincipal) (*models.AIWorkflowVersion, error) {
	if operator == nil {
		return nil, errorsx.UnauthorizedI18n("error.auth.expired")
	}
	workflow := s.Get(req.WorkflowID)
	if workflow == nil || workflow.Status == enums.StatusDeleted {
		return nil, errorsx.InvalidParamI18n("error.e0002")
	}
	result := s.ValidateDefinition(req.Definition)
	if !result.Valid {
		return nil, errorsx.InvalidParam("workflow definition is invalid")
	}
	definition, err := marshalDefinition(req.Definition)
	if err != nil {
		return nil, err
	}
	now := time.Now()
	var version *models.AIWorkflowVersion
	err = sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		nextVersion := repositories.AIWorkflowVersionRepository.MaxVersionByWorkflowID(ctx.Tx, req.WorkflowID) + 1
		version = &models.AIWorkflowVersion{
			WorkflowID:      req.WorkflowID,
			Version:         nextVersion,
			Status:          enums.StatusOk,
			Definition:      definition,
			DefinitionHash:  hashDefinition(definition),
			PublishedAt:     &now,
			PublishedByID:   operator.UserID,
			PublishedByName: operator.Username,
			AuditFields:     utils.BuildAuditFields(operator),
		}
		if err := repositories.AIWorkflowVersionRepository.Create(ctx.Tx, version); err != nil {
			return err
		}
		return repositories.AIWorkflowRepository.Updates(ctx.Tx, req.WorkflowID, map[string]interface{}{
			"draft_definition":     definition,
			"published_version_id": version.ID,
			"update_user_id":       operator.UserID,
			"update_user_name":     operator.Username,
			"updated_at":           now,
		})
	})
	if err != nil {
		return nil, err
	}
	return version, nil
}

func defaultAgentWorkflowDefinition() dsl.Definition {
	return officialDefaultAgentWorkflowDefinition()
}

func officialDefaultAgentWorkflowDefinition() dsl.Definition {
	return dsl.Definition{
		Nodes: []dsl.Node{
			{
				ID:   "start_0",
				Type: workflowregistry.NodeTypeStart,
				Meta: dsl.NodeMeta{Position: dsl.Position{X: 180, Y: 300}},
				Data: dsl.NodeData{
					Title:   "Start",
					Outputs: json.RawMessage(`{"type":"object","properties":{"query":{"type":"string","default":"Hello Flow."}}}`),
				},
			},
			{
				ID:   "llm_0",
				Type: workflowregistry.NodeTypeLLM,
				Meta: dsl.NodeMeta{Position: dsl.Position{X: 640, Y: 220}},
				Data: dsl.NodeData{
					Title: "LLM",
					InputsValues: map[string]dsl.Value{
						"modelName":   dsl.ConstantValue("gpt-3.5-turbo"),
						"apiKey":      dsl.ConstantValue("sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
						"apiHost":     dsl.ConstantValue("https://mock-ai-url/api/v3"),
						"temperature": dsl.ConstantValue(0.5),
						"systemPrompt": dsl.TemplateValue(
							"# Role\nYou are an AI assistant.\n",
						),
						"prompt": dsl.TemplateValue(""),
					},
					Inputs:  json.RawMessage(`{"type":"object","required":["modelName","apiKey","apiHost","temperature","prompt"],"properties":{"modelName":{"type":"string"},"apiKey":{"type":"string"},"apiHost":{"type":"string"},"temperature":{"type":"number"},"systemPrompt":{"type":"string","extra":{"formComponent":"prompt-editor"}},"prompt":{"type":"string","extra":{"formComponent":"prompt-editor"}}}}`),
					Outputs: json.RawMessage(`{"type":"object","properties":{"result":{"type":"string"}}}`),
				},
			},
			{
				ID:   "end_0",
				Type: workflowregistry.NodeTypeEnd,
				Meta: dsl.NodeMeta{Position: dsl.Position{X: 1100, Y: 300}},
				Data: dsl.NodeData{
					Title: "End",
					InputsValues: map[string]dsl.Value{
						"result": dsl.RefValue("llm_0", "result"),
					},
					Inputs: json.RawMessage(`{"type":"object","properties":{"result":{"type":"string"}}}`),
				},
			},
		},
		Edges: []dsl.Edge{
			{SourceNodeID: "start_0", TargetNodeID: "llm_0"},
			{SourceNodeID: "llm_0", TargetNodeID: "end_0"},
		},
		GlobalVariable: json.RawMessage(`{"type":"object","properties":{}}`),
	}
}

func legacyDefaultAgentWorkflowDefinition() dsl.Definition {
	return dsl.Definition{
		SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			workflowNode("start_1", workflowregistry.NodeTypeStart, "开始", 180, 285.5, nil, nil),
			workflowNode("understanding_1", workflowregistry.NodeTypeConversationUnderstanding, "会话理解", 640, 285.5, workflowInputs("userMessage", "start_1", "userMessage"), nil),
			workflowNode("policy_1", workflowregistry.NodeTypeReplyPolicy, "回复策略", 1100, 285.5, map[string]dsl.Value{
				"userMessage":   dsl.RefValue("start_1", "userMessage"),
				"messageIntent": dsl.RefValue("understanding_1", "messageIntent"),
				"answerScope":   dsl.RefValue("understanding_1", "answerScope"),
				"riskSignals":   dsl.RefValue("understanding_1", "riskSignals"),
			}, nil),
			workflowNode("policy_route_1", workflowregistry.NodeTypeCondition, "策略分流", 1560, 125.5, nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				workflowConditionBranch("handoff", "转人工", "handoff_confirm_prompt_1", "policy_1", "action", "eq", "handoff_to_human"),
				workflowConditionBranch("direct", "直接回复", "policy_reply_1", "policy_1", "action", "eq", "direct_reply"),
				workflowConditionBranch("clarify", "追问澄清", "policy_reply_1", "policy_1", "action", "eq", "clarify"),
				workflowConditionBranch("end_conversation", "结束语", "policy_reply_1", "policy_1", "action", "eq", "end_conversation"),
				workflowConditionBranch("ticket", "创建工单", "draft_ticket_1", "policy_1", "action", "eq", "prepare_ticket"),
				workflowConditionBranch("knowledge", "知识库回复", "retrieve_1", "policy_1", "action", "eq", "retrieve_knowledge"),
				{ID: "default", Name: "策略兜底", TargetNodeID: "policy_reply_1", Default: true},
			}}),
			workflowNode("handoff_confirm_prompt_1", workflowregistry.NodeTypeLLMReply, "转人工确认文案", 2020, 0, workflowInputs("userMessage", "start_1", "userMessage"), map[string]any{"staticReply": "我可以为你转接人工客服处理。请回复“确认”继续转人工，或回复“取消”继续由 AI 协助。"}),
			workflowNode("handoff_confirm_1", workflowregistry.NodeTypeHumanConfirm, "确认转人工", 2480, 0, workflowInputs("prompt", "handoff_confirm_prompt_1", "replyText"), nil),
			workflowNode("handoff_confirm_route_1", workflowregistry.NodeTypeCondition, "转人工确认分流", 2940, 0, nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				workflowConditionBranch("confirmed", "已确认", "handoff_1", "handoff_confirm_1", "confirmed", "is_true", nil),
				{ID: "default", Name: "取消或未确认", TargetNodeID: "handoff_cancel_reply_1", Default: true},
			}}),
			workflowNode("handoff_1", workflowregistry.NodeTypeHandoffToHuman, "转人工", 3400, 0, map[string]dsl.Value{
				"reason":    dsl.RefValue("start_1", "userMessage"),
				"confirmed": dsl.RefValue("handoff_confirm_1", "confirmed"),
			}, nil),
			workflowNode("handoff_cancel_reply_1", workflowregistry.NodeTypeLLMReply, "取消转人工提示", 3400, 480, workflowInputs("userMessage", "start_1", "userMessage"), map[string]any{"staticReply": "已取消转人工。你可以继续补充问题，我会继续协助。"}),
			workflowNode("send_handoff_cancel_1", workflowregistry.NodeTypeSendReply, "发送取消提示", 3860, 480, workflowInputs("replyText", "handoff_cancel_reply_1", "replyText"), nil),
			workflowNode("policy_reply_1", workflowregistry.NodeTypeSendReply, "发送策略回复", 4320, 98.5, workflowInputs("replyText", "policy_1", "replyText"), nil),
			workflowNode("handoff_end_1", workflowregistry.NodeTypeEnd, "结束", 3860, 0, nil, nil),
			workflowNode("draft_ticket_1", workflowregistry.NodeTypePrepareTicketDraft, "整理工单草稿", 2020, 379, workflowInputs("issue", "start_1", "userMessage"), nil),
			workflowNode("ticket_draft_route_1", workflowregistry.NodeTypeCondition, "草稿就绪分流", 2480, 329, nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				workflowConditionBranch("ready", "草稿完整", "ticket_confirm_prompt_1", "draft_ticket_1", "ready", "is_true", nil),
				{ID: "default", Name: "补充信息", TargetNodeID: "ticket_followup_reply_1", Default: true},
			}}),
			workflowNode("ticket_confirm_prompt_1", workflowregistry.NodeTypeLLMReply, "建单确认文案", 2940, 285.5, map[string]dsl.Value{
				"userMessage":       dsl.RefValue("start_1", "userMessage"),
				"ticketTitle":       dsl.RefValue("draft_ticket_1", "title"),
				"ticketDescription": dsl.RefValue("draft_ticket_1", "description"),
			}, map[string]any{"staticReply": "我已整理工单草稿，请确认是否创建：\n标题：{{ticketTitle}}\n描述：{{ticketDescription}}\n请回复“确认”创建工单，或回复“取消”放弃。"}),
			workflowNode("ticket_confirm_1", workflowregistry.NodeTypeHumanConfirm, "确认建单", 3400, 285.5, workflowInputs("prompt", "ticket_confirm_prompt_1", "replyText"), nil),
			workflowNode("ticket_confirm_route_1", workflowregistry.NodeTypeCondition, "建单确认分流", 3860, 235.5, nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				workflowConditionBranch("confirmed", "已确认", "create_ticket_1", "ticket_confirm_1", "confirmed", "is_true", nil),
				{ID: "default", Name: "取消或未确认", TargetNodeID: "ticket_cancel_reply_1", Default: true},
			}}),
			workflowNode("create_ticket_1", workflowregistry.NodeTypeCreateTicket, "创建工单", 4780, 192, map[string]dsl.Value{
				"ticketDraft": dsl.RefValue("draft_ticket_1", "ticketDraft"),
				"confirmed":   dsl.RefValue("ticket_confirm_1", "confirmed"),
			}, nil),
			workflowNode("ticket_result_reply_1", workflowregistry.NodeTypeSendReply, "发送建单结果", 5240, 192, workflowInputs("replyText", "create_ticket_1", "message"), nil),
			workflowNode("ticket_cancel_reply_1", workflowregistry.NodeTypeLLMReply, "取消建单提示", 4320, 379, workflowInputs("userMessage", "start_1", "userMessage"), map[string]any{"staticReply": "已取消创建工单。你可以继续补充问题，我会继续帮你处理。"}),
			workflowNode("send_ticket_cancel_1", workflowregistry.NodeTypeSendReply, "发送取消提示", 4780, 379, workflowInputs("replyText", "ticket_cancel_reply_1", "replyText"), nil),
			workflowNode("ticket_followup_reply_1", workflowregistry.NodeTypeLLMReply, "追问工单信息", 3860, 1033.5, map[string]dsl.Value{
				"userMessage":       dsl.RefValue("start_1", "userMessage"),
				"followUpQuestions": dsl.RefValue("draft_ticket_1", "followUpQuestions"),
			}, map[string]any{"staticReply": "为了创建工单，还需要补充以下信息：\n{{followUpQuestions}}"}),
			workflowNode("send_ticket_followup_1", workflowregistry.NodeTypeSendReply, "发送工单追问", 4780, 1033.5, workflowInputs("replyText", "ticket_followup_reply_1", "replyText"), nil),
			workflowNode("retrieve_1", workflowregistry.NodeTypeKnowledgeRetrieve, "知识检索", 2480, 753, workflowInputs("query", "start_1", "userMessage"), map[string]any{"knowledgeBaseIds": []int64{}}),
			workflowNode("answerability_1", workflowregistry.NodeTypeAnswerabilityGate, "可回答判断", 2940, 753, map[string]dsl.Value{
				"userMessage":    dsl.RefValue("start_1", "userMessage"),
				"knowledgeItems": dsl.RefValue("retrieve_1", "items"),
			}, nil),
			workflowNode("answerability_route_1", workflowregistry.NodeTypeCondition, "可回答分流", 3400, 703, nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				workflowConditionBranch("answerable", "可以回答", "reply_1", "answerability_1", "answerability", "eq", "answerable"),
				{ID: "default", Name: "兜底追问", TargetNodeID: "fallback_reply_1", Default: true},
			}}),
			workflowNode("reply_1", workflowregistry.NodeTypeLLMReply, "AI 回复", 3860, 659.5, map[string]dsl.Value{
				"userMessage":    dsl.RefValue("start_1", "userMessage"),
				"knowledgeItems": dsl.RefValue("retrieve_1", "items"),
			}, nil),
			workflowNode("send_1", workflowregistry.NodeTypeSendReply, "发送回复", 4320, 659.5, workflowInputs("replyText", "reply_1", "replyText"), nil),
			workflowNode("fallback_reply_1", workflowregistry.NodeTypeLLMReply, "兜底追问", 3860, 846.5, map[string]dsl.Value{
				"userMessage":    dsl.RefValue("start_1", "userMessage"),
				"knowledgeItems": dsl.RefValue("retrieve_1", "items"),
			}, nil),
			workflowNode("send_fallback_1", workflowregistry.NodeTypeSendReply, "发送兜底", 4320, 846.5, workflowInputs("replyText", "fallback_reply_1", "replyText"), nil),
			workflowNode("end_1", workflowregistry.NodeTypeEnd, "结束", 5700, 472.5, nil, nil),
		},
		Edges: []dsl.Edge{
			workflowEdge("start_1", "understanding_1"),
			workflowEdge("understanding_1", "policy_1"),
			workflowEdge("policy_1", "policy_route_1"),
			workflowPortEdge("policy_route_1", "handoff_confirm_prompt_1", "handoff"),
			workflowPortEdge("policy_route_1", "policy_reply_1", "direct"),
			workflowPortEdge("policy_route_1", "policy_reply_1", "clarify"),
			workflowPortEdge("policy_route_1", "policy_reply_1", "end_conversation"),
			workflowPortEdge("policy_route_1", "draft_ticket_1", "ticket"),
			workflowPortEdge("policy_route_1", "retrieve_1", "knowledge"),
			workflowPortEdge("policy_route_1", "policy_reply_1", "default"),
			workflowEdge("policy_reply_1", "end_1"),
			workflowEdge("handoff_confirm_prompt_1", "handoff_confirm_1"),
			workflowEdge("handoff_confirm_1", "handoff_confirm_route_1"),
			workflowPortEdge("handoff_confirm_route_1", "handoff_1", "confirmed"),
			workflowPortEdge("handoff_confirm_route_1", "handoff_cancel_reply_1", "default"),
			workflowEdge("handoff_1", "handoff_end_1"),
			workflowEdge("handoff_cancel_reply_1", "send_handoff_cancel_1"),
			workflowEdge("send_handoff_cancel_1", "end_1"),
			workflowEdge("draft_ticket_1", "ticket_draft_route_1"),
			workflowPortEdge("ticket_draft_route_1", "ticket_confirm_prompt_1", "ready"),
			workflowPortEdge("ticket_draft_route_1", "ticket_followup_reply_1", "default"),
			workflowEdge("ticket_confirm_prompt_1", "ticket_confirm_1"),
			workflowEdge("ticket_confirm_1", "ticket_confirm_route_1"),
			workflowPortEdge("ticket_confirm_route_1", "create_ticket_1", "confirmed"),
			workflowPortEdge("ticket_confirm_route_1", "ticket_cancel_reply_1", "default"),
			workflowEdge("create_ticket_1", "ticket_result_reply_1"),
			workflowEdge("ticket_result_reply_1", "end_1"),
			workflowEdge("ticket_cancel_reply_1", "send_ticket_cancel_1"),
			workflowEdge("send_ticket_cancel_1", "end_1"),
			workflowEdge("ticket_followup_reply_1", "send_ticket_followup_1"),
			workflowEdge("send_ticket_followup_1", "end_1"),
			workflowEdge("retrieve_1", "answerability_1"),
			workflowEdge("answerability_1", "answerability_route_1"),
			workflowPortEdge("answerability_route_1", "reply_1", "answerable"),
			workflowPortEdge("answerability_route_1", "fallback_reply_1", "default"),
			workflowEdge("reply_1", "send_1"),
			workflowEdge("send_1", "end_1"),
			workflowEdge("fallback_reply_1", "send_fallback_1"),
			workflowEdge("send_fallback_1", "end_1"),
		},
	}
}

func identityConfirmationWorkflowDefinition() dsl.Definition {
	return dsl.Definition{SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			workflowNode("start_1", workflowregistry.NodeTypeStart, "开始", 180, 180, nil, nil),
			workflowNode("prompt_1", workflowregistry.NodeTypeLLMReply, "身份确认提示", 600, 180, workflowInputs("userMessage", "start_1", "userMessage"), map[string]any{"staticReply": "为保护你的账户信息，请确认是否继续身份核验。"}),
			workflowNode("confirm_1", workflowregistry.NodeTypeHumanConfirm, "确认身份核验", 1020, 180, workflowInputs("prompt", "prompt_1", "replyText"), nil),
			workflowNode("route_1", workflowregistry.NodeTypeCondition, "确认分流", 1440, 180, nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				workflowConditionBranch("confirmed", "已确认", "confirmed_reply_1", "confirm_1", "confirmed", "is_true", nil),
				{ID: "default", Name: "取消", TargetNodeID: "cancel_reply_1", Default: true},
			}}),
			workflowNode("confirmed_reply_1", workflowregistry.NodeTypeLLMReply, "确认结果", 1860, 100, workflowInputs("userMessage", "start_1", "userMessage"), map[string]any{"staticReply": "已收到确认，人工客服将继续为你核验身份。"}),
			workflowNode("cancel_reply_1", workflowregistry.NodeTypeLLMReply, "取消提示", 1860, 280, workflowInputs("userMessage", "start_1", "userMessage"), map[string]any{"staticReply": "已取消身份核验。"}),
			workflowNode("send_confirmed_1", workflowregistry.NodeTypeSendReply, "发送确认结果", 2280, 100, workflowInputs("replyText", "confirmed_reply_1", "replyText"), nil),
			workflowNode("send_cancel_1", workflowregistry.NodeTypeSendReply, "发送取消提示", 2280, 280, workflowInputs("replyText", "cancel_reply_1", "replyText"), nil),
			workflowNode("end_1", workflowregistry.NodeTypeEnd, "结束", 2700, 180, nil, nil),
		},
		Edges: []dsl.Edge{
			workflowEdge("start_1", "prompt_1"), workflowEdge("prompt_1", "confirm_1"), workflowEdge("confirm_1", "route_1"),
			workflowPortEdge("route_1", "confirmed_reply_1", "confirmed"), workflowPortEdge("route_1", "cancel_reply_1", "default"),
			workflowEdge("confirmed_reply_1", "send_confirmed_1"), workflowEdge("cancel_reply_1", "send_cancel_1"), workflowEdge("send_confirmed_1", "end_1"), workflowEdge("send_cancel_1", "end_1"),
		},
	}
}

func complaintEscalationWorkflowDefinition() dsl.Definition {
	return confirmationHandoffWorkflowDefinition("投诉升级确认", "我们将把本次投诉升级给人工客服处理。请确认是否继续。", "已为你升级投诉，人工客服会尽快跟进。", "投诉升级已取消。")
}

func confirmationHandoffWorkflowDefinition(title, prompt, confirmedReply, cancelledReply string) dsl.Definition {
	return dsl.Definition{SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			workflowNode("start_1", workflowregistry.NodeTypeStart, "开始", 180, 180, nil, nil),
			workflowNode("prompt_1", workflowregistry.NodeTypeLLMReply, title, 600, 180, workflowInputs("userMessage", "start_1", "userMessage"), map[string]any{"staticReply": prompt}),
			workflowNode("confirm_1", workflowregistry.NodeTypeHumanConfirm, "确认升级", 1020, 180, workflowInputs("prompt", "prompt_1", "replyText"), nil),
			workflowNode("route_1", workflowregistry.NodeTypeCondition, "确认分流", 1440, 180, nil, dsl.ConditionConfig{Branches: []dsl.ConditionBranch{
				workflowConditionBranch("confirmed", "已确认", "handoff_1", "confirm_1", "confirmed", "is_true", nil),
				{ID: "default", Name: "取消", TargetNodeID: "cancel_reply_1", Default: true},
			}}),
			workflowNode("handoff_1", workflowregistry.NodeTypeHandoffToHuman, "转人工处理", 1860, 100, map[string]dsl.Value{"reason": dsl.RefValue("start_1", "userMessage"), "confirmed": dsl.RefValue("confirm_1", "confirmed")}, nil),
			workflowNode("cancel_reply_1", workflowregistry.NodeTypeLLMReply, "取消提示", 1860, 280, workflowInputs("userMessage", "start_1", "userMessage"), map[string]any{"staticReply": cancelledReply}),
			workflowNode("send_handoff_1", workflowregistry.NodeTypeSendReply, "发送升级结果", 2280, 100, workflowInputs("replyText", "handoff_1", "message"), nil),
			workflowNode("send_cancel_1", workflowregistry.NodeTypeSendReply, "发送取消提示", 2280, 280, workflowInputs("replyText", "cancel_reply_1", "replyText"), nil),
			workflowNode("end_1", workflowregistry.NodeTypeEnd, "结束", 2700, 180, nil, nil),
		},
		Edges: []dsl.Edge{
			workflowEdge("start_1", "prompt_1"), workflowEdge("prompt_1", "confirm_1"), workflowEdge("confirm_1", "route_1"),
			workflowPortEdge("route_1", "handoff_1", "confirmed"), workflowPortEdge("route_1", "cancel_reply_1", "default"),
			workflowEdge("handoff_1", "send_handoff_1"), workflowEdge("cancel_reply_1", "send_cancel_1"), workflowEdge("send_handoff_1", "end_1"), workflowEdge("send_cancel_1", "end_1"),
		},
	}
}

func refundRequestPreparationWorkflowDefinition() dsl.Definition {
	return confirmationHandoffWorkflowDefinition("退款申请确认", "我会先整理退款申请并转交人工客服核验。请确认是否继续。", "退款申请已准备完成，人工客服将继续核验订单和退款条件。", "退款申请准备已取消。")
}

func workflowNode(id string, nodeType string, title string, x float64, y float64, inputs map[string]dsl.Value, config any) dsl.Node {
	return dsl.Node{
		ID:   id,
		Type: nodeType,
		Meta: dsl.NodeMeta{Position: dsl.Position{X: x, Y: y}},
		Data: dsl.NodeData{
			Title:        title,
			Config:       mustMarshalWorkflowConfig(config),
			InputsValues: inputs,
		},
	}
}

func workflowInputs(name string, nodeID string, field string) map[string]dsl.Value {
	return map[string]dsl.Value{name: dsl.RefValue(nodeID, field)}
}

func workflowConditionBranch(id string, name string, targetNodeID string, nodeID string, field string, operator string, right any) dsl.ConditionBranch {
	return dsl.ConditionBranch{
		ID:           id,
		Name:         name,
		TargetNodeID: targetNodeID,
		Condition: &dsl.Condition{
			Left:     &dsl.Value{Type: dsl.ValueTypeRef, Content: []string{nodeID, field}},
			Operator: operator,
			Right:    right,
		},
	}
}

func workflowEdge(source string, target string) dsl.Edge {
	return dsl.Edge{SourceNodeID: source, TargetNodeID: target}
}

func workflowPortEdge(source string, target string, sourcePortID string) dsl.Edge {
	return dsl.Edge{SourceNodeID: source, TargetNodeID: target, SourcePortID: sourcePortID}
}

func mustMarshalWorkflowConfig(value any) json.RawMessage {
	if value == nil {
		return nil
	}
	raw, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return raw
}

func defaultAgentWorkflowName(agentName string) string {
	agentName = strings.TrimSpace(agentName)
	if agentName == "" {
		return "会话流程"
	}
	return agentName + " 会话流程"
}

func marshalDefinition(def dsl.Definition) (string, error) {
	buf, err := json.Marshal(def)
	if err != nil {
		return "", errorsx.InvalidParam("invalid workflow definition")
	}
	return string(buf), nil
}

func hashDefinition(definition string) string {
	sum := sha256.Sum256([]byte(definition))
	return hex.EncodeToString(sum[:])
}
