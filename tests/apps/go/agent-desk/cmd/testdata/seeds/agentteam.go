package seeds

import "agent-desk/cmd/testdata/seedlang"

type AgentUserSeed struct {
	Username string
	Nickname string
	Code     string
}

func AgentTeamName(lang seedlang.Language) string {
	if lang == seedlang.English {
		return "Default Support Team"
	}
	return "默认客服组"
}

func AgentUsers(lang seedlang.Language, leaderUsername string) []AgentUserSeed {
	if lang == seedlang.English {
		return []AgentUserSeed{
			{
				Username: leaderUsername,
				Nickname: "Support Lead",
				Code:     "AGENT_LEADER_A",
			},
			{
				Username: "agent_a",
				Nickname: "Agent A",
				Code:     "AGENT_A",
			},
			{
				Username: "agent_b",
				Nickname: "Agent B",
				Code:     "AGENT_B",
			},
		}
	}
	return []AgentUserSeed{
		{
			Username: leaderUsername,
			Nickname: "客服组长",
			Code:     "AGENT_LEADER_A",
		},
		{
			Username: "agent_a",
			Nickname: "客服A",
			Code:     "AGENT_A",
		},
		{
			Username: "agent_b",
			Nickname: "客服B",
			Code:     "AGENT_B",
		},
	}
}
