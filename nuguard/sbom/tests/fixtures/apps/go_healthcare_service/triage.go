package main

import (
	"github.com/gin-gonic/gin"
	openai "github.com/sashabaranov/go-openai"
)

// systemPrompt mirrors the shape found in mosaic-care/healthcare-service's
// backend/chat/chat.go: a package-level raw-string constant ending in
// "Prompt", holding the actual instructions sent to the model.
const systemPrompt = `You are Mosaic's health assistant. You help a patient understand THEIR OWN health record and make sense of it in plain, calm, reassuring language. Never diagnose or prescribe medication — always recommend the patient discuss findings with their clinician.`

func triagePatient(c *gin.Context) {
	client := openai.NewClient("sk-test")
	req := openai.ChatCompletionRequest{
		Model: "gpt-4-turbo",
	}
	_ = systemPrompt
	client.CreateChatCompletion(c, req)
}
