#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
A3_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)

cd "$A3_ROOT"
pdflatex -interaction=nonstopmode Lab_3_u0000000.tex >/tmp/a3_lab_pdflatex.log 2>&1
pdflatex -interaction=nonstopmode Lab_3_u0000000.tex >/tmp/a3_lab_pdflatex.log 2>&1
pdflatex -interaction=nonstopmode research_paper.tex >/tmp/a3_paper_pdflatex.log 2>&1
pdflatex -interaction=nonstopmode research_paper.tex >/tmp/a3_paper_pdflatex.log 2>&1
