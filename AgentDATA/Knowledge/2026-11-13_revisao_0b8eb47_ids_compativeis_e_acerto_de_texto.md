# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-13T00:00:00+00:00`
- Autor: `agent`
- Assunto: limites conhecidos de 0b8eb47 (ids `compat:`) e questao parqueada sobre o acerto de texto
- Status: `ativo`
- Retestar em: antes de ligar um gateway compativel pago, e quando se avaliar o tratamento de falhas de acerto de texto

## Achado

Revisao independente (read-only) de 264612e e 0b8eb47 (2026-11-13). 0b8eb47 foi aprovado com
reservas menores; ficam registados aqui os limites que nao se corrigem nesta onda, mais uma
questao de desenho fora de ambito.

## Contexto

0b8eb47 faz o `OpenAiClient` gravar `provider_request_id = compat:<uuid por chamada>:<id original>`
quando a `base_url` nao e a da OpenAI (api.openai.com e subdominios), porque o Ollama devolve ids
curtos repetidos (`chatcmpl-944`) e a UNIQUE `uq_cost_entry_provider_request_id` rejeitava o segundo
acerto legitimo. Ver `2026-11-13_ollama_ids_repetidos.md`.

## Metodo

Leitura do diff de 0b8eb47 (`llm/openai_adapter.py`, `llm/types.py`, testes) e do caminho de acerto
de custos do motor e do pipeline de conteudo (`content/rounds.py`).

## Comandos

```bash
git show 0b8eb47 --stat
grep -n "compat" src/marketing_agent/llm/openai_adapter.py
```

## Evidencia

- O id `compat:` leva um uuid novo em CADA chamada ao adaptador, por isso dois acertos do mesmo
  pedido ao fornecedor teriam ids locais diferentes.
- O adaptador da Anthropic (`llm/anthropic_adapter.py`) continua a usar o id devolvido tal como vem
  (`_require_str(raw, "id")`), sem prefixo; aceita `base_url`, logo um gateway compativel com a API
  da Anthropic que repita ids teria a mesma colisao.
- Os clientes passam `timeout=<segundos>` ao SDK; o httpx aplica um numero unico a cada fase
  (ligar, escrever, ler, pool) e nao como limite total da chamada.

## Codigo relevante

```text
src/marketing_agent/llm/openai_adapter.py   provider_request_id=f"compat:{uuid4().hex}:{...}"
src/marketing_agent/llm/anthropic_adapter.py  provider_request_id=_require_str(raw, "id")
src/marketing_agent/content/rounds.py       AgentRunFailed (falha de execucao do papel)
```

## Interpretacao

Fato observado: os tres pontos acima.
Inferencia: (a) para um gateway compativel PAGO, a UNIQUE sobre `provider_request_id` deixa de
apanhar um duplo acerto da mesma chamada, porque o id local e novo a cada chamada; fica a proteger
so contra o duplo acerto da mesma resposta com o mesmo id (API oficial e Anthropic). A protecao
efetiva contra duplo acerto passa a ser a idempotencia por reserva (repetir o acerto da MESMA
reserva devolve o lancamento existente), que continua intacta. Aceitavel: a repeticao de uma
chamada de resultado desconhecido nunca e autorizada pelo motor. (b) O timeout por fase significa
que `LLM_CALL_TIMEOUT_S` (e o escalado por `LLM_TIME_MULTIPLIER`) nao e um teto rigido de uma
chamada; os limites derivados (leases, marcadores, limiar das reservas, proxy) tem margem fixa por
isso mesmo, mas continuam a ser uma aproximacao.

Questao parqueada (a avaliar mais tarde): um erro de infraestrutura durante o ACERTO de uma
chamada de TEXTO (por exemplo a gravacao do custo falhar depois de a resposta chegar) deixa o passo
em `unknown_outcome` e o motor devolve a execucao falhada; o pipeline levanta `AgentRunFailed` e o
worker reenfileira o job com `retry=True`. A tentativa seguinte pode regenerar, e voltar a pagar, um
pacote cuja chamada anterior ja foi faturada pelo fornecedor. Isto conflitua com a regra de nunca
repetir uma chamada de resultado desconhecido. As imagens ja seguem o caminho certo: vao para
`NeedsHuman` com `retry=False`. O fumo de 2026-11-13 mostrou o caso numa chamada do Revisor (modelo de visao, pelo mesmo
adaptador de texto): job `queued` apos `AgentRunFailed` no acerto, embora ali a causa fosse a
colisao de ids ja corrigida.

## Consequencia operacional

- Antes de ligar um gateway compativel pago: confirmar que os ids do fornecedor sao unicos por
  chamada, ou que a idempotencia por reserva chega; nao confiar na UNIQUE como rede de seguranca.
- Se se usar a Anthropic atras de um gateway com ids repetidos, acrescentar o mesmo prefixo no
  adaptador (nao feito: nao ha esse caso de uso).
- Reiniciar o worker depois de um erro de acerto de texto pode repetir uma geracao paga; a
  recuperacao imediata dos jobs orfaos no arranque (lock de worker unico) nao altera isto.

## Riscos e limites

Sem teste automatico para o gateway pago (nao ha gateway pago configurado). A questao do acerto de
texto nao foi reproduzida depois da correcao dos ids; a leitura do fluxo e estatica.

## Pendencias

- Avaliar mapear a falha de acerto de texto para `NeedsHuman` com `retry=False` (como as imagens),
  com a decisao do dono sobre o custo de nao repetir.
- Decidir se o adaptador da Anthropic ganha o mesmo prefixo quando houver gateway.
