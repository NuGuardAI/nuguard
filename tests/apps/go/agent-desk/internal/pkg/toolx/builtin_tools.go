package toolx

import (
	"strings"

	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/i18nx"
)

type ToolSpec struct {
	Code           string
	ServerCode     string
	Name           string
	Title          string
	TitleKey       string
	Description    string
	DescriptionKey string
	SourceType     enums.ToolSourceType
	AutoInjected   bool
	DirectAccess   bool
	RuntimeStatic  bool
	Aliases        []string
	Appendix       string
	AppendixKey    string
}

type ToolMetadata struct {
	ToolCode   string
	ServerCode string
	ToolName   string
	SourceType enums.ToolSourceType
}

var (
	BuiltinToolSearch = ToolSpec{
		Code:           "builtin/tool_search",
		ServerCode:     "builtin",
		Name:           "tool_search",
		Title:          i18nx.Get("tool.builtin.toolSearch.title"),
		TitleKey:       "tool.builtin.toolSearch.title",
		Description:    i18nx.Get("tool.builtin.toolSearch.description"),
		DescriptionKey: "tool.builtin.toolSearch.description",
		SourceType:     enums.ToolSourceTypeBuiltin,
		AutoInjected:   true,
		DirectAccess:   true,
		Appendix:       i18nx.Get("tool.builtin.toolSearch.appendix"),
		AppendixKey:    "tool.builtin.toolSearch.appendix",
	}
	BuiltinSkill = ToolSpec{
		Code:           "builtin/skill",
		ServerCode:     "builtin",
		Name:           "skill",
		Title:          i18nx.Get("tool.builtin.skill.title"),
		TitleKey:       "tool.builtin.skill.title",
		Description:    i18nx.Get("tool.builtin.skill.description"),
		DescriptionKey: "tool.builtin.skill.description",
		SourceType:     enums.ToolSourceTypeBuiltin,
		AutoInjected:   true,
	}
	BuiltinConversationContext = ToolSpec{
		Code:         "builtin/conversation_context",
		ServerCode:   "builtin",
		Name:         "conversation_context",
		Title:        "会话上下文",
		Description:  "读取当前客户基础信息和会话摘要。",
		SourceType:   enums.ToolSourceTypeBuiltin,
		DirectAccess: true,
	}
	BuiltinKnowledgeRetrieve = ToolSpec{
		Code:         "builtin/knowledge_retrieve",
		ServerCode:   "builtin",
		Name:         "knowledge_retrieve",
		Title:        "知识检索",
		Description:  "在当前 Agent 已绑定的知识库中检索证据。",
		SourceType:   enums.ToolSourceTypeBuiltin,
		DirectAccess: true,
	}
	GraphTriageServiceRequest = ToolSpec{
		Code:           "graph/triage_service_request",
		ServerCode:     "graph",
		Name:           "triage_service_request",
		Title:          i18nx.Get("tool.graph.triageServiceRequest.title"),
		TitleKey:       "tool.graph.triageServiceRequest.title",
		Description:    i18nx.Get("tool.graph.triageServiceRequest.description"),
		DescriptionKey: "tool.graph.triageServiceRequest.description",
		SourceType:     enums.ToolSourceTypeGraph,
		DirectAccess:   true,
		RuntimeStatic:  true,
		Appendix:       i18nx.Get("tool.graph.triageServiceRequest.appendix"),
		AppendixKey:    "tool.graph.triageServiceRequest.appendix",
	}
	GraphAnalyzeConversation = ToolSpec{
		Code:           "graph/analyze_conversation",
		ServerCode:     "graph",
		Name:           "analyze_conversation",
		Title:          i18nx.Get("tool.graph.analyzeConversation.title"),
		TitleKey:       "tool.graph.analyzeConversation.title",
		Description:    i18nx.Get("tool.graph.analyzeConversation.description"),
		DescriptionKey: "tool.graph.analyzeConversation.description",
		SourceType:     enums.ToolSourceTypeGraph,
		RuntimeStatic:  true,
		DirectAccess:   true,
		Appendix:       i18nx.Get("tool.graph.analyzeConversation.appendix"),
		AppendixKey:    "tool.graph.analyzeConversation.appendix",
	}
	GraphPrepareTicketDraft = ToolSpec{
		Code:           "graph/prepare_ticket_draft",
		ServerCode:     "graph",
		Name:           "prepare_ticket_draft",
		Title:          i18nx.Get("tool.graph.prepareTicketDraft.title"),
		TitleKey:       "tool.graph.prepareTicketDraft.title",
		Description:    i18nx.Get("tool.graph.prepareTicketDraft.description"),
		DescriptionKey: "tool.graph.prepareTicketDraft.description",
		SourceType:     enums.ToolSourceTypeGraph,
		DirectAccess:   true,
		RuntimeStatic:  true,
		Appendix:       i18nx.Get("tool.graph.prepareTicketDraft.appendix"),
		AppendixKey:    "tool.graph.prepareTicketDraft.appendix",
	}
	GraphCreateTicketConfirm = ToolSpec{
		Code:           "graph/create_ticket_with_confirmation",
		ServerCode:     "graph",
		Name:           "create_ticket_with_confirmation",
		Title:          i18nx.Get("tool.graph.createTicketConfirm.title"),
		TitleKey:       "tool.graph.createTicketConfirm.title",
		Description:    i18nx.Get("tool.graph.createTicketConfirm.description"),
		DescriptionKey: "tool.graph.createTicketConfirm.description",
		SourceType:     enums.ToolSourceTypeGraph,
		RuntimeStatic:  true,
		Aliases:        []string{"builtin/create_ticket_with_confirmation"},
		Appendix:       i18nx.Get("tool.graph.createTicketConfirm.appendix"),
		AppendixKey:    "tool.graph.createTicketConfirm.appendix",
	}
	GraphHandoffConversation = ToolSpec{
		Code:           "graph/handoff_to_human",
		ServerCode:     "graph",
		Name:           "handoff_to_human",
		Title:          i18nx.Get("tool.graph.handoffConversation.title"),
		TitleKey:       "tool.graph.handoffConversation.title",
		Description:    i18nx.Get("tool.graph.handoffConversation.description"),
		DescriptionKey: "tool.graph.handoffConversation.description",
		SourceType:     enums.ToolSourceTypeGraph,
		RuntimeStatic:  true,
		Appendix:       i18nx.Get("tool.graph.handoffConversation.appendix"),
		AppendixKey:    "tool.graph.handoffConversation.appendix",
	}
	RegisteredToolSpecs = []ToolSpec{
		BuiltinToolSearch,
		BuiltinSkill,
		BuiltinConversationContext,
		BuiltinKnowledgeRetrieve,
		GraphTriageServiceRequest,
		GraphAnalyzeConversation,
		GraphPrepareTicketDraft,
		GraphCreateTicketConfirm,
		GraphHandoffConversation,
	}
)

