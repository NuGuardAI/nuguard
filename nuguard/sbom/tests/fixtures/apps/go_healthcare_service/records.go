package main

import (
	"context"

	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/mongo"
)

func getPatient(c *gin.Context) {
	client, err := mongo.Connect(context.Background(), nil)
	if err != nil {
		c.AbortWithStatus(500)
		return
	}
	_ = client
}
