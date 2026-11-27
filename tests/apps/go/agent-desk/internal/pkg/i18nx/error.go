package i18nx

type Error struct {
	Key   string
	Args  []any
	Cause error
}

func Errorf(key string, args ...any) *Error {
	err := &Error{Key: key, Args: args}
	for _, arg := range args {
		if cause, ok := arg.(error); ok {
			err.Cause = cause
		}
	}
	return err
}

func (e *Error) Error() string {
	if e == nil {
		return ""
	}
	return e.Message(DefaultLocale)
}

func (e *Error) Message(locale string) string {
	if e == nil {
		return ""
	}
	return Getf(locale, e.Key, e.Args...)
}

func (e *Error) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Cause
}
