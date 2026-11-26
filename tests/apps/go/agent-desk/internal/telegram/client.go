package telegram

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const defaultBaseURL = "https://api.telegram.org"

type Client struct {
	token      string
	baseURL    string
	httpClient *http.Client
}

func NewClient(token string) *Client {
	return &Client{
		token:      strings.TrimSpace(token),
		baseURL:    defaultBaseURL,
		httpClient: &http.Client{Timeout: 15 * time.Second},
	}
}

func (c *Client) SetBaseURL(url string) {
	if strings.TrimSpace(url) != "" {
		c.baseURL = strings.TrimRight(strings.TrimSpace(url), "/")
	}
}

func (c *Client) GetMe(ctx context.Context) (*User, error) {
	var resp APIResponse[User]
	if err := c.doRequest(ctx, "getMe", nil, &resp); err != nil {
		return nil, err
	}
	if !resp.OK {
		return nil, fmt.Errorf("telegram getMe failed (%d): %s", resp.ErrorCode, resp.Description)
	}
	return &resp.Result, nil
}

func (c *Client) SetWebhook(ctx context.Context, req SetWebhookRequest) error {
	var resp APIResponse[bool]
	if err := c.doRequest(ctx, "setWebhook", req, &resp); err != nil {
		return err
	}
	if !resp.OK {
		return fmt.Errorf("telegram setWebhook failed (%d): %s", resp.ErrorCode, resp.Description)
	}
	return nil
}

func (c *Client) DeleteWebhook(ctx context.Context) error {
	var resp APIResponse[bool]
	if err := c.doRequest(ctx, "deleteWebhook", nil, &resp); err != nil {
		return err
	}
	if !resp.OK {
		return fmt.Errorf("telegram deleteWebhook failed (%d): %s", resp.ErrorCode, resp.Description)
	}
	return nil
}

func (c *Client) GetWebhookInfo(ctx context.Context) (*WebhookInfo, error) {
	var resp APIResponse[WebhookInfo]
	if err := c.doRequest(ctx, "getWebhookInfo", nil, &resp); err != nil {
		return nil, err
	}
	if !resp.OK {
		return nil, fmt.Errorf("telegram getWebhookInfo failed (%d): %s", resp.ErrorCode, resp.Description)
	}
	return &resp.Result, nil
}

func (c *Client) SendMessage(ctx context.Context, req SendMessageRequest) (*Message, error) {
	if strings.TrimSpace(req.Text) == "" {
		return nil, fmt.Errorf("telegram message text is required")
	}
	if req.ChatID == 0 {
		return nil, fmt.Errorf("telegram chat_id is required")
	}

	var resp APIResponse[Message]
	if err := c.doRequest(ctx, "sendMessage", req, &resp); err != nil {
		return nil, err
	}
	if !resp.OK {
		return nil, fmt.Errorf("telegram sendMessage failed (%d): %s", resp.ErrorCode, resp.Description)
	}
	return &resp.Result, nil
}

func (c *Client) doRequest(ctx context.Context, method string, payload any, result any) error {
	if c.token == "" {
		return fmt.Errorf("telegram bot token is required")
	}

	endpoint := fmt.Sprintf("%s/bot%s/%s", c.baseURL, c.token, method)

	var bodyReader io.Reader
	if payload != nil {
		bodyBytes, err := json.Marshal(payload)
		if err != nil {
			return fmt.Errorf("marshal telegram request failed: %w", err)
		}
		bodyReader = bytes.NewBuffer(bodyBytes)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bodyReader)
	if err != nil {
		return fmt.Errorf("create telegram request failed: %w", err)
	}
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	res, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("telegram http request failed: %w", err)
	}
	defer res.Body.Close()

	bodyBytes, err := io.ReadAll(res.Body)
	if err != nil {
		return fmt.Errorf("read telegram response failed: %w", err)
	}

	if err := json.Unmarshal(bodyBytes, result); err != nil {
		return fmt.Errorf("unmarshal telegram response failed: %w (body: %s)", err, string(bodyBytes))
	}
	return nil
}
