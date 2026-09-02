import React from 'react';

function Comment({ req }) {
  return <div dangerouslySetInnerHTML={{ __html: req.query.html }} />;
}

export default Comment;
