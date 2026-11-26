package telegram

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestTelegramClient_GetMe(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/bot123456:ABC-DEF/getMe" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(APIResponse[User]{
			OK: true,
			Result: User{
				ID:        987654321,
				IsBot:     true,
				FirstName: "Crove Desk Bot",
				Username:  "crove_desk_bot",
			},
		})
	}))
	defer ts.Close()

	client := NewClient("123456:ABC-DEF")
	client.SetBaseURL(ts.URL)

	user, err := client.GetMe(context.Background())
	if err != nil {
		t.Fatalf("GetMe failed: %v", err)
	}
	if user.Username != "crove_desk_bot" || !user.IsBot {
		t.Fatalf("unexpected user: %+v", user)
	}
}

func TestTelegramClient_SendMessage(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/bot123456:ABC-DEF/sendMessage" {
			http.NotFound(w, r)
			return
		}
		var req SendMessageRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if req.ChatID != 112233 || req.Text != "Xin chào từ Crove Desk!" {
			http.Error(w, "invalid params", http.StatusBadRequest)
			return
		}
		_ = json.NewEncoder(w).Encode(APIResponse[Message]{
			OK: true,
			Result: Message{
				MessageID: 101,
				Chat: Chat{
					ID:   112233,
					Type: "private",
				},
				Text: req.Text,
			},
		})
	}))
	defer ts.Close()

	client := NewClient("123456:ABC-DEF")
	client.SetBaseURL(ts.URL)

	msg, err := client.SendMessage(context.Background(), SendMessageRequest{
		ChatID: 112233,
		Text:   "Xin chào từ Crove Desk!",
	})
	if err != nil {
		t.Fatalf("SendMessage failed: %v", err)
	}
	if msg.MessageID != 101 || msg.Text != "Xin chào từ Crove Desk!" {
		t.Fatalf("unexpected sent message: %+v", msg)
	}
}

func TestTelegramClient_SetAndDeleteWebhook(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/bot123456:ABC-DEF/setWebhook":
			_ = json.NewEncoder(w).Encode(APIResponse[bool]{OK: true, Result: true})
		case "/bot123456:ABC-DEF/deleteWebhook":
			_ = json.NewEncoder(w).Encode(APIResponse[bool]{OK: true, Result: true})
		case "/bot123456:ABC-DEF/getWebhookInfo":
			_ = json.NewEncoder(w).Encode(APIResponse[WebhookInfo]{
				OK: true,
				Result: WebhookInfo{
					URL: "https://desk.crove.com/api/third/telegram/webhook/ch_123",
				},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer ts.Close()

	client := NewClient("123456:ABC-DEF")
	client.SetBaseURL(ts.URL)

	err := client.SetWebhook(context.Background(), SetWebhookRequest{
		URL: "https://desk.crove.com/api/third/telegram/webhook/ch_123",
	})
	if err != nil {
		t.Fatalf("SetWebhook failed: %v", err)
	}

	info, err := client.GetWebhookInfo(context.Background())
	if err != nil || info.URL != "https://desk.crove.com/api/third/telegram/webhook/ch_123" {
		t.Fatalf("GetWebhookInfo failed: %+v, err: %v", info, err)
	}

	err = client.DeleteWebhook(context.Background())
	if err != nil {
		t.Fatalf("DeleteWebhook failed: %v", err)
	}
}
