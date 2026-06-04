#!/usr/bin/env bash
# Flow quickstart — install, seed, and run a Flow server in one command.
#
#   ./scripts/quickstart.sh [PORT]
#
# Installs the package, bootstraps first-run data, then starts the server.
# Hand the printed /llms.txt link to any LLM agent to connect it.
set -euo pipefail

# Run from the repo root regardless of where the script is invoked from.
cd "$(dirname "$0")/.."

PORT="${1:-${FLOW_PORT:-8100}}"
HOST="${FLOW_HOST:-0.0.0.0}"

echo "==> Installing Flow…"
pip install .

cat <<EOF

==> Starting Flow on http://${HOST}:${PORT}

  Board UI:           http://localhost:${PORT}/           (click "Sign in", paste an admin key)
  Agent onboarding:   http://localhost:${PORT}/llms.txt   <- hand this link to your agent
  Connect Claude Code:
      claude mcp add --transport http flow http://localhost:${PORT}/mcp \\
        --header "Authorization: Bearer <ADMIN_OR_IMPLEMENTER_KEY>"

  Press Ctrl-C to stop.

EOF

echo "==> Bootstrapping if needed (save any newly printed keys — they are shown only once)."
exec flow-serve --bootstrap --host "${HOST}" --port "${PORT}"
