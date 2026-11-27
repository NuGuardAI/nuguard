package seeds

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/enums"

	"github.com/mlogclub/simple/common/jsons"
)

type ChannelSeed struct {
	Name        string
	ChannelType string
	ConfigJSON  string
	Remark      string
}

func ChannelSeeds(lang seedlang.Language) []ChannelSeed {
	if lang == seedlang.English {
		return []ChannelSeed{
			{
				Name:        "Website Support",
				ChannelType: enums.ChannelTypeWeb,
				ConfigJSON: jsons.ToJsonStr(dto.WebChannelConfig{
					Title:      "Online Support",
					Subtitle:   "Powered by AgentDesk",
					ThemeColor: "#2563eb",
					Position:   "right",
					Width:      "780px",
				}),
				Remark: "Local testdata seed",
			},
		}
	}
	return []ChannelSeed{
		{
			Name:        "官网客服",
			ChannelType: enums.ChannelTypeWeb,
			ConfigJSON: jsons.ToJsonStr(dto.WebChannelConfig{
				Title:      "在线客服",
				Subtitle:   "AgentDesk 提供技术支持",
				ThemeColor: "#2563eb",
				Position:   "right",
				Width:      "780px",
			}),
			Remark: "Local testdata seed",
		},
	}
}
