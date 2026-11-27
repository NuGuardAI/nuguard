package zalo

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const defaultBaseURL = "https://openapi.zalo.me"

type Client struct {
	accessToken string
	baseURL     string
	httpClient  *http.Client
}

func NewClient(accessToken string) *Client {
	return &Client{
		accessToken: strings.TrimSpace(accessToken),
		baseURL:     defaultBaseURL,
		httpClient:  &http.Client{Timeout: 15 * time.Second},
	}
}

func (c *Client) SetBaseURL(u string) {
	if strings.TrimSpace(u) != "" {
		c.baseURL = strings.TrimRight(strings.TrimSpace(u), "/")
	}
}

func (c *Client) SendCSMessage(ctx context.Context, userID string, text string) (*APIResponse, error) {
	if strings.TrimSpace(userID) == "" {
		return nil, fmt.Errorf("zalo user_id is required")
	}
	if strings.TrimSpace(text) == "" {
		return nil, fmt.Errorf("zalo message text is required")
	}

	reqPayload := SendMessageRequest{
		Recipient: UserRef{ID: strings.TrimSpace(userID)},
		Message:   SendContent{Text: text},
	}

	var resp APIResponse
	if err := c.doPost(ctx, "/v3.0/oa/message/cs", reqPayload, &resp); err != nil {
		return nil, err
	}
	if resp.Error != 0 {
		return nil, fmt.Errorf("zalo send message failed (%d): %s", resp.Error, resp.Message)
	}
	return &resp, nil
}

func (c *Client) GetUserProfile(ctx context.Context, userID string) (*UserProfile, error) {
	if strings.TrimSpace(userID) == "" {
		return nil, fmt.Errorf("zalo user_id is required")
	}

	params := url.Values{}
	params.Set("data", fmt.Sprintf(`{"user_id":"%s"}`, strings.TrimSpace(userID)))

	var resp UserProfileResponse
	if err := c.doGet(ctx, "/v3.0/oa/user/detail?"+params.Encode(), &resp); err != nil {
		return nil, err
	}
	if resp.Error != 0 {
		return nil, fmt.Errorf("zalo get user profile failed (%d): %s", resp.Error, resp.Message)
	}
	return &resp.Data, nil
}

func (c *Client) doPost(ctx context.Context, path string, payload any, result any) error {
	if c.accessToken == "" {
		return fmt.Errorf("zalo access_token is required")
	}

	endpoint := fmt.Sprintf("%s%s", c.baseURL, path)
	bodyBytes, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal zalo request failed: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewBuffer(bodyBytes))
	if err != nil {
		return fmt.Errorf("create zalo request failed: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("access_token", c.accessToken)

	res, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("zalo http request failed: %w", err)
	}
	defer res.Body.Close()

	body, err := io.ReadAll(res.Body)
	if err != nil {
		return fmt.Errorf("read zalo response failed: %w", err)
	}

	if err := json.Unmarshal(body, result); err != nil {
		return fmt.Errorf("unmarshal zalo response failed: %w (body: %s)", err, string(body))
	}
	return nil
}

func (c *Client) doGet(ctx context.Context, path string, result any) error {
	if c.accessToken == "" {
		return fmt.Errorf("zalo access_token is required")
	}

	endpoint := fmt.Sprintf("%s%s", c.baseURL, path)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return fmt.Errorf("create zalo request failed: %w", err)
	}
	req.Header.Set("access_token", c.accessToken)

	res, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("zalo http request failed: %w", err)
	}
	defer res.Body.Close()

	body, err := io.ReadAll(res.Body)
	if err != nil {
		return fmt.Errorf("read zalo response failed: %w", err)
	}

	if err := json.Unmarshal(body, result); err != nil {
		return fmt.Errorf("unmarshal zalo response failed: %w (body: %s)", err, string(body))
	}
	return nil
}
