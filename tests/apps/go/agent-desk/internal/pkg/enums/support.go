package enums

type DocPageStatus string

const (
	DocPageStatusDraft     DocPageStatus = "draft"
	DocPageStatusPublished DocPageStatus = "published"
	DocPageStatusHidden    DocPageStatus = "hidden"
	DocPageStatusDeleted   DocPageStatus = "deleted"
)

type PostStatus string

const (
	PostStatusPending  PostStatus = "pending"
	PostStatusNormal   PostStatus = "normal"
	PostStatusResolved PostStatus = "resolved"
	PostStatusClosed   PostStatus = "closed"
	PostStatusHidden   PostStatus = "hidden"
	PostStatusDeleted  PostStatus = "deleted"
)

type CommentStatus string

const (
	CommentStatusNormal  CommentStatus = "normal"
	CommentStatusHidden  CommentStatus = "hidden"
	CommentStatusDeleted CommentStatus = "deleted"
)

type CommentAuthorType string

const (
	CommentAuthorTypeUser     CommentAuthorType = "user"
	CommentAuthorTypeEmployee CommentAuthorType = "employee"
)

type ReactionTarget string

const (
	ReactionTargetPost    ReactionTarget = "post"
	ReactionTargetComment ReactionTarget = "comment"
)

type ReactionType string

const (
	ReactionTypeLike ReactionType = "like"
)

type UserType string

const (
	UserTypeUser     UserType = "user"
	UserTypeEmployee UserType = "employee"
)
