"""Papel Revisor: parecer independente sobre o rascunho de texto; nunca o edita."""

from pathlib import Path

# Pasta com o spec YAML e o prompt do papel (resolvida a partir do modulo, nao do cwd).
REVISOR_DIR = Path(__file__).resolve().parent
