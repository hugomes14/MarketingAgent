# Procedimento: documentacao de CLI tools

## Quando carregar

Carregar quando a tarefa envolver README de tool, `--help`, exemplos de comandos, docs operacionais ou referencia de automacao.

## Objetivo

Garantir que CLI tools sejam operaveis por humanos e agentes sem depender de conhecimento implicito.

## Regras

- Documentar comando principal e subcomandos.
- Explicar pre-condicoes, entradas, saidas e efeitos colaterais.
- Incluir exemplos copiaveis e seguros.
- Informar variaveis de ambiente sem expor valores reais.
- Documentar exit codes quando forem parte do contrato.
- Manter `--help`, README e registro em `AgentDATA/tools/` coerentes.
- Nao inflar docs com texto promocional; focar em operacao e manutencao.

## Estrutura recomendada

1. Objetivo da tool.
2. Instalacao ou localizacao.
3. Comando rapido.
4. Subcomandos e flags principais.
5. Exemplos.
6. Validacao ou smoke test.
7. Troubleshooting.
8. Riscos e limites.

## Validacao

Depois de atualizar docs de tool:

```bash
<comando> --help
```

Comparar exemplos documentados com a CLI real sempre que houver implementacao disponivel.
