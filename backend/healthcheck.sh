#!/bin/sh

# This script waits for the Ollama API to be available.

# Exit immediately if a command exits with a non-zero status.
set -e

# Loop until the API is responsive
until curl -sf http://ollama:11434/api/tags > /dev/null; do
  >&2 echo "Ollama is unavailable - sleeping"
  sleep 5
done

>&2 echo "Ollama is up - executing command"