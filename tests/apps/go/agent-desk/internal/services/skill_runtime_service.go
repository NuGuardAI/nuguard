package services

import (
	"context"
	"fmt"
	"strings"

	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/errorsx"
)

var SkillRuntimeService = newSkillRuntimeService()
var SkillDebugRunHook func(ctx context.Context, req request.SkillDebugRunRequest) (*response.SkillDebugRunResponse, error)
var SkillDebugResumeHook func(ctx context.Context, req request.SkillDebugResumeRequest) (*response.SkillDebugRunResponse, error)

func newSkillRuntimeService() *skillRuntimeService {
	return &skillRuntimeService{}
}

type skillRuntimeService struct{}

func (s *skillRuntimeService) DebugRun(ctx context.Context, req request.SkillDebugRunRequest) (*response.SkillDebugRunResponse, error) {
	if req.AIAgentID <= 0 {
		return nil, errorsx.InvalidParamI18n("error.e0061")
	}
	if req.SkillDefinitionID <= 0 {
		return nil, errorsx.InvalidParamI18n("error.e0071")
	}
	if strings.TrimSpace(req.UserMessage) == "" {
		return nil, errorsx.InvalidParamI18n("error.e0078")
	}
	if SkillDebugRunHook == nil {
		return nil, fmt.Errorf("skill debug runner is not initialized")
	}
	return SkillDebugRunHook(ctx, req)
}

func (s *skillRuntimeService) DebugResume(ctx context.Context, req request.SkillDebugResumeRequest) (*response.SkillDebugRunResponse, error) {
	if req.AIAgentID <= 0 {
		return nil, errorsx.InvalidParamI18n("error.e0061")
	}
	if strings.TrimSpace(req.CheckPointID) == "" {
		return nil, errorsx.InvalidParamI18n("error.e0063")
	}
	if strings.TrimSpace(req.UserMessage) == "" {
		return nil, errorsx.InvalidParamI18n("error.e0078")
	}
	if SkillDebugResumeHook == nil {
		return nil, fmt.Errorf("skill debug resume runner is not initialized")
	}
	return SkillDebugResumeHook(ctx, req)
}
