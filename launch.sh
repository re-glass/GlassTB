#!/usr/bin/env bash
# Launch GlassTB (Trading Bot GUI)
cd "$(dirname "$0")"
exec venv/bin/python gui/app.py
