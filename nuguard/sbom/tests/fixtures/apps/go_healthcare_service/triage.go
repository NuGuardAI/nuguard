package main

import (
	"github.com/gin-gonic/gin"
	openai "github.com/sashabaranov/go-openai"
)

func triagePatient(c *gin.Context) {
	client := openai.NewClient("sk-test")
	req := openai.ChatCompletionRequest{
		Model: "gpt-4-turbo",
	}
	client.CreateChatCompletion(c, req)
}
