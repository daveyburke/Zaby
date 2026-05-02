#!/bin/bash
# Deploy the Zaby server to Cloud Run.
# Set GEMINI_API_KEY in your shell before running.
set -euo pipefail

PROJECT_ID=zaby-453603
SERVICE_NAME=zaby-server
REGION=${REGION:-us-central1}

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "GEMINI_API_KEY not set" >&2
  exit 1
fi

if [[ -z "${MEMORY_UI_PASSWORD:-}" ]]; then
  echo "MEMORY_UI_PASSWORD not set" >&2
  exit 1
fi

gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 1 \
  --cpu 1 \
  --memory 512Mi \
  --timeout 600 \
  --add-volume "name=memory,type=cloud-storage,bucket=zaby-memory" \
  --add-volume-mount "volume=memory,mount-path=/mnt/memory" \
  --set-env-vars "GEMINI_API_KEY=${GEMINI_API_KEY},MEMORY_UI_PASSWORD=${MEMORY_UI_PASSWORD}"
