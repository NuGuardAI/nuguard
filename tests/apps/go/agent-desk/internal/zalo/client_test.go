package zalo

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestZaloClient_SendCSMessage(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v3.0/oa/message/cs" {
			http.NotFound(w, r)
			return
		}
		if r.Header.Get("access_token") != "test_access_token_123" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		var req SendMessageRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if req.Recipient.ID != "zalo_user_001" || req.Message.Text != "Xin chào từ Crove Desk!" {
			http.Error(w, "invalid payload", http.StatusBadRequest)
			return
		}
		_ = json.NewEncoder(w).Encode(APIResponse{
			Error:   0,
			Message: "Success",
			Data: map[string]any{
				"message_id": "zalo_msg_1001",
			},
		})
	}))
	defer ts.Close()

	client := NewClient("test_access_token_123")
	client.SetBaseURL(ts.URL)

	resp, err := client.SendCSMessage(context.Background(), "zalo_user_001", "Xin chào từ Crove Desk!")
	if err != nil {
		t.Fatalf("SendCSMessage failed: %v", err)
	}
	if resp.Error != 0 {
		t.Fatalf("unexpected resp: %+v", resp)
	}
}

func TestZaloClient_GetUserProfile(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v3.0/oa/user/detail" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(UserProfileResponse{
			Error:   0,
			Message: "Success",
			Data: UserProfile{
				UserID:      "zalo_user_001",
				DisplayName: "Nguyen Van B",
				UserGender:  "1",
				Avatar:      "https://avatar.zalo.me/1.jpg",
			},
		})
	}))
	defer ts.Close()

	client := NewClient("test_access_token_123")
	client.SetBaseURL(ts.URL)

	profile, err := client.GetUserProfile(context.Background(), "zalo_user_001")
	if err != nil {
		t.Fatalf("GetUserProfile failed: %v", err)
	}
	if profile.DisplayName != "Nguyen Van B" || profile.UserID != "zalo_user_001" {
		t.Fatalf("unexpected profile: %+v", profile)
	}
}