var (
	toolSpecByCode       = buildToolSpecByCode()
	toolSpecByName       = buildToolSpecByName()
	toolAliasToCanonical = buildToolAliasToCanonical()
)

func buildToolSpecByCode() map[string]ToolSpec {
	ret := make(map[string]ToolSpec, len(RegisteredToolSpecs))
	for _, spec := range RegisteredToolSpecs {
		if strings.TrimSpace(spec.Code) == "" {
			continue
		}
		ret[spec.Code] = spec
	}
	return ret
}

func buildToolAliasToCanonical() map[string]string {
	ret := make(map[string]string)
	for _, spec := range RegisteredToolSpecs {
		for _, alias := range spec.Aliases {
			alias = strings.TrimSpace(alias)
			if alias == "" {
				continue
			}
			ret[alias] = spec.Code
		}
	}
	return ret
}

func buildToolSpecByName() map[string]ToolSpec {
	ret := make(map[string]ToolSpec, len(RegisteredToolSpecs))
	for _, spec := range RegisteredToolSpecs {
		name := strings.TrimSpace(spec.Name)
		if name == "" {
			continue
		}
		ret[name] = spec
	}
	return ret
}

func ListRegisteredToolSpecs() []ToolSpec {
	return append([]ToolSpec(nil), RegisteredToolSpecs...)
}

func GetRegisteredToolSpec(toolCode string) (ToolSpec, bool) {
	toolCode = NormalizeToolCodeAlias(strings.TrimSpace(toolCode))
	spec, ok := toolSpecByCode[toolCode]
	return spec, ok
}

func GetRegisteredToolSpecByName(name string) (ToolSpec, bool) {
	name = strings.TrimSpace(name)
	spec, ok := toolSpecByName[name]
	return spec, ok
}

func GetRegisteredToolTitle(toolCode string) string {
	spec, ok := GetRegisteredToolSpec(toolCode)
	if !ok {
		return ""
	}
	return spec.Title
}

