#!/bin/sh
# Syntax-check every frontend file (store.js alone is not enough).
set -eu
for f in web/*.js; do
  echo "check $f"
  node --check "$f"
done
echo "web syntax OK"
