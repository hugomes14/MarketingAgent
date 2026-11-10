# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-10T10:00:00+00:00`
- Autor: `agent`
- Assunto: Teste de fumo do texto semanal (Plano 02a) com `qwen2.5:14b` e `llama3.1:8b` no Ollama: tempos, voltas do Revisor, saidas invalidas e dois limites do `json_schema` do Ollama
- Status: `ativo`
- Retestar em: ao atualizar o Ollama ou os modelos; ao acrescentar um esquema de saida novo; ao mudar prompts do Copywriter, do Revisor ou do plano

## Achado

1. O runner do Ollama (llama.cpp) **cai com HTTP 500** ("model runner has unexpectedly stopped") quando o `json_schema` do `response_format` leva (a) `pattern` com `\d` (era o `semana` do plano) ou (b) uma repeticao grande: `maxLength: 10000` na legenda do Copywriter. Bisseccao direta com `curl`/`urllib`: `maxLength` 1500 funciona, 2200 ja nao; `maxItems` 100 e `maxLength` 600 funcionam. O motor trata o 500 como `unknown_outcome` (a chamada nao e repetida) e a execucao acaba em `needs_human`.
2. Com os dois limites movidos para validadores em codigo (`PlanoSemanal.semana`, `RascunhoTexto.legenda`), o percurso completo passa: plano de 3 publicacoes, worker, lote com as 3 em `in_review`.
3. Uma tentativa de job do Copywriter falhou com `unknown_outcome` (erro de transporte do Ollama, causa nao apurada; coincidiu com a alternancia entre os dois modelos) e a segunda tentativa do mesmo job passou: 4 jobs executados para 3 publicacoes, sem revisao duplicada.

## Contexto

- Ollama em `http://127.0.0.1:11434` (WSL2); `qwen2.5:14b` para Diretor e Copywriter, `llama3.1:8b` para o Revisor; contexto efetivo do `qwen2.5:14b` observado em `/api/ps`: 4096.
- Base descartavel `marketing_agent_smoke_*` no Postgres de teste (5433), nunca a de desenvolvimento.

## Metodo

1. `scripts/smoke_ollama.py --posts 3` (tentativas 1, 3: o plano caiu; 2: o perfil falhou com 3 saidas invalidas; 4: o Copywriter caiu nas 3 publicacoes; 5: passou).
2. Reproducao direta contra `/v1/chat/completions` com o esquema de cada modelo Pydantic e remocao de palavras-chave (`pattern`, `format`, `maxLength`, `maxItems`, `minLength`) ate encontrar a que faz o runner cair.

## Comandos

```bash
uv run python scripts/smoke_ollama.py --posts 3
curl -s http://127.0.0.1:11434/v1/chat/completions -H 'Content-Type: application/json' -d @pedido.json
```

## Evidencia

Quinta tentativa (a que passou), tempos em segundos: entrevista 46,0 (6 respostas), perfil 18,8, diagnostico 20,7, estrategia 16,9, base 0,0, plano semanal 43,4 (3 publicacoes, semana 2026-W47), jobs do worker 8,2 + 15,9 + 33,5 + 32,0 (o 4.o e a segunda tentativa do job que falhou), lote 0,0; total 235,5.

- Voltas do Revisor: 0 nas 3 publicacoes (`review_rounds`). Pareceres: `aprovar`, `aprovar` e `rejeitar` com a violacao `instrucao_suspeita`; a terceira vai para o responsavel com as notas, como previsto, e fica `in_review`.
- Saidas invalidas: Diretor `invalid_output` 1 (entrevista ou plano, repetido pelo motor); Copywriter `unknown_outcome` 1 (a falha de transporte); Revisor nenhuma.
- Execucoes: diretor/succeeded 11, copywriter/succeeded 3, copywriter/failed 1, revisor/succeeded 3. 18 `cost_entry` e 18 reservas `settled` (mais 1 `unknown_outcome`). Custo 0.
- Variabilidade entre corridas do mesmo script: o perfil falhou uma vez com 3 saidas invalidas seguidas (modelo local de 14B); a entrevista levou 43 a 66 s.

## Codigo relevante

- `src/marketing_agent/planning/schemas.py` (`PlanoSemanal._week_has_iso_shape`), `src/marketing_agent/content/schemas.py` (`_check_caption_length`), `tests/llm/test_output_schema_limits.py` (guardas: sem `pattern` e sem repeticoes acima de 1500 nos esquemas enviados ao modelo), `scripts/smoke_ollama.py`.

## Interpretacao

- Facto observado: o 500 reproduz-se com o esquema isolado, sem prompt do projeto; desaparece ao retirar `pattern` (plano) ou `maxLength` da legenda (Copywriter).
- Inferencia: a gramatica de repeticoes grandes e de `\d{4}` expande-se de forma a rebentar o runner; o limiar exato (entre 1500 e 2200) depende dos outros limites do esquema e da versao do Ollama.
- A causa do `unknown_outcome` isolado do Copywriter nao foi confirmada.

## Consequencia operacional

- Esquemas de saida enviados a modelos servidos pelo Ollama: sem `pattern` e sem `maxLength`/`maxItems` acima de 1500; os limites reais verificam-se em codigo. A guarda esta na suite.
- O smoke deve ser repetido apos mudar prompts/specs; uma falha no perfil ou na entrevista e variabilidade do modelo local, nao do codigo.

## Riscos e limites

- Os tempos dependem do hardware e de o modelo ja estar carregado. Uma unica corrida bem-sucedida; sem estatistica.
- O Ollama nao foi alterado nem reiniciado.
- O contexto de 4096 tokens do `qwen2.5:14b` e o padrao do Ollama (`num_ctx`): um pedido do Copywriter com muitos itens da base e exemplos pode ser truncado em silencio. Nao verificado nesta corrida.

## Pendencias

- Investigar o `unknown_outcome` do Copywriter (logs do Ollama) se voltar a aparecer.
- O `unknown_outcome` nao repete a chamada: uma falha de transporte do Ollama custa uma tentativa do job (de 3).
