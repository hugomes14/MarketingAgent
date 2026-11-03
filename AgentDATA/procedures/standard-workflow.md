# Procedimento: fluxo padrao

## Quando carregar

Carregar em toda tarefa de implementacao, correcao, refatoracao, documentacao relevante ou mudanca operacional.

## Plan

1. Entender pedido e escopo.
2. Ler `AGENTS.md`, `README.md` e documentos relevantes.
3. Verificar `AgentDATA/Knowledge/` antes de investigacao cara ou sensivel.
4. Ler arquivos impactados por inteiro antes de editar.
5. Identificar contratos, entradas, saidas, riscos e validacao.
6. Explicar plano curto ao usuario.

## Exec

1. Implementar a menor mudanca correta.
2. Seguir padroes locais antes de introduzir novo padrao.
3. Evitar refatoracao paralela sem necessidade.
4. Atualizar docs, testes e registros quando o comportamento exigir.
5. Registrar conhecimento novo e reutilizavel em `AgentDATA/Knowledge/`.

## Verify

1. Rodar typecheck, se existir.
2. Rodar lint, se existir.
3. Rodar testes afetados.
4. Rodar build, se aplicavel.
5. Fazer validacao funcional local quando fizer sentido.

Se uma validacao essencial falhar, a tarefa nao esta pronta.

## Commit

1. Revisar `git status`.
2. Revisar diff.
3. Stagear apenas arquivos relacionados.
4. Revisar diff staged.
5. Commitar mudanca relevante com mensagem no formato definido.
6. Fazer push quando o fluxo permitir e a branch estiver integra.
