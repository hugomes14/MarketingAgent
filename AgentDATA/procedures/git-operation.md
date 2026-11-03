# Procedimento: Git seguro

## Quando carregar

Carregar quando a tarefa envolver branch, stage, commit, pull, push, historico, revisao de diff ou sincronizacao.

## Regras centrais

- Assumir que pode haver outro agente no mesmo worktree.
- Nunca destruir trabalho alheio.
- Nunca usar `git add .` ou `git add -A`.
- Nunca commitar arquivo que nao foi modificado nesta sessao.
- Sempre inspecionar `git status` antes de stage e commit.

## Comandos permitidos

- `git status`
- `git diff`
- `git diff --staged`
- `git log`
- `git show`
- `git blame`
- `git fetch`
- `git pull --rebase`
- `git branch`
- `git switch`
- `git switch -c <branch>`
- `git add <arquivos especificos>`
- `git restore --staged <arquivos especificos>`
- `git commit`
- `git push`

## Comandos proibidos por padrao

Salvo ordem explicita do usuario, nao usar:

- `git reset --hard`
- `git clean -fd`
- `git checkout .`
- `git stash`
- `git stash pop`
- `git commit --no-verify`
- `git push --force`
- `git push --force-with-lease`
- `git rebase -i`
- qualquer operacao que descarte trabalho alheio;
- qualquer operacao que reescreva historico remoto.

## Branch

- Se ja estiver em branch de tarefa, seguir nela.
- Se estiver em `main` e a tarefa for nao trivial, preferir `agent/<timestamp>_<slug>`.
- Nao trocar de branch sem necessidade.
- Nao criar merge commit em `main` por padrao.
- Ao sincronizar com remoto, preferir `pull --rebase`.

## Commit

Commit e obrigatorio ao final de mudanca relevante concluida e validada.

Mensagem:

```text
type(scope): resumo curto no imperativo
```

Tipos preferidos:

- `feat`
- `fix`
- `refactor`
- `docs`
- `test`
- `chore`
- `build`
- `ci`

## Fluxo canonico

```bash
git status
git diff
# editar arquivos
# validar
git status
git add caminho/especifico/arquivo1 caminho/especifico/arquivo2
git diff --staged
git commit -m "docs(agents): organize operational procedures"
git pull --rebase
git push
```

Ignorar arquivos estranhos no status se nao forem da tarefa atual.
