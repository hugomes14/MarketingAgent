# Checklist de prova da autenticacao do painel (para o runbook do Plano 0)

- Timestamp: `2026-11-06T14:26:00+00:00`
- Autor: `agent`
- Assunto: prova, no percurso real, da autenticacao por proxy confiavel (Tarefa 13)
- Estado: **NAO EXECUTADO**. Os testes unitarios (`tests/panel/test_auth.py`) so provam a
  condicao no codigo; a garantia de rede so se prova com `tailscale serve` e Docker reais.

## Preparacao

- [ ] Painel a escutar numa porta local sem publicar essa porta noutra interface do anfitriao.
- [ ] `tailscale serve` a apontar para o painel, na rede Docker partilhada com o `tailscaled`.
- [ ] Registar o IP (ou a CIDR da rede Docker) com que o painel ve o `tailscaled`
      (`request.client.host`) e coloca-lo em `PANEL_TRUSTED_PROXY`.
- [ ] `PANEL_ALLOWED_LOGINS` com o login do dono; confirmar que nao esta vazio.

## Provas e resultado esperado

| # | Pedido | Esperado |
| --- | --- | --- |
| 1 | Do dispositivo do dono, pelo URL do Serve | 200, utilizador reconhecido |
| 2 | De outro contentor da rede Docker, direto a porta do painel, com `Tailscale-User-Login` forjado igual ao do dono | 403 "Acesso negado." |
| 3 | Idem, com `X-Forwarded-For: 127.0.0.1` adicionado | 403 (cabecalho ignorado) |
| 4 | Pela origem publica/Funnel (se existir) | painel inalcancavel; nunca 200 |
| 5 | Pelo Serve, a partir de dispositivo `tagged` | 401 (sem cabecalho de utilizador) |
| 6 | Pelo Serve, com utilizador da tailnet fora de `PANEL_ALLOWED_LOGINS` | 403 "Acesso negado." |
| 7 | Pelo Serve, cliente envia `Tailscale-User-Login` forjado no pedido | Serve substitui/remove; vale a identidade real |
| 8 | Com `PANEL_ALLOWED_LOGINS=[]` | 403 para todos, incluindo o dono |

## Registo

- [ ] Guardar comandos e respostas de cada linha (data, versao do Tailscale, IP do proxy).
- [ ] Confirmar nos logs: so motivo e IP do peer, nunca o login nem a lista.
- [ ] Qualquer desvio bloqueia a aprovacao de perfis, diagnosticos e estrategias pelo painel.
