# mcp

Catalogo de MCP servers que podem existir neste projeto.

Leia esta pasta quando uma tarefa depender de MCP server, conector ou recurso remoto.

Arquivos principais:

- `mcp_servers_index.md`: lista curta dos MCP servers do projeto.
- `mcp_server_registry_template.md`: modelo para registrar um novo MCP server.

Use registros de MCP para declarar servidores disponiveis ou desejados, seus contratos, escopos, variaveis de ambiente e referencias de credenciais.

Nao registre segredos reais aqui. Valores sensiveis devem ficar em configuracao local nao versionada ou em arquivos ignorados dentro de `AgentDATA/secrets/`.
