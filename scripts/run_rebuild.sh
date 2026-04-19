#!/bin/bash
cd ~/projects/japan-ir-search
export EDINET_API_KEY=421cddcec57c43e59b5094dc1e91b370
exec uv run python3 -u scripts/rebuild_month.py 30
