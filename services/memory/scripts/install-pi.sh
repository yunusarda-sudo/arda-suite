#!/usr/bin/env bash
# ==============================================================================
# 🧠 mem0-local — Raspberry Pi 5 Automated Setup Script
# ==============================================================================
# Installs Qdrant (Docker), Ollama (Embeddings), Python venv, Systemd services,
# and configures the Shared Brain for loopback-only access.
# ==============================================================================

set -euo pipefail

MEM0_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURRENT_USER="${SUDO_USER:-$USER}"
USER_HOME="${MEM0_INSTALL_HOME:-$(getent passwd "$CURRENT_USER" | cut -d: -f6)}"
if [ -z "$USER_HOME" ]; then
  echo "Could not determine home directory for $CURRENT_USER" >&2
  exit 1
fi
SYSTEMD_DIR="${MEM0_SYSTEMD_DIR:-/etc/systemd/system}"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${CYAN}${BOLD}"
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║          🧠  mem0-local — Raspberry Pi 5 Setup  🧠             ║"
echo "║             Autonomous Shared Brain & MCP Server               ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check for Linux
if [[ "$(uname -s)" != "Linux" ]]; then
  echo -e "${YELLOW}⚠️  Warning: This installer is tailored for Raspberry Pi OS (Linux ARM64).${NC}"
  echo -e "   Detected OS: $(uname -s). Continuing in testing mode..."
fi

echo -e "\n${BOLD}[1/7] Updating package index & system tools...${NC}"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv python3-pip git curl jq openssl ca-certificates >/dev/null
  echo -e "${GREEN}✓ System dependencies installed.${NC}"
else
  echo -e "${YELLOW}ℹ Non-apt system detected. Skipping apt packages.${NC}"
fi

echo -e "\n${BOLD}[2/7] Checking Docker & Qdrant Vector Store...${NC}"
if ! command -v docker >/dev/null 2>&1; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Docker is missing. Install it from your distribution's signed package repository, then rerun." >&2
    exit 1
  fi
  echo -e "${BLUE}Installing Docker from the configured signed apt repositories...${NC}"
  sudo apt-get install -y docker.io
  sudo usermod -aG docker "$CURRENT_USER" || true
  echo -e "${GREEN}✓ Docker installed.${NC}"
fi

mkdir -p "$MEM0_DIR/qdrant_storage"
chown -R "$CURRENT_USER:$CURRENT_USER" "$MEM0_DIR/qdrant_storage" || true

if sudo docker ps -a --format '{{.Names}}' | grep -q "^mem0-qdrant$"; then
  QDRANT_BINDINGS="$(sudo docker port mem0-qdrant 6333/tcp 2>/dev/null || true)"
  case "$QDRANT_BINDINGS" in
    127.0.0.1:6333|localhost:6333|\[::1\]:6333) ;;
    *)
      echo "Existing Qdrant container is not confirmed loopback-only; inspect and secure it before continuing." >&2
      exit 1
      ;;
  esac
  echo -e "${GREEN}✓ Qdrant container already exists.${NC}"
  sudo docker start mem0-qdrant >/dev/null
else
  echo -e "${BLUE}Launching Qdrant ARM64 Docker container...${NC}"
  sudo docker run -d \
    --name mem0-qdrant \
    --restart always \
    -p 127.0.0.1:6333:6333 \
    -v "$MEM0_DIR/qdrant_storage:/qdrant/storage" \
    qdrant/qdrant:v1.19.2
  echo -e "${GREEN}✓ Qdrant container running on port 6333.${NC}"
fi

echo -e "\n${BOLD}[3/7] Setting up local Ollama models...${NC}"
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is missing. Install it using the official instructions, review the installer, then rerun this script." >&2
  exit 1
fi

# Ensure Ollama service is active
if command -v systemctl >/dev/null 2>&1; then
  if ! sudo systemctl enable --now ollama >/dev/null 2>&1; then
    echo "Could not enable/start Ollama; inspect the Ollama service before retrying." >&2
    exit 1
  fi
fi

echo -e "${BLUE}Pulling nomic-embed-text embedding model...${NC}"
if ! ollama pull nomic-embed-text; then
  echo "Could not download Ollama embedding model nomic-embed-text." >&2
  exit 1
fi
echo -e "${GREEN}✓ Embedding model ready.${NC}"
echo -e "${BLUE}Pulling qwen2.5:7b-instruct local LLM...${NC}"
if ! ollama pull qwen2.5:7b-instruct; then
  echo "Could not download Ollama LLM qwen2.5:7b-instruct." >&2
  exit 1
fi
echo -e "${GREEN}✓ Local LLM ready.${NC}"

QDRANT_READY=false
for attempt in {1..10}; do
  if curl --fail --silent http://127.0.0.1:6333/healthz >/dev/null 2>&1; then
    QDRANT_READY=true
    break
  fi
  sleep 1
done
if [ "$QDRANT_READY" != true ]; then
  echo "Qdrant did not pass its local health check at 127.0.0.1:6333." >&2
  exit 1
fi
OLLAMA_READY=false
for attempt in {1..10}; do
  if curl --fail --silent http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_READY=true
    break
  fi
  sleep 1
done
if [ "$OLLAMA_READY" != true ]; then
  echo "Ollama did not pass its local API health check at 127.0.0.1:11434." >&2
  exit 1
