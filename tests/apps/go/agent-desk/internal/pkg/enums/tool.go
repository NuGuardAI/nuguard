package enums

type ToolSourceType string

const (
	ToolSourceTypeMCP     ToolSourceType = "mcp"
	ToolSourceTypeGraph   ToolSourceType = "graph"
	ToolSourceTypeBuiltin ToolSourceType = "builtin"
)

var ToolSourceTypeValues = []ToolSourceType{
	ToolSourceTypeMCP,
	ToolSourceTypeGraph,
	ToolSourceTypeBuiltin,
}

func IsValidToolSourceType(sourceType ToolSourceType) bool {
	for _, item := range ToolSourceTypeValues {
		if item == sourceType {
			return true
		}
	}
	return false
}
