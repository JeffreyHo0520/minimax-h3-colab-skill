#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: run_colab_inference.sh --image IMAGE [--image IMAGE ...] --prompt PROMPT.txt [--output OUTPUT.mp4]

Run one MiniMax H3 Ref2VA inference on Colab. Repeat --image to provide 1–9
ordered local reference images; the local UTF-8 prompt file is sent verbatim.

Options:
  -i, --image PATH    Local reference image (required; repeat 1–9 times)
  -p, --prompt PATH   Local UTF-8 prompt text file (required)
  -o, --output PATH   Output MP4 path (default: next to the first image)
  -h, --help          Show this help

Environment overrides:
  COLAB_AUTH            CLI auth provider (default: oauth2; adc is also supported)
  COLAB_GPU             Colab GPU model (default: A100)
  COLAB_HIGH_MEM       Request high-RAM machine shape (default: 1)
  COLAB_EXEC_TIMEOUT    Notebook execution timeout in seconds (default: 3600)
  COLAB_SESSION_NAME    Name for the session created for this run
  H3_DURATION_SECONDS   Requested clip length in seconds (default: 12)

Prompt image tags follow supplied order: first --image is <Picture 1>,
second --image is <Picture 2>, and so on.
EOF
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
RUNNER="$SCRIPT_DIR/scripts/runner.py"
INPUT_IMAGES=()
PROMPT_FILE=""
OUTPUT_TARGET=""

while (($#)); do
  case "$1" in
    -i|--image)
      (($# >= 2)) || { echo "Missing path after $1" >&2; usage >&2; exit 2; }
      INPUT_IMAGES+=("$2")
      shift 2
      ;;
    -p|--prompt)
      (($# >= 2)) || { echo "Missing path after $1" >&2; usage >&2; exit 2; }
      PROMPT_FILE="$2"
      shift 2
      ;;
    -o|--output)
      (($# >= 2)) || { echo "Missing path after $1" >&2; usage >&2; exit 2; }
      OUTPUT_TARGET="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ((${#INPUT_IMAGES[@]} < 1 || ${#INPUT_IMAGES[@]} > 9)); then
  echo "Provide between 1 and 9 reference images with --image." >&2
  usage >&2
  exit 2
fi
if [[ -z "$PROMPT_FILE" ]]; then
  echo "A local prompt text file is required; pass it with --prompt." >&2
  usage >&2
  exit 2
fi
for input_path in "${INPUT_IMAGES[@]}"; do
  [[ -f "$input_path" && -s "$input_path" ]] || { echo "Reference image is missing or empty: $input_path" >&2; exit 2; }
done
[[ -f "$PROMPT_FILE" && -s "$PROMPT_FILE" ]] || { echo "Prompt file is missing or empty: $PROMPT_FILE" >&2; exit 2; }
[[ -f "$RUNNER" ]] || { echo "Bundled runner not found: $RUNNER" >&2; exit 2; }
command -v colab >/dev/null 2>&1 || { echo "Colab CLI is missing. Install it with: uv tool install google-colab-cli" >&2; exit 127; }

HIGH_MEM="${COLAB_HIGH_MEM:-1}"
[[ "$HIGH_MEM" =~ ^(0|1)$ ]] || { echo "COLAB_HIGH_MEM must be 0 or 1." >&2; exit 2; }
EXEC_TIMEOUT="${COLAB_EXEC_TIMEOUT:-3600}"
[[ "$EXEC_TIMEOUT" =~ ^[0-9]+$ ]] && ((EXEC_TIMEOUT > 0)) || { echo "COLAB_EXEC_TIMEOUT must be a positive integer." >&2; exit 2; }

ARGS=(single)
for input_path in "${INPUT_IMAGES[@]}"; do ARGS+=(--image "$input_path"); done
ARGS+=(--prompt "$PROMPT_FILE" --gpu "${COLAB_GPU:-A100}" --timeout "$EXEC_TIMEOUT")
if [[ -n "$OUTPUT_TARGET" ]]; then ARGS+=(--output "$OUTPUT_TARGET"); fi
if (( ! HIGH_MEM )); then ARGS+=(--no-high-mem); fi

exec python3 "$RUNNER" "${ARGS[@]}"
