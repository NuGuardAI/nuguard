package constants

import "testing"

func TestBuiltinAuthSeedNamesDefaultToEnglish(t *testing.T) {
	t.Parallel()

	if BootstrapAdminNickname != "Super Admin" {
		t.Fatalf("BootstrapAdminNickname = %q, want %q", BootstrapAdminNickname, "Super Admin")
	}

	roles := map[string]string{}
	for _, role := range Roles {
		roles[role.Code] = role.Name
	}

	tests := map[string]string{
		RoleCodeSuperAdmin:   "Super Admin",
		RoleCodeAdmin:        "Admin",
		RoleCodeCsTeamLeader: "Support Team Lead",
		RoleCodeCsUser:       "Support Agent",
	}
	for code, want := range tests {
		if got := roles[code]; got != want {
			t.Fatalf("role %s name = %q, want %q", code, got, want)
		}
	}

	permissions := map[string]string{}
	for _, permission := range Permissions {
		permissions[permission.Code] = permission.Name
	}

	permissionTests := map[string]string{
		"user.view":         "View users",
		"ticket.create":     "Create tickets",
		"conversation.send": "Send conversation messages",
		"channel.view":      "View channels",
		"wxworkOutbox.view": "View WeCom outbox records",
		"agent.view":        "View agents",
	}
	for code, want := range permissionTests {
		if got := permissions[code]; got != want {
			t.Fatalf("permission %s name = %q, want %q", code, got, want)
		}
	}
}