fi

echo -e "\n${BOLD}[4/7] Preparing Python Virtual Environment...${NC}"
cd "$MEM0_DIR"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

echo -e "${BLUE}Installing requirements.txt...${NC}"
./.venv/bin/pip install --upgrade pip -q
./.venv/bin/pip install -r requirements.txt -q
echo -e "${GREEN}✓ Python dependencies installed.${NC}"

echo -e "\n${BOLD}[5/7] Configuring Environment & Security Tokens...${NC}"
mkdir -p "$USER_HOME/.mem0"
TOKEN_FILE="$USER_HOME/.mem0/mcp-token"
if [ ! -f "$TOKEN_FILE" ]; then
  ( umask 077; openssl rand -hex 32 > "$TOKEN_FILE" )
  chown "$CURRENT_USER:$CURRENT_USER" "$TOKEN_FILE" || true
  echo -e "${GREEN}✓ Generated secure auth token at ~/.mem0/mcp-token${NC}"
fi
chmod 600 "$TOKEN_FILE"
CONFIG_ENV="$USER_HOME/.mem0/config.env"
if [ ! -f "$CONFIG_ENV" ]; then
  ( umask 077; cat << EOF > "$CONFIG_ENV"
# mem0-local Raspberry Pi Configuration
MEM0_OWNER=$CURRENT_USER
# Local by default. Remote providers require explicit configuration and send
# memory extraction content to that provider; keep provider keys out of Git.
MEM0_LLM_PROVIDER=ollama
MEM0_LLM_MODEL=qwen2.5:7b-instruct

# Network bindings
MEM0_MCP_TRANSPORT=streamable-http
MEM0_MCP_HOST=127.0.0.1
MEM0_MCP_PORT=8812
MEM0_DASHBOARD_HOST=127.0.0.1
MEM0_DASHBOARD_PORT=8899
EOF
  )
  chown "$CURRENT_USER:$CURRENT_USER" "$CONFIG_ENV" || true
  echo -e "${GREEN}✓ Created ~/.mem0/config.env${NC}"
else
  echo -e "${GREEN}✓ Existing ~/.mem0/config.env preserved.${NC}"
fi
chmod 600 "$CONFIG_ENV"

echo -e "\n${BOLD}[6/7] Installing Systemd Services & Dream Daemon Timer...${NC}"
if command -v systemctl >/dev/null 2>&1; then
  for sfile in mem0-mcp.service mem0-dashboard.service mem0-pipeline.service; do
    if [ -f "$MEM0_DIR/systemd/$sfile" ]; then
      sed -e "s|@MEM0_USER@|$CURRENT_USER|g" -e "s|@MEM0_DIR@|$MEM0_DIR|g" "$MEM0_DIR/systemd/$sfile" | sudo tee "$SYSTEMD_DIR/$sfile" >/dev/null
    fi
  done

  if [ -f "$MEM0_DIR/systemd/mem0-pipeline.timer" ]; then
    sudo cp "$MEM0_DIR/systemd/mem0-pipeline.timer" "$SYSTEMD_DIR/mem0-pipeline.timer"
  fi

  sudo systemctl daemon-reload
  sudo systemctl enable --now mem0-mcp.service mem0-dashboard.service mem0-pipeline.timer >/dev/null
  echo -e "${GREEN}✓ Systemd services (mcp, dashboard, nightly pipeline timer) enabled and running.${NC}"
else
  echo -e "${YELLOW}ℹ Systemd not active in this environment. Skipping systemctl activation.${NC}"
fi

echo -e "\n${BOLD}[7/7] Running Initial Brain Health Audit...${NC}"
if ! ./.venv/bin/python mem.py health --scope personal; then
  echo "Initial memory health check failed; inspect service dependencies and logs." >&2
  exit 1
fi

# Determine Local IP
PI_IP=127.0.0.1

echo -e "\n${GREEN}${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}🎉 SUCCESS! mem0-local Shared Brain is live on your Pi! 🎉${NC}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo -e "\n${BOLD}📊 Web Dashboard & Ask Brain:${NC}  http://127.0.0.1:8899"
echo -e "${BOLD}🔌 MCP Server (HTTP):${NC}          http://127.0.0.1:8812/mcp"
echo -e "${BOLD}🔑 Auth Token:${NC}                 saved privately in ~/.mem0/mcp-token (0600)"
echo -e "\n${BOLD}💻 Client configuration:${NC} read the token from ~/.mem0/mcp-token and configure a local client."

echo -e "\n${BOLD}📝 Obsidian Vault Bridge:${NC}"
echo -e "   - Export anytime:  ${CYAN}./mem0ctl.sh obsidian export${NC}"
echo -e "   - Two-way Inbox:   Write markdown notes in ${CYAN}~/mem0-vault/Inbox/${NC} and run ${CYAN}./mem0ctl.sh obsidian import${NC}"
echo -e "\n${BOLD}💬 Interactive Assistant:${NC}"
echo -e "   - Ask CLI:         ${CYAN}./mem0ctl.sh ask \"Your question\"${NC}"
echo -e "   - Chat CLI:        ${CYAN}./mem0ctl.sh chat${NC}"
echo -e "   - Web UI:          ${CYAN}http://${PI_IP}:8899#assistant${NC}\n"
