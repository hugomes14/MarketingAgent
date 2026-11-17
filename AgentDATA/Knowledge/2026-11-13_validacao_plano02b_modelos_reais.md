# Validacao do Plano 02B com modelos reais

- Data: 2026-11-13 (Europe/Lisbon).
- Autor: Codex.
- Revisao testada: 92e6b40; worktree inicialmente limpo.
- Pedido: executar os testes depois de os modelos estarem disponiveis.

## Validacao automatica

- `.venv/bin/mypy src scripts`: sucesso, 129 ficheiros.
- `.venv/bin/ruff check .`: sucesso.
- `.venv/bin/ruff format --check .`: sucesso, 322 ficheiros.
- Suite completa: **2506 passed, 2 deselected in 106.22s**.
- TEST_DATABASE_URL aponta para o Postgres exclusivo de testes, porta 5543,
  base marketing_agent_test. As credenciais sao as ficticias de desenvolvimento.

## Teste de fumo real

Comando (sem API paga; base propria criada e eliminada pelo script):

```bash
NO_PROXY=172.28.112.1,127.0.0.1,localhost .venv/bin/python -u scripts/smoke_ollama.py \
  --posts 3 --ollama-url http://172.28.112.1:11434 \
  --server-url postgresql+psycopg://marketing_agent:marketing_agent@127.0.0.1:5543/postgres
```

O endereco e o observado nesta sessao WSL, nao uma constante para outras instalacoes.
Ambos os modelos exigidos estavam disponiveis no Ollama do Windows.

Resultado: **codigo 1, ProfileRequestFailed**.

| Passo | Tempo | Resultado |
| --- | ---: | --- |
| Entrevista | 435,6 s | Quatro respostas: tres completed, ultima needs_human |
| Perfil | 120,1 s | Falhou |
| Total | 555,7 s | FALHOU |

- Zero publicacoes; nenhuma imagem ou revisao visual chegou a ser exercitada pelo fumo.
- Diretor: unknown_outcome=2; execucoes needs_human=2 e succeeded=4; cost_entry=4.
- Contexto observado em /api/ps: qwen2.5:14b=4096 tokens (abaixo dos 8192 recomendados).
- A duracao do perfil coincide com o timeout de chamada. Isto nao prova a causa original:
  sem logs do servidor nao se distingue lentidao, transporte ou falha do runner.
- Nao foram alterados timeouts, contexto, configuracao do Windows nem reiniciados servicos.

## Pendencias

Inspecionar o Ollama/runner e configurar contexto >=8192 no servidor antes de repetir o fumo;
verificar latencia e logs do fornecedor para esclarecer unknown_outcome. Nao repetir uma chamada
paga de resultado desconhecido. O gerador deste fumo e falso (sem custo externo).

Playwright e Chromium nao estao instalados no ambiente: sem validacao visual no browser.
Os marcos manuais e a geracao paga continuam pendentes; o Plano 02B nao fica fechado por esta
suite automatica. A suite com modelos falsos passou; o percurso com modelos reais nao passou.
