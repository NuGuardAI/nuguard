// NuGuard fixture: empty asset adapter for the backend-only fixture; no frontend is vendored.
package webspa

import (
	"io/fs"
	"testing/fstest"
)

var SPA fs.FS = fstest.MapFS{}
