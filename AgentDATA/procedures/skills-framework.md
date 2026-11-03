# Procedimento: framework de skills

## Quando carregar

Carregar quando a tarefa mencionar skills, exigir capacidade especializada ou depender de uma ferramenta que pode ser encapsulada como skill.

## Objetivo

Manter um catalogo leve de skills esperadas por projeto sem duplicar a implementacao da skill.

## Regras

- Consultar `AgentDATA/skills/` antes de instalar, criar ou depender de uma skill.
- Registrar skills esperadas usando `AgentDATA/skills/skill_registry_template.md`.
- Referenciar origem, versao, quando usar, quando nao usar e como validar disponibilidade.
- Nao guardar segredos no registro da skill.
- Nao copiar documentacao longa de terceiros; apontar para a fonte.

## Sequencia

1. Identificar se a tarefa exige skill especializada.
2. Verificar se existe registro em `AgentDATA/skills/`.
3. Validar se a skill esta disponivel no ambiente.
4. Se ausente, registrar status e seguir fallback aprovado pelo contexto.
5. Atualizar o registro quando a expectativa de uso mudar.

## Validacao

Um registro de skill deve deixar claro:

- nome;
- origem;
- status;
- gatilho de uso;
- entradas e saidas esperadas;
- dependencias;
- metodo de validacao.
