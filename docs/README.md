# ARDA Suite documentation

Use this index to find the guide for your component and task. The guides stay
in their component directories; Jarvis and Shared Memory have separate Python
environments and service lifecycles.

## Understand the suite

- [Product overview and quickstart](../README.md): capabilities, component
  layout, and starting points for setup and verification.
- [Engineering notes](ENGINEERING_NOTES.md): architecture, implementation
  evidence, and verification references.
- [Product layout and data policy](PRODUCT_LAYOUT.md): component boundaries,
  data ownership, and deployment/migration limits.
- [Security policy](../SECURITY.md): private vulnerability reporting,
  authentication, network boundaries, and data excluded from public reports.
- [Contributing guidelines](../CONTRIBUTING.md): principles, branching conventions,
  local verification, and Pull Request submission workflows.

## Jarvis and ARDA Core

For desktop assistant users and contributors to the bounded agent runtime:

- [Jarvis guide](../apps/jarvis/README.md): assistant setup, configuration,
  platform requirements, and development commands.
- [Jarvis and ARDA Core documentation index](../apps/jarvis/docs/README.md):
  architecture, agent behavior, release targets, setup guides, and the
  Core/Hermes specification archive.

## Shared Memory

### Installation and operations

For developers setting up the service and operators maintaining a host:

- [Shared Memory overview](../services/memory/README.md): service interfaces,
  quickstart links, and data/network boundaries.
- [Local installation](../services/memory/docs/INSTALL.md): an independent
  development environment, stdio MCP, and optional authenticated HTTP mode.
- [Raspberry Pi setup](../services/memory/docs/RASPBERRY-PI-SETUP.md): installer
  prerequisites, service checks, upgrades, and remote-access boundaries.

### Client integration

For people connecting assistants and other devices to Shared Memory:

- [One brain, many workers](../services/memory/docs/SINGLE-BRAIN.md): remote
  MCP clients, bearer tokens, host checks, and dashboard access through an SSH
  tunnel. The guide marks Tailscale Serve activation and Claude Desktop HTTP
  configuration as unverified.
- [iOS and Siri Shortcuts](../services/memory/docs/SIRI-SHORTCUTS.md): Turkish
  examples for asking and remembering through the dashboard API. Read these
  alongside the security policy: the dashboard must remain on loopback, so
  the examples do not authorize exposing its port to other devices.

### Evaluation and design

For contributors measuring retrieval or working on memory behavior:

- [Memory retrieval evaluation](../services/memory/docs/memory-evaluation.md):
  dataset format, ranking/supersession metrics, and read-only searches against
  the configured memory stack; embedding services may receive the queries.
- [Offline retrieval comparison](../services/memory/docs/retrieval-comparison.md):
  snapshot-based ranking experiments with no service calls, including limits
  on what synthetic results establish about retrieval quality.
- [Lessons Loop specification](../services/memory/docs/lessons-loop-spec.md):
  design, decision log, verified failure/fix evidence, and staged gates for
  extraction, recall, measurement, and promotion.
