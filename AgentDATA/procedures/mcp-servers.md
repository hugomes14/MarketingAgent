# Procedimento: MCP servers e credenciais

## Quando carregar

Carregar quando a tarefa mencionar MCP, connectors, credenciais externas, servidores de ferramentas ou integracoes que exponham recursos para o agente.

## Objetivo

Documentar MCP servers disponiveis ou desejados sem versionar segredos reais.

## Regras

- Consultar `AgentDATA/mcp/` antes de depender de um MCP server.
- Registrar servidores usando `AgentDATA/mcp/mcp_server_registry_template.md`.
- Manter credenciais reais fora do Git.
- Usar `AgentDATA/secrets/mcp_credentials.example.json` apenas como contrato de exemplo.
- Documentar variaveis de ambiente, arquivos locais esperados, escopo minimo e rotacao.
- Nao escrever tokens, chaves privadas, cookies ou credenciais reais em logs, docs ou commits.

## Sequencia

1. Identificar servidor, transporte e capacidades necessarias.
2. Verificar registro em `AgentDATA/mcp/`.
3. Confirmar quais credenciais locais sao esperadas.
4. Validar disponibilidade do servidor sem expor segredo.
5. Registrar riscos, escopo minimo e forma de rotacao quando aplicavel.

## Validacao

Um registro de MCP deve deixar claro:

- nome do servidor;
- objetivo;
- capacidades esperadas;
- transporte;
- variaveis de ambiente;
- arquivos locais esperados;
- template de credencial;
- comando ou metodo de validacao.
