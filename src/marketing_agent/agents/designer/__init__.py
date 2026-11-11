"""Papel Designer: escolhe o layout e a imagem de uma publicacao; nunca escreve prompts."""

from pathlib import Path

# Pasta com o spec YAML e o prompt do papel (resolvida a partir do modulo, nao do cwd).
DESIGNER_DIR = Path(__file__).resolve().parent
