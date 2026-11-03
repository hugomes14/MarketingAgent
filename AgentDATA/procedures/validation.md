# Procedimento: validacao

## Quando carregar

Carregar depois de qualquer mudanca de codigo, contrato, configuracao, build, documentacao operacional ou fluxo de execucao.

## Ordem padrao

1. Typecheck, se existir.
2. Lint, se existir.
3. Testes afetados.
4. Build, se aplicavel.
5. Validacao funcional local ou smoke test, se aplicavel.

## Testes

- Logica nova relevante deve ter teste.
- Bug corrigido deve ganhar teste de regressao quando fizer sentido.
- Funcao pura deve preferir teste unitario.
- Integracao entre modulos deve ter teste de integracao quando necessario.
- Comportamento critico deve ter validacao funcional ou smoke test.

## Regras

- Rodar os testes afetados.
- Iterar ate passar quando a falha for causada pela mudanca.
- Nao fingir que testou o que nao foi testado.
- Nao alterar snapshot, baseline ou expected file so para silenciar falha.
- Se nao existir teste adequado, criar quando fizer sentido ou registrar a lacuna.

Nada esta pronto se typecheck, lint, teste, build, contrato ou execucao principal relevante estiver quebrado pela propria mudanca.
