#!/bin/sh
# Copy the committed project (code, pilot data, queries, results) to the shared project folder.
set -e
DEST=${1:-/mnt/project-files/visongsearch/code}
mkdir -p "$DEST"
git archive --format=tar HEAD | tar -x -C "$DEST"
echo "synced $(git rev-parse --short HEAD) to $DEST"
