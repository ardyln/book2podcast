#!/usr/bin/env bash
# Install book2podcast for the current user:
#   ~/.local/bin/book2podcast                     the command
#   ~/.local/share/book2podcast/pdf_blocks.py     Docling helper (PDF support)
#   ~/.local/share/book2podcast/docling-venv      Docling + PyTorch (CUDA), ~6 GB
#
#   ./install.sh            everything
#   ./install.sh --no-pdf   skip Docling (EPUB only)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SHARE="$HOME/.local/share/book2podcast"
mkdir -p "$HOME/.local/bin" "$SHARE" "$HOME/.config/book2podcast"

install -m 755 "$HERE/book2podcast" "$HOME/.local/bin/book2podcast"
install -m 644 "$HERE/pdf_blocks.py" "$SHARE/pdf_blocks.py"
[ -f "$HOME/.config/book2podcast/config.json" ] || install -m 600 "$HERE/config.example.json" "$HOME/.config/book2podcast/config.json"
echo "installed ~/.local/bin/book2podcast (settings: ~/.config/book2podcast/config.json)"

if [ "${1:-}" = "--no-pdf" ]; then
    exit 0
fi
# Docling needs a Python that PyTorch has wheels for; 3.11 is the safe choice.
PY="$(command -v python3.11 || true)"
if [ -z "$PY" ] && command -v uv >/dev/null; then
    uv python install 3.11 >/dev/null && PY="$(uv python find 3.11)"
fi
if [ -z "$PY" ]; then
    echo "PDF support needs Python 3.11 (install it, or install uv: https://docs.astral.sh/uv/), then re-run." >&2
    exit 1
fi
if [ ! -x "$SHARE/docling-venv/bin/python" ]; then
    echo "creating the Docling environment (downloads PyTorch, several GB)..."
    "$PY" -m venv "$SHARE/docling-venv"
    "$SHARE/docling-venv/bin/pip" install -q --upgrade pip
    "$SHARE/docling-venv/bin/pip" install -q docling
fi
"$SHARE/docling-venv/bin/python" -c "import torch, docling; print('docling ready; GPU:', torch.cuda.is_available())"