func GetRegisteredToolTitleLocale(toolCode string, locale string) string {
	spec, ok := GetRegisteredToolSpec(toolCode)
	if !ok {
		return ""
	}
	if strings.TrimSpace(spec.TitleKey) != "" {
		return i18nx.Getf(locale, spec.TitleKey)
	}
	return spec.Title
}

func GetRegisteredToolDescription(toolCode string) string {
	spec, ok := GetRegisteredToolSpec(toolCode)
	if !ok {
		return ""
	}
	return spec.Description
}

func GetRegisteredToolDescriptionLocale(toolCode string, locale string) string {
	spec, ok := GetRegisteredToolSpec(toolCode)
	if !ok {
		return ""
	}
	if strings.TrimSpace(spec.DescriptionKey) != "" {
		return i18nx.Getf(locale, spec.DescriptionKey)
	}
	return spec.Description
}

func GetRegisteredToolAppendixLocale(toolCode string, locale string) string {
	spec, ok := GetRegisteredToolSpec(toolCode)
	if !ok {
		return ""
	}
	if strings.TrimSpace(spec.AppendixKey) != "" {
		return i18nx.Getf(locale, spec.AppendixKey)
	}
	return spec.Appendix
}

func GetRegisteredToolIdentity(toolCode string) (serverCode, toolName string, ok bool) {
	spec, ok := GetRegisteredToolSpec(toolCode)
	if !ok {
		return "", "", false
	}
	return spec.ServerCode, spec.Name, true
}

func ResolveToolSourceType(toolCode string) enums.ToolSourceType {
	if spec, ok := GetRegisteredToolSpec(toolCode); ok {
		return spec.SourceType
	}
	toolCode = strings.TrimSpace(toolCode)
	switch {
	case strings.HasPrefix(toolCode, "graph/"):
		return enums.ToolSourceTypeGraph
	case strings.HasPrefix(toolCode, "builtin/"):
		return enums.ToolSourceTypeBuiltin
	default:
		return enums.ToolSourceTypeMCP
	}
}

func IsAutoInjectedToolCode(toolCode string) bool {
	spec, ok := GetRegisteredToolSpec(toolCode)
	return ok && spec.AutoInjected
}

func ListAgentDirectToolSpecs() []ToolSpec {
	ret := make([]ToolSpec, 0, len(RegisteredToolSpecs))
	for _, spec := range RegisteredToolSpecs {
		if !spec.DirectAccess {
			continue
		}
		ret = append(ret, spec)
	}
	return ret
}

func ListRuntimeStaticToolSpecs() []ToolSpec {
	ret := make([]ToolSpec, 0, len(RegisteredToolSpecs))
	for _, spec := range RegisteredToolSpecs {
		if !spec.RuntimeStatic {
			continue
		}
		ret = append(ret, spec)
	}
	return ret
}

func IsAgentDirectToolCode(toolCode string) bool {
	toolCode = NormalizeToolCodeAlias(strings.TrimSpace(toolCode))
	spec, ok := GetRegisteredToolSpec(toolCode)
	return ok && spec.DirectAccess
}

func IsAgentDirectGraphToolCode(toolCode string) bool {
	toolCode = NormalizeToolCodeAlias(strings.TrimSpace(toolCode))
	spec, ok := GetRegisteredToolSpec(toolCode)
	return ok && spec.DirectAccess && spec.SourceType == enums.ToolSourceTypeGraph
}

func NormalizeToolCodeAlias(toolCode string) string {
	toolCode = strings.TrimSpace(toolCode)
	if canonical, ok := toolAliasToCanonical[toolCode]; ok {
		return canonical
	}
	return toolCode
}

func BuildToolAppendices(hasDynamicMCPTools bool, toolCodes map[string]string) []string {
	return BuildToolAppendicesForCodes(hasDynamicMCPTools, toolCodesFromMap(toolCodes))
}

func BuildToolAppendicesForCodes(hasDynamicMCPTools bool, toolCodes []string) []string {
	return BuildToolAppendicesForCodesLocale(hasDynamicMCPTools, toolCodes, i18nx.DefaultLocale)
}

