#!/bin/sh
set -eu

python -m src.agent.session_retention
exec "$@"
