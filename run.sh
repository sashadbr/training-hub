#!/usr/bin/env bash
# Lance le Training Hub dans le navigateur.
cd "$(dirname "$0")"
exec .venv/bin/streamlit run app/dashboard.py "$@"
