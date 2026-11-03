# AGENTS.md

# Operacao do agente neste repositorio

Este arquivo contem apenas as regras sempre ativas. Procedimentos, skills, MCP servers e
CLI tools ficam em separadores proprios dentro de `AgentDATA/` e devem ser carregados
dinamicamente quando a tarefa exigir.

## Regra mestra

O agente existe para produzir trabalho:

- correto;
- rastreavel;
- reproduzivel;
- legivel;
- modular;
- validado;
- facil de manter.

Velocidade sem trilha e erro.
Codigo sem contrato e divida.
Mudanca sem validacao e aposta.
Mudanca sem commit e bagunca.

Prioridade fixa:

1. entender o problema;
2. entender o repositorio;
3. identificar os arquivos impactados;
4. planejar a solucao;
5. implementar com o minimo de impacto necessario;
6. validar;
7. registrar achados tecnicos quando isso gerar conhecimento reutilizavel;
8. commitar mudanca relevante.

## Sequencia de pensamento obrigatoria

Antes de editar, o agente deve conseguir responder:

- qual problema esta resolvendo;
- quais arquivos serao tocados;
- por que cada arquivo sera tocado;
- qual contrato esta sendo criado ou alterado;
- se a mudanca afeta build, teste, documentacao, configuracao ou operacao;
- se exige migracao, tracing, teste novo ou commit separado.

Se ainda nao consegue responder, deve ler mais contexto antes de mexer.

## Inicializacao de toda tarefa

1. Ler este `AGENTS.md`.
2. Ler `README.md`, se existir.
3. Verificar `AgentDATA/Knowledge/` antes de repetir investigacao cara ou sensivel.
4. Verificar `AgentDATA/skills/` quando a tarefa depender de skill especializada.
5. Verificar `AgentDATA/mcp/` quando a tarefa depender de MCP server, conector ou credencial externa.
6. Verificar `AgentDATA/tools/` quando a tarefa depender de CLI tool existente ou nova.
7. Ler documentacao diretamente relevante para a tarefa.
8. Ler os arquivos diretamente relevantes por inteiro antes de edita-los.
9. Entender a estrutura do repositorio e o estado Git.
10. Identificar arquivos que serao tocados.
11. Explicar plano curto antes de implementar.

Quando `AgentDATA/` nao existir ou estiver incompleto, carregar
`AgentDATA/procedures/agentdata-bootstrap.md`.

## Procedimento padrao: Plan, Exec, Verify, Commit

1. **Plan**: delimitar escopo, contratos, arquivos afetados e validacao.
2. **Exec**: implementar a menor mudanca correta, respeitando padroes locais.
3. **Verify**: rodar typecheck, lint, testes, build e smoke test conforme aplicavel.
4. **Commit**: revisar diff, stagear apenas arquivos da tarefa e commitar mudanca relevante.

Detalhes operacionais ficam em:

- `AgentDATA/procedures/standard-workflow.md`
- `AgentDATA/procedures/git-operation.md`
- `AgentDATA/procedures/validation.md`

## Procedimentos dinamicos

Carregar apenas quando a tarefa exigir:

- `AgentDATA/procedures/agentdata-bootstrap.md`: estrutura, templates e regras de `AgentDATA/`.
- `AgentDATA/procedures/standard-workflow.md`: fluxo operacional completo.
- `AgentDATA/procedures/git-operation.md`: branch, stage, commit, pull e push.
- `AgentDATA/procedures/validation.md`: testes, lint, typecheck, build e smoke tests.
- `AgentDATA/procedures/code-quality.md`: regras detalhadas de arquitetura, nomeacao e robustez.
- `AgentDATA/procedures/logging-and-knowledge.md`: logs, tracing, memoria e registros tecnicos.
- `AgentDATA/procedures/documentation.md`: quando e como atualizar documentacao.
- `AgentDATA/procedures/skills-framework.md`: catalogo e uso de skills por projeto.
- `AgentDATA/procedures/mcp-servers.md`: catalogo de MCP servers e credenciais locais.
- `AgentDATA/procedures/cli-tool-build.md`: construcao, contrato e validacao de CLI tools.
- `AgentDATA/procedures/cli-tool-documentation.md`: documentacao de CLI tools, help e exemplos.
- `AgentDATA/procedures/npm-publish.md`: publicacao npm e validacao de pacote.
- `AgentDATA/procedures/pypi-publish.md`: publicacao PyPI e validacao de distribuicao Python.
- `AgentDATA/procedures/legacy-and-delete.md`: migracao, legado e remocao segura.

## Skills do projeto

Consultar quando a tarefa depender de capacidade especializada, ferramenta externa ou workflow reutilizavel.

- `AgentDATA/skills/README.md`: explica a pasta e o contrato de registro de skills.
- `AgentDATA/skills/skills_index.md`: indice das skills esperadas, disponiveis ou desejadas.
- `AgentDATA/skills/skill_registry_template.md`: template para registrar uma skill.
- `AgentDATA/procedures/skills-framework.md`: procedimento para decidir, validar e registrar skills.

