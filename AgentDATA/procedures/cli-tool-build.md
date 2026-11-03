# Procedimento: construcao de CLI tools

## Quando carregar

Carregar quando a tarefa envolver criar, alterar, empacotar, instalar ou validar uma CLI tool.

## Objetivo

Construir ferramentas de linha de comando com contrato claro, comportamento previsivel e validacao reproduzivel.

## Regras de contrato

- Toda CLI deve ter `--help`.
- Toda CLI deve documentar entradas, saidas, exit codes e efeitos colaterais.
- Argumentos externos devem ser validados na fronteira.
- Erros devem informar comando, etapa e contexto suficiente para debug.
- Comandos destrutivos devem exigir confirmacao explicita ou modo dry-run quando fizer sentido.
- Saida machine-readable deve ser estavel quando for consumida por scripts.
- Segredos nunca devem aparecer em argumentos logados, stdout, stderr ou exemplos.

## Sequencia

1. Identificar usuario da tool e problema operacional.
2. Definir comando, subcomandos, flags, entradas, saidas e exit codes.
3. Escolher runtime e biblioteca CLI seguindo o padrao do projeto.
4. Implementar parsing, validacao de fronteira e tratamento de erro.
5. Adicionar `--help` e exemplos curtos.
6. Criar ou atualizar registro em `AgentDATA/tools/`.
7. Rodar typecheck, lint, testes e build quando existirem.
8. Rodar smoke test minimo com `--help` e um fluxo real seguro.

## Validacao minima

```bash
<comando> --help
<comando> <fluxo-seguro>
```

Quando a tool manipular arquivos, validar em diretorio temporario. Quando chamar rede ou servico externo, usar dry-run, sandbox, TestPyPI, registry de teste ou fixture.

## Registro

Toda CLI tool criada ou alterada deve ter registro atualizado em `AgentDATA/tools/` quando for reutilizavel pelo projeto.
