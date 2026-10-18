# Contributing to ARDA Suite

Thank you for your interest in contributing to ARDA Suite! We welcome contributions that improve reliability, security, documentation, and agent developer workflows.

---

## 🎯 Contribution Principles

1. **Safety & Bound Execution First:**  
   ARDA Suite operates under deterministic `AUTO / CONFIRM / DENY` verification policies. Code changes affecting runtime execution or permissions must not bypass verification gates or security boundaries.
2. **Quality Over Quantity (No Trivial PRs):**  
   We prioritize substantive fixes, architectural improvements, and reproducible tests. Please avoid cosmetic whitespace, automated dependency bumps without context, or superficial README edits.
3. **Local-First & Privacy:**  
   Sensitive credentials, vector memory inventories, and private communication must never be committed or logged to public endpoints.

---

## 🛠️ Development Workflow

### 1. Issues & Proposals
- Before starting a major new feature or architectural refactor, please open an issue to discuss your approach.
- Check open issues with labels like `good first issue` or `help wanted` for self-contained tasks.

### 2. Branching & Commits
- Fork the repository and create a descriptive feature branch:
  ```bash
  git checkout -b feat/your-feature-name
  # or
  git checkout -b fix/your-bugfix-name
  ```
- Write clear, conventional commit messages:
  ```text
  <type>(<scope>): <concise summary>

  <detailed description of what was changed and why>
  ```
- If co-authoring with another contributor, please use standard git trailers:
  ```text
  Co-authored-by: Name <user@users.noreply.github.com>
  ```

### 3. Local Verification
Always verify formatting and tests before opening a Pull Request:
- **Python / Ruff:**
  ```bash
  uv run ruff check .
  uv run ruff format --check .
  ```
- **Tests:**
  ```bash
  pytest
  ```

### 4. Submitting Pull Requests
- Open a PR targeting the `main` branch.
- Include a descriptive summary of your changes, reference any relevant issue numbers (e.g. `Fixes #...`), and provide verification instructions.
- Ensure all CI status checks pass. Maintainers will review your PR and provide constructive feedback.

---

## 📜 Code of Conduct
Please be respectful, collaborative, and constructive in all issue discussions, code reviews, and communications.
