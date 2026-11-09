# Plano 02B: armazenamento local preparado em isolamento

- Data: 2026-11-09
- Autor: Codex
- Ramo: `agent/2026-11-09_plano02b_store`
- Base: `9d1be06` do ramo `agent/2026-11-09_plano02a`
- Estado: parte independente da Tarefa 2 implementada; Tarefa 2 e marcos do 2B continuam abertos.

## Escopo e isolamento

O responsavel autorizou adiantar trabalho de 2B/2C/2D sem interferir com Claude no 2A.
Apenas o armazenamento de bytes foi antecipado. O worktree separado fica em
`/tmp/marketing-agent-plano02b-store`. Nao foram alterados ficheiros do worktree do Claude,
o seu indice, os servicos locais, a base de testes ou o ambiente virtual partilhado.
O ramo fica separado, sem merge ou push.

O pre-requisito de fecho do 2A continua a aplicar-se a integracao e aos marcos do 2B.
Este modulo nao depende dos contratos em construcao: so usa a biblioteca padrao de Python.

## Contrato entregue

Ficheiros novos: `src/marketing_agent/media/__init__.py`, `media/store.py` e
`tests/media/test_store.py`.

- `StoredObject` imutavel: `key`, `sha256`, `size`.
- `MediaStore` (Protocol): `put(brand_id, data, extension)` e `read(key)`.
- `LocalMediaStore(root: Path)`: chave `{brand_id}/{sha256[:2]}/{sha256}.{jpg|png}`.
- IDs de marca: 1–128 caracteres ASCII, primeiro alfanumerico; restantes alfanumericos,
  `_` ou `-`. Aceita os UUIDs existentes. Extensao validada tambem em runtime.
- Temporario na mesma pasta, bytes sincronizados antes de publicar com `os.link`.
  Conteudo identico e idempotente; bytes diferentes numa chave existente dao erro sem
  sobrescrever. Concorrencia nao publica ficheiros parciais.
- Pastas criadas dentro da raiz com `0o700`, objetos com `0o600`. Permissoes de pastas
  preexistentes nao sao alteradas. Os antepassados da raiz configurada sao confiaveis.
- Descritores relativos e `O_NOFOLLOW` recusam symlinks na raiz e nos componentes internos.
  Chaves absolutas, travessia, prefixos incoerentes e objetos nao regulares sao recusados.
- `MediaStoreError`: mensagens fixas sem bytes de conteudo; falhas de disco preservam a causa.
- Dependencia de operacao: sistema POSIX com `dir_fd`, `O_NOFOLLOW` e hard links no mesmo
  sistema de ficheiros (o ambiente Linux atual). Nao e um adaptador para Windows.

O armazenamento nao descodifica nem normaliza imagens: essa fronteira continua a pertencer
a `images/codec.py` e `media/ingest.py`, ainda por implementar. Nao foi adicionado Pillow,
configuracao `MEDIA_DIR`, migracao, biblioteca, rota ou wiring no painel.

## Validacao

Usou-se o interpretador existente, sem `uv sync`, e `PYTHONPATH=src:tests/panel` para garantir
que os imports usam o codigo do worktree isolado.

```bash
PYTHONPATH=src:tests/panel /home/marketing-agent/MarketingAgent/.venv/bin/python -m pytest tests/media tests/test_secrets.py tests/test_clock.py -q
/home/marketing-agent/MarketingAgent/.venv/bin/mypy --cache-dir=/tmp/marketing-agent-plano02b-mypy src
/home/marketing-agent/MarketingAgent/.venv/bin/ruff check .
/home/marketing-agent/MarketingAgent/.venv/bin/ruff format --check src/marketing_agent/media tests/media
```

Resultado: 43 testes passaram (31 de armazenamento e 12 de regressao sem base), mypy sem
erros nos 71 ficheiros de codigo, lint global limpo e formatacao dos ficheiros novos limpa.
O teste principal foi observado a falhar antes da implementacao (`NotImplementedError`).

`ruff format --check .` assinala um bloco Python preexistente no plano 02B, linha 333.
Essa diferenca de formatacao ja existia na base e foi deixada intacta: o mesmo documento
tem uma alteracao local no worktree do Claude. Nao e uma falha do codigo novo.
A suite com Postgres nao foi executada para evitar concorrencia com os testes do 2A.

## Integracao posterior

Depois de coordenar com o trabalho do 2A, aplicar o commit deste ramo por cherry-pick.
Nao e necessario fundir todo o ramo de origem: este trabalho so acrescenta os quatro
ficheiros indicados. Continuar a Tarefa 2 com codec, ingestao, Pillow, configuracao e testes
de imagem; depois executar a validacao global numa base de testes exclusivamente disponivel.

Referencias: plano `2026-11-09T14-07-58+00-00_plano_02b_imagem.md`, Tarefa 2;
spec `2026-11-09T14-07-58+00-00_spec_plano_02b_imagem.md`, seccao 4.
