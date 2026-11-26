package lark

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
)

func TestBaseURLForDomain(t *testing.T) {
	cases := []struct {
		domain string
		want   string
	}{
		{domain: "", want: "https://open.larksuite.com"},
		{domain: "lark", want: "https://open.larksuite.com"},
		{domain: "feishu", want: "https://open.feishu.cn"},
		{domain: "FEISHU", want: "https://open.feishu.cn"},
		{domain: "something-else", want: "https://open.larksuite.com"},
	}
	for _, tc := range cases {
		if got := BaseURLForDomain(tc.domain); got != tc.want {
			t.Errorf("BaseURLForDomain(%q) = %q, want %q", tc.domain, got, tc.want)
		}
	}
}

func TestTextFromEventContent(t *testing.T) {
	if got := TextFromEventContent(`{"text":"  hello there  "}`); got != "hello there" {
		t.Errorf("TextFromEventContent = %q", got)
	}
	if got := TextFromEventContent(`not-json`); got != "" {
		t.Errorf("malformed content = %q, want empty", got)
	}
	if got := TextFromEventContent(`{"post":"rich"}`); got != "" {
		t.Errorf("non-text content = %q, want empty", got)
	}
}

// The token endpoint answers HTTP 200 and reports failures through the code
// field, so the envelope - not the status code - decides success.
func TestTenantTokenFailureSurfacesLarkError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/open-apis/auth/v3/tenant_access_token/internal" {
			t.Errorf("token path = %q", r.URL.Path)
		}
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["app_id"] != "cli_test" || body["app_secret"] != "secret" {
			t.Errorf("credentials = %+v", body)
		}
		_, _ = w.Write([]byte(`{"code":99991663,"msg":"app not found"}`))
	}))
	defer server.Close()

	client := NewClient("", "cli_test", "secret")
	client.SetBaseURL(server.URL)
	if _, err := client.SendMessageText(context.Background(), "oc_chat", "hello"); err == nil {
		t.Fatal("expected the lark token error to surface")
	}
}

// The tenant token is fetched once and reused until shortly before expiry;
// a burst of sends must not hammer the token endpoint per call.
func TestSendMessageTextCachesTenantToken(t *testing.T) {
	var mu sync.Mutex
	tokenCalls := 0
	messageCalls := 0

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		switch r.URL.Path {
		case "/open-apis/auth/v3/tenant_access_token/internal":
			tokenCalls++
			_, _ = w.Write([]byte(`{"code":0,"tenant_access_token":"tk_test","expire":7200}`))
		case "/open-apis/im/v1/messages":
			messageCalls++
			if r.URL.Query().Get("receive_id_type") != "chat_id" {
				t.Errorf("receive_id_type = %q", r.URL.Query().Get("receive_id_type"))
			}
			if r.Header.Get("Authorization") != "Bearer tk_test" {
				t.Errorf("authorization = %q", r.Header.Get("Authorization"))
			}
			var body struct {
				ReceiveID string `json:"receive_id"`
				MsgType   string `json:"msg_type"`
				Content   string `json:"content"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.ReceiveID != "oc_chat" || body.MsgType != "text" {
				t.Errorf("payload = %+v", body)
			}
			if body.Content != `{"text":"hello"}` {
				t.Errorf("content = %q", body.Content)
			}
			_, _ = w.Write([]byte(`{"code":0,"data":{"message_id":"om_1"}}`))
		default:
			t.Errorf("unexpected path %q", r.URL.Path)
			_, _ = w.Write([]byte(`{}`))
		}
	}))
	defer server.Close()

	client := NewClient("", "cli_test", "secret")
	client.SetBaseURL(server.URL)
	for i := 0; i < 3; i++ {
		if _, err := client.SendMessageText(context.Background(), "oc_chat", "hello"); err != nil {
			t.Fatalf("SendMessageText %d: %v", i, err)
		}
	}

	if tokenCalls != 1 {
		t.Errorf("token endpoint called %d times, want 1", tokenCalls)
	}
	if messageCalls != 3 {
		t.Errorf("message endpoint called %d times, want 3", messageCalls)
	}
}

func TestSendMessageTextRejectsBlankInputs(t *testing.T) {
	client := NewClient("", "cli", "secret")
	if _, err := client.SendMessageText(context.Background(), "", "hello"); err == nil {
		t.Error("blank chat id must be rejected")
	}
	if _, err := client.SendMessageText(context.Background(), "oc_chat", "   "); err == nil {
		t.Error("blank text must be rejected")
	}
}
