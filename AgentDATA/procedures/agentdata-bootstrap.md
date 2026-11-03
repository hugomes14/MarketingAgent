# Procedimento: AgentDATA bootstrap

## Quando carregar

Carregar quando `AgentDATA/` nao existir, estiver incompleto ou quando a tarefa envolver memoria, logs, scripts, referencias, segredos locais, conhecimento, procedimentos, skills, MCP servers ou CLI tools.

## Objetivo

Garantir uma area organizada para contexto vivo, rastreabilidade e conhecimento reutilizavel.

## Estrutura obrigatoria

- `context/`: contexto de sessao, execucao e configuracao.
- `memories/`: aprendizados e memorias com timestamp ISO 8601.
- `docs/`: documentos auxiliares em Markdown.
- `logs/`: logs estruturados, preferencialmente JSON.
- `scripts/`: scripts auxiliares em TypeScript.
- `references/`: exemplos e materiais de consulta.
- `secrets/`: placeholders e instrucoes de segredos locais.
- `Knowledge/`: registros tecnicos reutilizaveis.
- `procedures/`: procedimentos dinamicos.
- `skills/`: catalogo de skills esperadas, instaladas ou recomendadas.
- `mcp/`: catalogo de MCP servers, contratos e requisitos de credenciais.
- `tools/`: catalogo de CLI tools, automacoes e comandos operacionais.

Cada pasta deve ter `README.md` e template adequado quando fizer sentido.

## Contratos

- Memorias devem conter timestamp ISO 8601.
- Logs devem seguir formato estruturado JSON.
- Scripts devem ser TypeScript e ter nome descritivo.
- Documentos em `docs/` devem ser Markdown e seguir `{timestamp}_{descricao}.md`.
- Registros em `Knowledge/` devem seguir `AgentDATA/Knowledge/knowledge_template.md`.
- Segredos reais nao devem ser commitados.

## Validacao

Verificar existencia das pastas e templates. Revisar `git status` antes de stage.
