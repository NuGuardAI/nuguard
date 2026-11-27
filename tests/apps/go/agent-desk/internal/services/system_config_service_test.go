package services

import (
	"encoding/json"
	"strings"
	"testing"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func TestSupportNavigationMenuDefaultsAndPublicFiltering(t *testing.T) {
	setupSystemConfigServiceTestDB(t)

	defaultConfig := SystemConfigService.GetPublicSupportConfig()
	if len(defaultConfig.NavigationMenu) != 3 {
		t.Fatalf("expected default navigation menu, got %#v", defaultConfig.NavigationMenu)
	}

	raw, _ := json.Marshal([]request.SupportNavigationMenuItemRequest{
		{ID: "community", Title: "Community", URL: "/support/community/posts", Visible: boolPtr(true)},
		{ID: "hidden", Title: "Hidden", URL: "/support/hidden", Visible: boolPtr(false)},
		{ID: "docs", Title: "Docs", URL: "https://docs.example.com", OpenInNewWindow: true, Visible: boolPtr(true)},
	})
	config, err := SystemConfigService.SaveSupportConfig(map[string]json.RawMessage{
		systemConfigKeySupportNavMenu: raw,
	}, &dto.AuthPrincipal{UserID: 1, Username: "admin"})
	if err != nil {
		t.Fatalf("SaveSupportConfig() error = %v", err)
	}
	items := config.NavigationMenu
	if got := []int{items[0].SortNo, items[1].SortNo, items[2].SortNo}; got[0] != 10 || got[1] != 20 || got[2] != 30 {
		t.Fatalf("unexpected sort numbers: %#v", got)
	}

	publicConfig := SystemConfigService.GetPublicSupportConfig()
	if len(publicConfig.NavigationMenu) != 2 {
		t.Fatalf("expected only enabled public items, got %#v", publicConfig.NavigationMenu)
	}
	if publicConfig.NavigationMenu[0].ID != "community" || publicConfig.NavigationMenu[1].ID != "docs" {
		t.Fatalf("unexpected public order: %#v", publicConfig.NavigationMenu)
	}
	if !publicConfig.NavigationMenu[1].OpenInNewWindow {
		t.Fatalf("expected docs to open in new window")
	}
}

func TestSaveSupportConfigValidation(t *testing.T) {
	setupSystemConfigServiceTestDB(t)

	tests := []struct {
		name string
		req  []request.SupportNavigationMenuItemRequest
	}{
		{
			name: "empty",
			req:  nil,
		},
		{
			name: "blank title",
			req: []request.SupportNavigationMenuItemRequest{
				{Title: " ", URL: "/support", Visible: boolPtr(true)},
			},
		},
		{
			name: "invalid url",
			req: []request.SupportNavigationMenuItemRequest{
				{Title: "Docs", URL: "javascript:alert(1)", Visible: boolPtr(true)},
			},
		},
		{
			name: "no visible item",
			req: []request.SupportNavigationMenuItemRequest{
				{Title: "Docs", URL: "/support", Visible: boolPtr(false)},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			raw, _ := json.Marshal(tt.req)
			err := SystemConfigService.SaveGroupConfig(systemConfigGroupSupportCenter, map[string]json.RawMessage{
				systemConfigKeySupportNavMenu: raw,
			}, nil)
			if err == nil {
				t.Fatal("expected validation error")
			}
			if _, ok := err.(*SystemConfigValidationError); !ok {
				t.Fatalf("expected SystemConfigValidationError, got %T", err)
			}
		})
	}
}

func TestSaveSupportConfigRejectsUnsupportedKey(t *testing.T) {
	setupSystemConfigServiceTestDB(t)

	err := SystemConfigService.SaveGroupConfig(systemConfigGroupSupportCenter, map[string]json.RawMessage{
		"unknown": json.RawMessage(`true`),
	}, nil)
	if err == nil {
		t.Fatal("expected unsupported key error")
	}
}

func setupSystemConfigServiceTestDB(t *testing.T) *gorm.DB {
	t.Helper()

	db, err := gorm.Open(sqlite.Open("file:"+strings.ReplaceAll(t.Name(), "/", "_")+"?mode=memory&cache=shared"), &gorm.Config{
		NamingStrategy: schema.NamingStrategy{
			TablePrefix:   "t_",
			SingularTable: true,
		},
	})
	if err != nil {
		t.Fatalf("open sqlite error = %v", err)
	}
	t.Cleanup(func() {
		sqlDB, err := db.DB()
		if err == nil {
			_ = sqlDB.Close()
		}
	})
	if err := db.AutoMigrate(&models.SystemConfig{}); err != nil {
		t.Fatalf("auto migrate error = %v", err)
	}
	sqls.SetDB(db)
	return db
}

func boolPtr(value bool) *bool {
	return &value
}
