# Security policy

Do not publish credentials, private memory data, user vaults, provider transcripts, logs, databases, or machine-specific configuration.

For private vulnerability reports, use the repository's GitHub private vulnerability reporting form. Do not post exploitable details in a public issue.

The memory service requires authentication for its HTTP MCP transport. Keep both HTTP and dashboard listeners on loopback unless a reviewed authenticated proxy is used. Dashboard access is not a substitute for service authentication.

The dashboard refuses non-loopback bind addresses, validates local Host headers
and browser Origin values for state-changing requests, and caps JSON request
bodies at 1 MiB. It has no user login system.

The current memory tools operate on one configured `MEM0_OWNER`. HTTP token
registries may contain multiple credentials for that owner, but non-empty
scopes, other owners, and unsupported role claims are rejected because the tools
do not enforce those claims as authorization boundaries.

Jarvis shell commands other than hard-denied destructive patterns require user
confirmation. This is an approval gate, not an operating-system sandbox; do not
run Jarvis with access to data the user or agent should not be able to inspect.
