package response

import "time"

type SkillDefinitionResponse struct {
	ID             int64     `json:"id"`
	Name           string    `json:"name"`
	Description    string    `json:"description"`
	Instruction    string    `json:"instruction"`
	Examples       []string  `json:"examples"`
	ToolWhitelist  []string  `json:"toolWhitelist"`
	Status         int       `json:"status"`
	StatusName     string    `json:"statusName"`
	Remark         string    `json:"remark"`
	CreatedAt      time.Time `json:"createdAt"`
	UpdatedAt      time.Time `json:"updatedAt"`
	CreateUserName string    `json:"createUserName"`
	UpdateUserName string    `json:"updateUserName"`
}

type SkillDebugRunResponse struct {
	SkillDefinitionID int64    `json:"skillDefinitionId"`
	SkillName         string   `json:"skillName"`
	ReplyText         string   `json:"replyText"`
	ToolWhitelist     []string `json:"toolWhitelist"`
	InvokedToolCodes  []string `json:"invokedToolCodes"`
	InterruptType     string   `json:"interruptType"`
	CheckPointID      string   `json:"checkPointId"`
	Interrupted       bool     `json:"interrupted"`
	TraceData         string   `json:"traceData"`
	ErrorMessage      string   `json:"errorMessage"`
	ConversationID    int64    `json:"conversationId"`
	AIAgentID         int64    `json:"aiAgentId"`
}
