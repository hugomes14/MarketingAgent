# Indice de CLI tools

Liste aqui as CLI tools esperadas, disponiveis ou desejadas pelo projeto.

## Tools registradas

| Tool | Status | Comando | Quando usar | Registro |
| --- | --- | --- | --- | --- |
| marketing-agent | disponivel | `uv run marketing-agent --help` | Marca, precos, reservas, migracoes, painel, worker de texto e proxy de desenvolvimento. | `marketing_agent_cli.md` |
| smoke_ollama | disponivel | `uv run python scripts/smoke_ollama.py --help` | Fumo semanal ou de um par 2C (`--eval-only`), numa base descartavel. | `smoke_ollama.md` |
| playwright | disponivel | `uv run python -m playwright --help` | Testes do painel em Chromium real, separados por `-m browser` ou `-m e2e_browser`. | `playwright.md` |
| vendor_three | disponivel | `uv run python scripts/vendor_three.py --help` | Importar o subconjunto Three.js fixado, verificando integridade e hashes. | `vendor_three.md` |
