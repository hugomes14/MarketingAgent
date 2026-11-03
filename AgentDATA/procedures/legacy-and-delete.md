# Procedimento: legado e delete seguro

## Quando carregar

Carregar quando a tarefa envolver codigo legado, migracao, desativacao, remocao de arquivos, snapshots, baselines ou limpeza estrutural.

## Legado

- Ler antes de substituir.
- Entender por que existe.
- Nao tratar legado como lixo automaticamente.
- Registrar diferenca importante entre comportamento legado e novo.
- Preferir migracao incremental quando reduzir risco.

## Delete

Nao apagar arquivo por impulso.

Proibido por padrao:

- remover arquivo historico sem necessidade real;
- apagar saida, snapshot, baseline ou pasta inteira para esconder problema;
- apagar coisa apenas para limpar visualmente.

Preferir:

- mover;
- isolar;
- renomear;
- desativar;
- arquivar.

Se o repositorio tiver politica de `Archive/`, `deprecated/`, `legacy/` ou similar, seguir a convencao existente.

## Validacao

Antes de remover ou migrar, revisar impacto, contratos, testes e referencias. Depois, rodar validacao proporcional e revisar diff com cuidado.
