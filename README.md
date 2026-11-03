# Starter Repo

Seed de repositorio para codar com agentes.

O objetivo e manter um contrato operacional pequeno no `AGENTS.md` e mover procedimentos situacionais para `AgentDATA/procedures/`, permitindo que o agente carregue apenas o que for necessario para cada tarefa.

## Bootstrap de primeira execucao

Se este repositorio ainda estiver como seed, o primeiro agente deve parar e perguntar ao usuario:

1. O que e este projeto?
2. O que vamos construir ou manter aqui?
3. Qual stack, runtime, linguagem e fluxo de entrega esperado?
4. Quais comandos de setup, validacao, build e execucao devem existir?
5. Quais skills, MCP servers e CLI tools devem ser registrados desde o inicio?

Depois das respostas, o agente deve reescrever este `README.md` para deixar de ser starter e virar o README real do projeto.
O README final deve conter objetivo, estrutura, setup, comandos principais, validacao, operacao e referencias para `AGENTS.md` e `AgentDATA/`.

## Estrutura

- `AGENTS.md`: regras sempre ativas e indice de procedimentos dinamicos.
- `AgentDATA/`: contexto vivo, registros, procedimentos, logs, scripts e conhecimento reutilizavel.
- `AgentDATA/procedures/`: procedimentos carregados sob demanda pelo agente.
- `AgentDATA/skills/`: catalogo de skills relevantes para projetos derivados desta seed.
- `AgentDATA/mcp/`: catalogo de MCP servers, requisitos e referencias de credenciais.
- `AgentDATA/tools/`: catalogo de CLI tools, comandos operacionais e automacoes.

## Fluxo padrao

Toda mudanca relevante deve seguir:

1. planejar;
2. executar;
3. validar;
4. commitar.

Detalhes operacionais estao em `AgentDATA/procedures/standard-workflow.md`.

## Skills, MCP e tools

Use `AgentDATA/skills/skill_registry_template.md` para registrar skills esperadas por projeto.

Use `AgentDATA/mcp/mcp_server_registry_template.md` para registrar MCP servers disponiveis ou desejados. Credenciais reais devem ficar fora do Git; use apenas templates versionados em `AgentDATA/secrets/`.

Use `AgentDATA/tools/tool_registry_template.md` para registrar CLI tools disponiveis ou desejadas.

Os indices `AgentDATA/skills/skills_index.md`, `AgentDATA/mcp/mcp_servers_index.md` e `AgentDATA/tools/tools_index.md` devem ser atualizados pelos projetos derivados quando novas capacidades forem adicionadas.