Cada skill registrada deve indicar quando usar, quando nao usar, origem, status, dependencias, entradas, saidas e validacao de disponibilidade.

## MCP servers do projeto

Consultar quando a tarefa depender de MCP server, conector, credencial externa ou recurso remoto.

- `AgentDATA/mcp/README.md`: explica a pasta e o contrato de registro de MCP servers.
- `AgentDATA/mcp/mcp_servers_index.md`: indice dos MCP servers esperados, disponiveis ou desejados.
- `AgentDATA/mcp/mcp_server_registry_template.md`: template para registrar um MCP server.
- `AgentDATA/secrets/mcp_credentials.example.json`: template versionado de credenciais, sem valores reais.
- `AgentDATA/procedures/mcp-servers.md`: procedimento para configurar, validar e documentar MCP servers.

Credenciais reais nunca devem ser commitadas. Registrar apenas nomes de variaveis, arquivos locais esperados, escopo minimo e forma de validacao.

## CLI tools do projeto

Consultar quando a tarefa envolver ferramenta de linha de comando, script operacional, automacao local ou binario distribuivel.

- `AgentDATA/tools/README.md`: explica a pasta e o contrato de registro de CLI tools.
- `AgentDATA/tools/tools_index.md`: indice das CLI tools esperadas, disponiveis ou desejadas.
- `AgentDATA/tools/tool_registry_template.md`: template para registrar uma CLI tool.
- `AgentDATA/procedures/cli-tool-build.md`: procedimento para construir e validar CLI tools.
- `AgentDATA/procedures/cli-tool-documentation.md`: procedimento para documentar CLI tools.

Cada tool registrada deve indicar comando, objetivo, entradas, saidas, efeitos colaterais, dependencias, exemplos, validacao e riscos.

## Regras de codigo sempre ativas

### Impacto minimo

- Mexer no minimo necessario.
- Evitar refatoracao paralela irrelevante.
- Evitar espalhar logica em varios arquivos sem necessidade.
- Evitar alterar estilo global por impulso.
- Evitar renomear muita coisa sem motivo.

Mudanca grande so e aceitavel quando a tarefa exige, o impacto foi entendido e a validacao cobre a mudanca.

### Organizacao

Organizar por responsabilidade:

- separar entrada, processamento e saida quando fizer sentido;
- separar dominio, infraestrutura, interface e execucao quando isso melhorar clareza;
- manter contratos claros;
- manter testes proximos ou organizados de forma consistente com o projeto.

Evitar arquivos genericos como `helpers`, `misc`, `tempFix` ou `newCode2` para esconder responsabilidade real.

### Nomeacao

- Seguir a convencao dominante do repositorio.
- Se nao houver convencao clara, preferir camelCase para nomes internos.
- Funcoes devem usar verbo e indicar acao real.
- Argumentos devem descrever o papel do dado.
- Booleanos devem preferencialmente comecar com `is`, `has`, `can` ou `should`.
- Tipos, interfaces e classes devem seguir o padrao idiomatico da linguagem.

### Tamanho e responsabilidade

- Funcoes devem ser enxutas: ideal ate 25 linhas, aceitavel ate 40.
- Acima de 60 linhas, extrair helpers quase sempre deve ser considerado.
- Arquivos devem ter uma responsabilidade principal.
- Nao quebrar codigo apenas por numero; quebrar quando melhorar leitura, teste e manutencao.

### Codigo limpo

Produzir codigo com:

- dependencias claras;
- fluxo facil de seguir;
- validacao explicita nas fronteiras;
- tratamento de erro com contexto;
- sem duplicacao desnecessaria;
- sem codigo morto;
- sem branch inutil;
- sem abstracao ornamental;
- sem efeito colateral implicito desnecessario.

Nao usar `any`, `object`, `dict`, `map[string]any` ou equivalentes sem justificativa real quando houver sistema de tipos.

### Contratos, fronteiras e erros

Antes de implementar uma etapa, saber:

- input;
- output;
- pre-condicoes;
- pos-condicoes;
- erros relevantes;
- validacao necessaria;
- log necessario;
- documentacao necessaria;
- teste necessario.

Validar fronteiras externas quando fizer sentido: arquivo, JSON, YAML, CSV, banco, HTTP, CLI args, config, variavel de ambiente e integracoes.

Toda falha importante deve ter mensagem util, contexto suficiente para debug e nao esconder causa original relevante.

### Comentarios

Comentarios sao obrigatorios quando explicam regra nao obvia, edge case, compatibilidade, determinismo, tracing, cache ou decisao tecnica que poderia parecer bug.

Comentarios sao ruins quando repetem o codigo, narram o banal ou mascaram codigo confuso.

Funcoes publicas ou exportadas devem ter comentario de documentacao no padrao idiomatico da linguagem.

## Regra final

Autonomia e permitida. Bagunca nao.

O agente tem liberdade para executar, refatorar, validar, usar Git, criar branch, commitar e empurrar mudancas quando o fluxo permitir. Essa liberdade depende de clareza, rastreabilidade, consistencia, qualidade de codigo, disciplina operacional e respeito ao contrato do projeto.
