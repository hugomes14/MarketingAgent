# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-06T14:47:54+00:00`
- Autor: `agent`
- Assunto: Ollama local como fornecedor de teste (formato OpenAI): o papel `developer` e ignorado; teste de fumo do nucleo com `qwen2.5:14b`
- Status: `ativo`
- Retestar em: ao atualizar o Ollama ou o modelo; antes de confiar nos prompts com um modelo diferente

## Achado

1. O `/v1/chat/completions` do Ollama **ignora mensagens com o papel `developer`**: o prompt de sistema desaparece. Com o papel `system` obedece. `response_format` com `json_schema` funciona. O `/v1/messages` (formato Anthropic) tambem responde e ignora o valor da chave.
2. O nucleo funciona de ponta a ponta com `qwen2.5:14b` (entrevista, perfil, diagnostico, estrategia, aprovacao): 22 execucoes, todas `succeeded`, 1 passo cada (sem repeticoes por saida invalida), 22 reservas acertadas.

## Contexto

- Ollama em `http://127.0.0.1:11434` (WSL2), modelos: `qwen2.5:14b`, `llama3.1:8b`, `nomic-embed-text`.
- Adaptador `OpenAiClient` (Tarefa 10) enviava `developer`; corrigido para usar `system` quando `OPENAI_BASE_URL` esta definido (`build_llm_router`).

## Metodo

1. `curl` ao `/v1/chat/completions` com a mesma mensagem de sistema ("Responde sempre apenas com a palavra BANANA") nos papeis `developer` e `system`.
2. `scripts/smoke_ollama.py`: percurso completo com os servicos reais, numa base propria (`marketing_smoke`), uma padaria fictica de Braga e 6 respostas escritas.

## Comandos

```bash
docker exec marketingagent-postgres-1 psql -U marketing_agent -d postgres -c "CREATE DATABASE marketing_smoke"
MODEL=qwen2.5:14b uv run python scripts/smoke_ollama.py
```

## Evidencia

- Papel `developer`: o modelo respondeu "A capital de Portugal e Lisboa." (ignorou a instrucao). Papel `system`: respondeu "BANANA".
- Tempos por chamada com `qwen2.5:14b`: turno de entrevista 2 a 15 s; perfil 21 a 25 s; diagnostico 33 a 38 s; estrategia 27 s.
- Qualidade observada: o perfil so ficou "pronto" na 6.a resposta numa das corridas e nunca noutra (10 de 14 topicos); alguns `summary` vieram vazios; uma pergunta repetiu o tema da anterior. Nenhum JSON invalido.

## Codigo relevante

- `src/marketing_agent/llm/openai_adapter.py` (`system_role`), `src/marketing_agent/llm/factory.py`, `scripts/smoke_ollama.py`.

## Interpretacao

- Facto observado: o Ollama ignora `developer`; o fluxo completo passa com um modelo local de 14B.
- Inferencia: a qualidade das perguntas e dos resumos de um modelo local e inferior ao de um modelo comercial; serve para testar o fluxo, nao para avaliar os prompts.

## Consequencia operacional

- Qualquer servidor compativel com a API OpenAI (Ollama, LM Studio) usa o papel `system` (automatico quando `OPENAI_BASE_URL` esta definido).
- Para correr o sistema localmente sem custos: rota `diretor` com `provider: openai`, `model_id: qwen2.5:14b`, `OPENAI_BASE_URL=http://127.0.0.1:11434/v1` e um ficheiro de chave com qualquer texto; sincronizar um preco 0 para esse modelo.

## Riscos e limites

- Nao valida os adaptadores contra a Anthropic nem a OpenAI reais.
- Tempos dependem do hardware; o limite de 120 s por execucao do spec da entrevista pode ser apertado para modelos maiores.

## Pendencias

- Validar os prompts com um modelo comercial; ajustar o prompt da entrevista (perguntas repetidas, `summary` vazio, criterio de "pronto para perfil").
