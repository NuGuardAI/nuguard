// Package main is a small, representative Go backend fixture combining the
// pieces the kscope smoke investigation found completely undetected before
// docs/go-support.md's phases 2-4: a gin HTTP router, a go-openai LLM
// client, a MongoDB datastore, and JWT auth — modeled on the shape of
// mosaic-care/healthcare-service (Golang + gin + MongoDB + Bearer/JWT auth).
package main

import (
	"context"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
)

func requireAuth(c *gin.Context) {
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	_ = token
}

func main() {
	r := gin.Default()
	r.GET("/patients/:id", getPatient)
	r.POST("/patients/:id/triage", triagePatient)

	_ = context.Background()
	r.Run(":8080")
}
