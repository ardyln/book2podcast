#!/usr/bin/env bash
# Create the "deep-dive" speaker and episode profiles in Open Notebook.
# Run after the LLM and the Kokoro TTS model are registered under Manage -> Models.
#
#   ./setup-profiles.sh
#   LLM_MODEL=gemma4:e4b ./setup-profiles.sh      # a different Ollama model
set -euo pipefail
API="${OPEN_NOTEBOOK_API:-http://localhost:5055/api}"
LLM_MODEL="${LLM_MODEL:-gemma4-abliterated-e4b-text:latest}"
TTS_MODEL="${TTS_MODEL:-speaches-ai/Kokoro-82M-v1.0-ONNX}"
HERE="$(cd "$(dirname "$0")" && pwd)"

python3 - "$API" "$LLM_MODEL" "$TTS_MODEL" "$HERE/profiles" <<'EOF'
import json, sys, urllib.request
api, llm, tts, folder = sys.argv[1:]

def call(method, path, body=None):
    req = urllib.request.Request(api + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")

models = call("GET", "/models")
def model_id(name, kind):
    hit = [m["id"] for m in models if m.get("name") == name and m.get("type") == kind]
    if not hit:
        sys.exit(f"Model {name!r} ({kind}) is not registered in Open Notebook yet (Manage -> Models).")
    return hit[0]

speakers = json.load(open(f"{folder}/deep-dive.speakers.json"))
speakers["voice_model"] = model_id(tts, "text_to_speech")
episode = json.load(open(f"{folder}/deep-dive.episode.json"))
episode["outline_llm"] = episode["transcript_llm"] = model_id(llm, "language")

existing = {p["name"] for p in call("GET", "/speaker-profiles")}
if speakers["name"] in existing:
    print("speaker profile 'deep-dive' already exists; leaving it alone")
else:
    call("POST", "/speaker-profiles", speakers)
    print("created speaker profile 'deep-dive'")
existing = {p["name"] for p in call("GET", "/episode-profiles")}
if episode["name"] in existing:
    print("episode profile 'deep-dive' already exists; leaving it alone")
else:
    call("POST", "/episode-profiles", episode)
    print("created episode profile 'deep-dive'")
EOF
