package services

import "agent-desk/internal/models"

var TriggerAIReplyAsyncHook func(conversation models.Conversation, message models.Message)
