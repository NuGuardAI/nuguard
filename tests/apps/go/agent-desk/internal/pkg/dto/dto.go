package dto

import "agent-desk/internal/pkg/enums"

type AuthPrincipal struct {
	UserID      int64
	Username    string
	Nickname    string
	Avatar      string
	UserType    enums.UserType
	Status      enums.Status
	Roles       []string
	Permissions []string
}

type WxWorkKFChannelConfig struct {
	OpenKfID string `json:"openKfId"`
}

type WebChannelConfig struct {
	Title           string `json:"title"`
	Subtitle        string `json:"subtitle"`
	ThemeColor      string `json:"themeColor"`
	Position        string `json:"position"`
	Width           string `json:"width"`
	UserTokenSecret string `json:"userTokenSecret,omitempty"`
}

type WechatMPChannelConfig struct {
	Title           string `json:"title"`
	Subtitle        string `json:"subtitle"`
	ThemeColor      string `json:"themeColor"`
	UserTokenSecret string `json:"userTokenSecret,omitempty"`
}

type TelegramChannelConfig struct {
	BotToken       string `json:"botToken"`
	BotUsername    string `json:"botUsername,omitempty"`
	WebhookSecret  string `json:"webhookSecret,omitempty"`
	WelcomeMessage string `json:"welcomeMessage,omitempty"`
}

type ZaloOAChannelConfig struct {
	AppID          string `json:"appId,omitempty"`
	OAID           string `json:"oaId,omitempty"`
	SecretKey      string `json:"secretKey,omitempty"`
	AccessToken    string `json:"accessToken"`
	RefreshToken   string `json:"refreshToken,omitempty"`
	WebhookSecret  string `json:"webhookSecret,omitempty"`
	WelcomeMessage string `json:"welcomeMessage,omitempty"`
}

type SlackChannelConfig struct {
	BotToken       string `json:"botToken,omitempty"`       // xoxb-... Bot Token
	SigningSecret  string `json:"signingSecret,omitempty"`  // Slack Signing Secret
	AppID          string `json:"appId,omitempty"`          // Slack App ID
	TeamID         string `json:"teamId,omitempty"`         // Slack Workspace Team ID
	TeamName       string `json:"teamName,omitempty"`       // Slack Workspace Team Name
	DefaultChannel string `json:"defaultChannel,omitempty"` // Default channel to post
}

type LarkChannelConfig struct {
	AppID             string `json:"appId,omitempty"`             // Lark custom app App ID (cli_...)
	AppSecret         string `json:"appSecret,omitempty"`         // Lark custom app App Secret
	VerificationToken string `json:"verificationToken,omitempty"` // Event subscription Verification Token
	Domain            string `json:"domain,omitempty"`            // lark (default) | feishu
}

type EmailChannelConfig struct {
	EmailAddress      string `json:"emailAddress,omitempty"`
	ForwardingAddress string `json:"forwardingAddress,omitempty"`
	SenderName        string `json:"senderName,omitempty"`
	Provider          string `json:"provider,omitempty"`
	APIKey            string `json:"apiKey,omitempty"`
	SMTPHost          string `json:"smtpHost,omitempty"`
	SMTPPort          int    `json:"smtpPort,omitempty"`
	SMTPUser          string `json:"smtpUser,omitempty"`
	SMTPPassword      string `json:"smtpPassword,omitempty"`
	WebhookSecret     string `json:"webhookSecret,omitempty"`
	WelcomeMessage    string `json:"welcomeMessage,omitempty"`
}

type DiscordChannelConfig struct {
	GuildID        string `json:"guildId,omitempty"`
	GuildName      string `json:"guildName,omitempty"`
	ChannelScope   string `json:"channelScope,omitempty"` // all | dm_only
	BotToken       string `json:"botToken,omitempty"`
	ApplicationID  string `json:"applicationId,omitempty"`
	PublicKey      string `json:"publicKey,omitempty"`
	WebhookSecret  string `json:"webhookSecret,omitempty"`
	WelcomeMessage string `json:"welcomeMessage,omitempty"`
}