func BuildToolAppendicesForCodesLocale(hasDynamicMCPTools bool, toolCodes []string, locale string) []string {
	ret := make([]string, 0, len(toolCodes)+1)
	normalizedToolCodes := NormalizeToolCodes(toolCodes)
	if hasDynamicMCPTools {
		if appendix := strings.TrimSpace(GetRegisteredToolAppendixLocale(BuiltinToolSearch.Code, locale)); appendix != "" {
			ret = append(ret, appendix)
		}
	}
	for _, spec := range RegisteredToolSpecs {
		if spec.Code == BuiltinToolSearch.Code {
			continue
		}
		if containsNormalizedToolCode(normalizedToolCodes, spec.Code) {
			if appendix := strings.TrimSpace(GetRegisteredToolAppendixLocale(spec.Code, locale)); appendix != "" {
				ret = append(ret, appendix)
			}
		}
	}
	return ret
}

func BuildToolMetadata(toolCode string) (serverCode, toolName string, sourceType enums.ToolSourceType, ok bool) {
	spec, ok := GetRegisteredToolSpec(toolCode)
	if !ok {
		return "", "", ResolveToolSourceType(toolCode), false
	}
	return spec.ServerCode, spec.Name, spec.SourceType, true
}

func ResolveToolMetadata(toolCode string, fallbackName string) ToolMetadata {
	toolCode = NormalizeToolCodeAlias(strings.TrimSpace(toolCode))
	fallbackName = strings.TrimSpace(fallbackName)
	serverCode, toolName, sourceType, ok := BuildToolMetadata(toolCode)
	if !ok && toolName == "" {
		toolName = fallbackName
	}
	if toolName == "" {
		toolName = fallbackName
	}
	return ToolMetadata{
		ToolCode:   toolCode,
		ServerCode: strings.TrimSpace(serverCode),
		ToolName:   strings.TrimSpace(toolName),
		SourceType: sourceType,
	}
}

func IsAlwaysAllowedToolCode(toolCode string) bool {
	return NormalizeToolCodeAlias(strings.TrimSpace(toolCode)) == GraphHandoffConversation.Code
}

func IsImpliedAllowedToolCode(toolCode string, allowedToolCodes map[string]struct{}) bool {
	toolCode = NormalizeToolCodeAlias(strings.TrimSpace(toolCode))
	if toolCode == "" || len(allowedToolCodes) == 0 {
		return false
	}
	switch toolCode {
	case GraphTriageServiceRequest.Code, GraphAnalyzeConversation.Code:
		if _, ok := allowedToolCodes[GraphCreateTicketConfirm.Code]; ok {
			return true
		}
		if _, ok := allowedToolCodes[GraphHandoffConversation.Code]; ok {
			return true
		}
	case GraphPrepareTicketDraft.Code:
		_, ok := allowedToolCodes[GraphCreateTicketConfirm.Code]
		return ok
	}
	return false
}

func hasToolCode(toolCodes map[string]string, target string) bool {
	return containsNormalizedToolCode(toolCodesFromMap(toolCodes), target)
}

func containsNormalizedToolCode(toolCodes []string, target string) bool {
	target = strings.TrimSpace(target)
	if target == "" || len(toolCodes) == 0 {
		return false
	}
	for _, item := range NormalizeToolCodes(toolCodes) {
		if item == target {
			return true
		}
	}
	return false
}

func toolCodesFromMap(toolCodes map[string]string) []string {
	if len(toolCodes) == 0 {
		return nil
	}
	ret := make([]string, 0, len(toolCodes))
	for _, item := range toolCodes {
		ret = append(ret, item)
	}
	return ret
}

func NormalizeToolCodes(items []string) []string {
	if len(items) == 0 {
		return nil
	}
	ret := make([]string, 0, len(items))
	seen := make(map[string]struct{}, len(items))
	for _, item := range items {
		item = NormalizeToolCodeAlias(strings.TrimSpace(item))
		if item == "" {
			continue
		}
		if _, ok := seen[item]; ok {
			continue
		}
		seen[item] = struct{}{}
		ret = append(ret, item)
	}
	return ret
}

func IntersectToolCodes(left []string, right []string) []string {
	left = NormalizeToolCodes(left)
	right = NormalizeToolCodes(right)
	switch {
	case len(left) == 0:
		return right
	case len(right) == 0:
		return left
	}
	rightSet := make(map[string]struct{}, len(right))
	for _, item := range right {
		rightSet[item] = struct{}{}
	}
	ret := make([]string, 0, len(left))
	for _, item := range left {
		if _, ok := rightSet[item]; ok {
			ret = append(ret, item)
		}
	}
	return ret
}
