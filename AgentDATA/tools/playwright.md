# Playwright e Chromium

- Data: 2026-11-13T00:00:00+00:00 (registo diario).
- Status: disponivel; dependencia dev em pyproject.toml e uv.lock.
- Comando: `uv run python -m playwright --help`.
- Objetivo: validar o painel num Chromium real, com servicos reais e LLM falso.
- Nao usar para publicar conteudo nem para atestar a qualidade dos modelos reais.

## Instalacao

```bash
uv sync
uv run python -m playwright install chromium
```

Num sistema com administrador, as dependencias Linux podem ser instaladas com
`uv run python -m playwright install-deps chromium`. Neste ambiente sudo exige palavra-passe;
foi usada uma alternativa local, sem alterar o sistema:

```bash
mkdir -p /tmp/marketing-agent-browser-libs
cd /tmp/marketing-agent-browser-libs
apt download libatomic1 libnspr4 libnss3 libasound2t64
for package in *.deb; do dpkg-deb -x "$package" root; done
```

Copiar o conteudo de `root/` para `var/browser-deps/` na raiz do projeto (ignorado pelo Git).
Se faltarem outras bibliotecas, consultar `ldd` no binario do Chromium; os pacotes dependem da
distribuicao. As versoes instaladas nesta sessao vieram do Ubuntu noble. Nao versionar binarios.

## Execucao

A partir da raiz, com TEST_DATABASE_URL exportado para o Postgres exclusivo de testes:

```bash
LD_LIBRARY_PATH="$PWD/var/browser-deps/usr/lib/x86_64-linux-gnu" \
  uv run pytest tests/e2e/test_batch_browser.py -m browser -q
```

Sem a alternativa de bibliotecas locais, omitir LD_LIBRARY_PATH. O marcador browser fica
excluido da suite padrao; correr explicitamente e nunca em paralelo com outro pytest na mesma
base, porque as fixtures recriam as tabelas. O teste abre so um servidor loopback em porta
efemera, injeta a identidade sintetica do dono e fecha o browser/servidor no fim.

## Cobertura e limites

Testa renderizacao de imagem, selo IA, troca de foto, upload manual, revisao pelo worker,
edicao preservando imagem e aprovacao. Verifica erros JavaScript. Prepara estrategia e plano
com servicos/fixtures: nao substitui a entrevista completa no browser nem o fumo Ollama.

Saida 0: passou; outra saida: consultar o relatorio pytest. Usa disco para browsers/cache e
base efemera para dados. Nao chama fornecedores pagos. Validacao nesta sessao: 1 teste passou.


## Percursos HTTP do painel (Plano 2D, tarefa 6)

Desde 2026-11-14, `tests/browser/` inclui sete percursos automatizados: pagina inicial,
duplo envio simples e HTMX, recuperacao via pageshow, turno com idempotencia/CSRF,
erro dirigido a #estado e oficina2D com selecao do Revisor. Usa servidores loopback em
portas efemeras, guioes falsos verificados no fim e recolha de erros JS/CSP.

```bash
LD_LIBRARY_PATH="$PWD/var/browser-deps/usr/lib/x86_64-linux-gnu" \
  uv run pytest tests/browser -m e2e_browser -v
```

Omitir LD_LIBRARY_PATH quando as bibliotecas ja estao instaladas no sistema. Os marcadores
`browser` e `e2e_browser` ficam ambos excluidos por omissao, a par de `live`. Nao correr
em paralelo com outro pytest na mesma base. Playwright e Chromium ja estavam instalados;
esta tarefa nao exige novas dependencias ou alteracao de uv.lock.
