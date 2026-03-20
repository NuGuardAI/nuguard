CREATE (a:Agent {id: "agent"})
CREATE (t:Tool {id: "tool"})
CREATE (a)-[:INVOKES]->(t)