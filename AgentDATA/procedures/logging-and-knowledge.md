# Procedimento: logs e conhecimento

## Quando carregar

Carregar quando a tarefa envolver pipeline, jobs, integracoes, processamento em lote, comportamento dificil de observar, debug sensivel ou investigacao reutilizavel.

## Logs e tracing

Logs devem conter, quando fizer sentido:

- timestamp;
- modulo;
- funcao;
- tipo de evento;
- contexto relevante;
- status;
- erro;
- duracao;
- identificador de execucao ou correlacao.

Regras:

- logar inicio e fim de etapa relevante;
- logar erro com contexto;
- nao logar segredo;
- nao transformar log em deposito caotico;
- manter nomes de eventos previsiveis;
- manter schema estavel quando o sistema depender disso.

## Knowledge

Criar um arquivo por achado principal em `AgentDATA/Knowledge/`.

O registro deve:

- seguir `AgentDATA/Knowledge/knowledge_template.md`;
- incluir timestamp ISO 8601;
- registrar contexto, metodo, comandos, evidencia, interpretacao e consequencia operacional;
- separar fato observado de inferencia tecnica;
- declarar riscos, limites do teste e quando retestar.

Antes de repetir investigacao cara ou sensivel, verificar registros existentes em `AgentDATA/Knowledge/`.
