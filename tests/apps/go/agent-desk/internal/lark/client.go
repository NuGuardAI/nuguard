package lark

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
)

const (
	defaultBaseURL   = "https://open.larksuite.com"
	tokenRefreshLead = 5 * time.Minute
	httpTimeout      = 15 * time.Second
)

type Client struct {
	baseURL    string
	appID      string
	appSecret  string
	httpClient *http.Client

	tokenMu     sync.Mutex
	tokenValue  string
	tokenExpiry time.Time
}

// NewClient builds a Lark client for one custom app. domain accepts "lark"
// (international, default) or "feishu" (mainland China).
func NewClient(domain, appID, appSecret string) *Client {
	return &Client{
		baseURL:    BaseURLForDomain(domain),
		appID:      strings.TrimSpace(appID),
		appSecret:  strings.TrimSpace(appSecret),
		httpClient: &http.Client{Timeout: httpTimeout},
	}
}

func (c *Client) SetBaseURL(url string) {
	if strings.TrimSpace(url) != "" {
		c.baseURL = strings.TrimRight(strings.TrimSpace(url), "/")
	}
}

// tenantToken returns a cached tenant_access_token, refreshing it shortly
// before expiry. Lark answers the token endpoint with HTTP 200 and reports
// failures through the code field.
func (c *Client) tenantToken(ctx context.Context) (string, error) {
	c.tokenMu.Lock()
	defer c.tokenMu.Unlock()

	if c.tokenValue != "" && time.Now().Before(c.tokenExpiry) {
		return c.tokenValue, nil
	}

	if c.appID == "" || c.appSecret == "" {
		return "", fmt.Errorf("lark app id and app secret are required")
	}

	payload, err := json.Marshal(map[string]string{
		"app_id":     c.appID,
		"app_secret": c.appSecret,
	})
	if err != nil {
		return "", fmt.Errorf("marshal lark token request failed: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/open-apis/auth/v3/tenant_access_token/internal", bytes.NewBuffer(payload))
	if err != nil {
		return "", fmt.Errorf("create lark token request failed: %w", err)
	}
	req.Header.Set("Content-Type", "application/json; charset=utf-8")

	res, err := c.httpClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("lark token request failed: %w", err)
	}
	defer res.Body.Close()

	bodyBytes, err := io.ReadAll(res.Body)
	if err != nil {
		return "", fmt.Errorf("read lark token response failed: %w", err)
	}
	if res.StatusCode < 200 || res.StatusCode >= 300 {
		return "", fmt.Errorf("lark token http error (%d): %s", res.StatusCode, string(bodyBytes))
	}

	var resp TenantTokenResponse
	if err := json.Unmarshal(bodyBytes, &resp); err != nil {
		return "", fmt.Errorf("unmarshal lark token response failed: %w (body: %s)", err, string(bodyBytes))
	}
	if resp.Code != 0 {
		return "", fmt.Errorf("lark token error (%d): %s", resp.Code, resp.Msg)
	}
	if strings.TrimSpace(resp.TenantAccessToken) == "" {
		return "", fmt.Errorf("lark token response returned no access token")
	}

	c.tokenValue = resp.TenantAccessToken
	expiry := time.Duration(resp.Expire) * time.Second
	if expiry <= 0 {
		expiry = 2 * time.Hour
	}
	c.tokenExpiry = time.Now().Add(expiry - tokenRefreshLead)

	return c.tokenValue, nil
}

// SendMessageText posts a plain-text message into a chat (receive_id_type is
// chat_id, so group chats and p2p chats share one call shape).
func (c *Client) SendMessageText(ctx context.Context, chatID string, text string) (*SendMessageResponse, error) {
	chatID = strings.TrimSpace(chatID)
	if chatID == "" {
		return nil, fmt.Errorf("lark chat id is required")
	}
	text = strings.TrimSpace(text)
	if text == "" {
		return nil, fmt.Errorf("lark message text is required")
	}

	token, err := c.tenantToken(ctx)
	if err != nil {
		return nil, err
	}

	content, err := json.Marshal(map[string]string{"text": text})
	if err != nil {
		return nil, fmt.Errorf("marshal lark content failed: %w", err)
	}

	payload := SendMessageRequest{
		ReceiveID: chatID,
		MsgType:   "text",
		Content:   string(content),
	}

	bodyBytes, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal lark request failed: %w", err)
	}

	endpoint := c.baseURL + "/open-apis/im/v1/messages?receive_id_type=chat_id"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewBuffer(bodyBytes))
	if err != nil {
		return nil, fmt.Errorf("create lark request failed: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json; charset=utf-8")

	res, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("lark http request failed: %w", err)
	}
	defer res.Body.Close()

	respBody, err := io.ReadAll(res.Body)
	if err != nil {
		return nil, fmt.Errorf("read lark response failed: %w", err)
	}

	var resp SendMessageResponse
	if err := json.Unmarshal(respBody, &resp); err != nil {
		return nil, fmt.Errorf("unmarshal lark response failed: %w (body: %s)", err, string(respBody))
	}
	if resp.Code != 0 {
		return nil, fmt.Errorf("lark api error (%d): %s", resp.Code, resp.Msg)
	}
	return &resp, nil
}

// TextFromEventContent extracts the plain text out of a message.content JSON
// string. Returns empty for non-text or malformed content.
func TextFromEventContent(content string) string {
	var parsed struct {
		Text string `json:"text"`
	}
	if err := json.Unmarshal([]byte(content), &parsed); err != nil {
		return ""
	}
	return strings.TrimSpace(parsed.Text)
}
