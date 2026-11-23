# WP-8: macOS Standalone Application Packaging & Release

**Status:** Planned  
**Base:** `main` (`48a1aba`)  
**Goal:** Resolve module dependency graph recursion issues during `py2app` compilation and deliver a standalone, redistributable `Jarvis.app` and `.dmg` installer for macOS.

---

## Context and boundaries

As documented in `apps/jarvis/docs/release-targets.md`, the current macOS output produced by `make app` is an alias build dependent on the local developer environment and `.venv` paths. Previous attempts to produce a standalone package failed with a `RecursionError` in `modulegraph` when traversing deep dynamic imports in optional machine learning packages (MLX, PyTorch, ONNX, and heavy audio libraries).

This package addresses bundle architecture, externalizing heavy runtimes (e.g. Ollama and local model weights) while packaging the Core UI, menu bar app, permissions engine, dictation emblem, and bridge clients into a clean, portable macOS app bundle.

---

## Milestones

1. **Dependency Graph Decoupling & Lazy Loading:**
   - Audit and refactor import structures in `apps/jarvis`: isolate optional MLX/CUDA/ONNX backends behind strict dynamic/lazy imports.
   - Configure `setup.py` / `py2app` exclusion rules (`excludes`, `packages`, `includes`) to prevent graph traversals into system-managed or platform-specific optional packages.

2. **External Runtime & Asset Strategy:**
   - Decouple Ollama binary and model weights from the application bundle; implement clean UI onboarding prompting the user or linking to system-installed Ollama.
   - Package bundled static assets (icons, sound effects, menu bar status emblems) using standard macOS app bundle structure (`Contents/Resources/`).

3. **Packaging, Entitlements, and Sandbox Settings:**
   - Configure native entitlements (`entitlements.plist`) for macOS microphone capture, accessibility (required for Wispr-style direct text insertion), and AppleScript automation.
   - Build automated distribution pipeline (`make standalone-app`, `make standalone-dmg`).

4. **Multi-Mac Clean Installation Verification:**
   - Verify execution on a secondary, clean macOS environment lacking local Python virtual environments or repository checkouts.
   - Test permission grant flows: microphone prompt, accessibility prompt, and graceful guidance if permissions are denied.

---

## Acceptance and verification

| Check | Acceptance Criteria | Target Status |
| :--- | :--- | :--- |
| `standalone-build` | `make standalone-app` completes without `RecursionError` or packaging failures | PENDING |
| `dmg-generation` | `make standalone-dmg` generates installable `Jarvis.dmg` | PENDING |
| `clean-env-run` | Application launches on a Mac without source tree or `.venv` present | PENDING |
| `permissions-flow` | Prompts for Accessibility & Microphone permissions on first run with clean fallbacks | PENDING |
| `dictation-smoke` | Push-to-talk (`⌘⇧A`) and Dictation (`⌘⇧D`) operate correctly in standalone mode | PENDING |

---

## Risk and Rollback

- **Development Impact:** The alias build (`make app`) remains default for local development so local edit-refresh cycles are not impeded.
- **Security:** Code signing and notarization keys (if introduced) must be injected via secure local/CI environment variables, never committed to source.
