#!/bin/sh
set -eu

mkdir -p /app/data/uploads
chown appuser:appuser /app/data /app/data/uploads
for database_file in /app/data/flashcards.db*; do
    if [ -e "$database_file" ]; then
        chown appuser:appuser "$database_file"
    fi
done

exec gosu appuser "$@"