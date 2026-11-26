package response

type WidgetConfigResponse struct {
	ChannelID   string `json:"channelId"`
	ChannelType string `json:"channelType"`
	Title       string `json:"title"`
	Subtitle    string `json:"subtitle"`
	ThemeColor  string `json:"themeColor"`
	Position    string `json:"position"`
	Width       string `json:"width"`
}
