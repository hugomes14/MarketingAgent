# Ollama: identificadores repetidos na API compativel

- Timestamp: 2026-11-13T00:00:00+00:00 (registo diario, Europe/Lisbon).
- Autor: Codex.
- Estado: colisao observada, correcao com testes de regressao e chamadas reais curtas.

## Evidencia

O fumo com contexto 8192 e multiplicador de tempos 5 ultrapassou o erro original:

| Fase | Tempo | Resultado |
| --- | ---: | --- |
| Entrevista | 726,0 s | seis respostas completed |
| Perfil | 276,2 s | sucesso |
| Diagnostico | 521,1 s | sucesso |
| Estrategia | 240,9 s | sucesso, duas propostas |
| Biblioteca/kit | 0,1 s | sucesso |
| Base | 0,2 s | sucesso |
| Plano | 516,1 s | tres publicacoes, 2026-W47 |
| Primeiro job | 596,1 s | pacote em in_review |
| Segundo job | 530,9 s | falha no acerto de custo |

Na segunda revisao visual do segundo pacote, cost_entry recusou a UNIQUE
uq_cost_entry_provider_request_id: o servidor devolvera chatcmpl-944, ja existente na base.
A resposta tinha sido recebida, mas o acerto nao ficou gravado; o passo passou a unknown_outcome
e o job ficou queued, com AgentRunFailed. Nao foi um timeout nesta chamada.

Antes de interromper, havia um pacote em in_review, dois drafting, onze execucoes do Diretor
succeeded, quatro do Designer succeeded e tres do Revisor succeeded. O Designer tinha duas
saidas invalidas corrigidas. O terceiro job ja arrancara. Interrompeu-se apenas o processo
deste fumo com SIGINT para nao gastar mais inferencia antes de corrigir a contabilizacao;
a base descartavel foi entregue a limpeza normal do script. Nao houve publicacao externa.

## Correcao

OpenAiClient distingue endpoints oficiais de servidores compativeis pela base_url. Para um
servidor compativel, provider_request_id recebe `compat:<uuid-por-chamada>:<id-original>`.
O sufixo preserva a correlacao e cada chamada legitima recebe identidade local unica, mesmo
quando o backend reutiliza ids curtos entre chamadas ou modelos. A API oficial api.openai.com
e os seus subdominios mantem os ids originais sem alteracao.

Nao se removeu a restricao UNIQUE nem se alterou a idempotencia por reserva: repetir o acerto
da MESMA reserva continua a devolver o lancamento existente. A mudanca distingue chamadas
realmente diferentes, nao autoriza repetir um pedido cujo resultado e desconhecido.

## Validacao

- Teste com SDK falso a devolver o mesmo id duas vezes: ids auditados distintos e mesmo conteudo/uso.
- Testes parametrizados: ids oficiais conservados, com base_url ausente, oficial e regional.
- Teste integrado do motor: duas chamadas com id original repetido, duas reservas settled e
  dois lancamentos de custo sem colisao.
- Duas chamadas reais curtas ao Ollama produziram ids no formato compat, distintos e com o
  id original no sufixo; sem APIs pagas. Isto confirma a integracao real do adaptador, mas nao
  constitui uma repeticao do fumo semanal completo.
- Suite completa: 2515 passed, 3 deselected in 115.80s (antes de acrescentar o teste integrado).
- Suite focada final com motor, adaptador e Chromium: 103 passed, 1 deselected in 6.75s.
- Mypy src scripts e ruff limpos.

## Pendencias

Repetir o fumo completo de tres publicacoes com a correcao, conservando 8192 e multiplicador 5.
O teste longo interrompido nao e declarado aprovado. A correcao dos tempos ja foi confirmada
nas fases de entrevista, perfil, diagnostico e plano, mas a latencia total e elevada neste
hardware. Os marcos manuais e a geracao paga continuam independentes desta correcao.
