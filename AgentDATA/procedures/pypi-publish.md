# Procedimento: publicacao PyPI

## Quando carregar

Carregar somente quando a tarefa envolver pacote Python, release, `twine upload`, TestPyPI, PyPI, Trusted Publishing ou validacao de distribuicao Python.

## Fontes oficiais

- Python Packaging User Guide: https://packaging.python.org/
- PyPI Trusted Publishers: https://docs.pypi.org/trusted-publishers/

## Regras centrais

- Publicacao e operacao de release rastreavel, nao simples upload.
- Publicar somente depois de validacao, diff revisado e commit coerente.
- Nunca commitar token PyPI, senha, cookie, chave privada ou arquivo `.pypirc` com segredo.
- Preferir Trusted Publishing em CI quando o projeto suportar esse fluxo.
- Para token manual, preferir token limitado ao projeto, nao token global.
- Nunca tentar sobrescrever versao ja publicada: PyPI nao permite reutilizar a mesma versao.

## Versionamento

- Usar versao valida conforme o ecossistema Python e o backend do projeto.
- Conferir versao em `pyproject.toml`, README, docs e metadados de citacao quando existirem.
- Incrementar versao antes de publicar nova distribuicao.
- Garantir que nome do pacote, import package e metadata nao estejam inconsistentes.

## Validacao canonica

Rodar a partir da pasta do pacote:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

Rodar tambem a validacao do projeto quando existir:

```bash
python -m pytest
python -m pip check
```

Se houver lint, typecheck ou format check configurado, rodar os comandos do projeto antes do build.

## Smoke local de artefato

Validar instalacao em ambiente limpo antes do upload:

```bash
tmpdir=$(mktemp -d /tmp/pypi-install-XXXXXX)
python -m venv "$tmpdir/.venv"
"$tmpdir/.venv/bin/python" -m pip install --upgrade pip
"$tmpdir/.venv/bin/python" -m pip install dist/*.whl
"$tmpdir/.venv/bin/python" -c "import <modulo>; print(<modulo>.__name__)"
```

No Windows PowerShell, adaptar para:

```powershell
$tmpdir = New-Item -ItemType Directory -Force -Path ([System.IO.Path]::Combine($env:TEMP, "pypi-install-" + [System.Guid]::NewGuid()))
python -m venv "$tmpdir\.venv"
& "$tmpdir\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$tmpdir\.venv\Scripts\python.exe" -m pip install dist\*.whl
& "$tmpdir\.venv\Scripts\python.exe" -c "import <modulo>; print(<modulo>.__name__)"
```

## TestPyPI

Quando for primeiro release, mudanca de packaging ou risco operacional relevante, validar em TestPyPI:

```bash
python -m twine upload --repository testpypi dist/*
python -m pip install --index-url https://test.pypi.org/simple/ --no-deps <nome-do-pacote>
```

Se dependencias tambem precisarem vir do PyPI real durante o smoke, usar `--extra-index-url https://pypi.org/simple/` com cuidado e registrar a diferenca.

## Publicacao PyPI

Com token manual:

```bash
TWINE_USERNAME=__token__ TWINE_PASSWORD=<pypi-token> python -m twine upload dist/*
```

Com Trusted Publishing em CI, configurar o publisher no PyPI e usar workflow com permissao OIDC. Nao guardar token persistente no repositorio.

## Confirmacao depois do publish

```bash
python -m pip index versions <nome-do-pacote>
python -m pip install --upgrade <nome-do-pacote>
python -c "import <modulo>; print(<modulo>.__name__)"
```

## Erros comuns

- Versao ja existe: incrementar versao; nao tentar sobrescrever.
- Metadata invalida: corrigir `pyproject.toml`, reconstruir e rodar `twine check`.
- Token sem permissao: usar token de projeto correto ou Trusted Publishing.
- Nome indisponivel: escolher nome valido e disponivel antes do release.
- Artefato antigo em `dist/`: limpar somente os artefatos da build atual com cuidado e reconstruir.
