# Registro de conhecimento

## Metadata

- Timestamp: `2026-11-03T09:59:50+00:00`
- Autor: `agent`
- Assunto: Meta — App Review, Business Verification, Standard Access e Advanced Access
- Status: `ativo`
- Retestar em: `2027-02-03`

## Achado

Uma app Meta usada apenas por pessoas com papel na própria app não precisa de App Review. Advanced Access (utilizadores sem papel) exige Business Verification e App Review. Apps do tipo Business têm Standard Access aprovado automaticamente.

## Contexto

Decidir se o projeto (utilizador único, contas próprias a criar) precisa de submeter App Review ou verificação de negócio, e se o confinamento por Tailscale (D7) colide com o teste da Meta.

## Metodo

`WebFetch` de três páginas oficiais em 2026-11-03: `resp-plat-initiatives/individual-processes/app-review`, `development/release/business-verification` e `graph-api/overview/access-levels`.

## Comandos

```text
Sem comandos executados.
```

## Evidencia

Fonte primária:

- "If your app will only be used by app users who have a role on the app itself, App Review is not required."
- App Review é obrigatório se a app for usada por quem não tem papel na app (nem papel num Business que a reivindicou).
- Permissões não aprovadas só podem ser pedidas a utilizadores com papel na app; funcionalidades não aprovadas só estão ativas para eles.
- "Business Verification is required to get Advanced Access." Também é pedida a apps que dão a outros Businesses acesso aos seus dados. Quem tem papel na app não precisa dela.
- Apps Business, Consumer e Gaming recebem Standard Access aprovado automaticamente para as permissões do seu tipo.
- Advanced Access é aprovado por permissão e funcionalidade, e exige *Data Use Checkup* anual.
- "If we are unable to access your app to test it, your entire submission will be rejected."
- A página de Business Verification não indica documentos, duração nem custo; remete para o Help Center do Business Manager.

Lacunas (a página não responde):

- Se Standard Access funciona em modo **Live** para quem tem papel na app.
- Que submissão exige (screencast, descrições de uso, URL da política de privacidade).

## Codigo relevante

```text
Sem código executado.
```

## Interpretacao

- Facto: ver Evidência.
- Inferência: com utilizador único e contas próprias com papel na app, App Review e Business Verification provavelmente não são necessários.
- Inferência: a fonte secundária do registo `2026-11-02T12-25-26` diz que o webhook de comentários exige Advanced Access. Se for verdade, contradiz a linha anterior e obriga a App Review e Business Verification.
- Inferência: se App Review for necessário, a app confinada à tailnet não pode ser testada pela Meta, e a submissão seria rejeitada. Seria preciso um ambiente de teste acessível à Meta.

## Consequencia operacional

- Fase 0: testar com contas de teste se webhooks de DMs e comentários chegam com Standard Access, em modo Live e em modo Development.
- Só preparar Business Verification e App Review se esse teste falhar.

## Riscos e limites

- Páginas oficiais curtas; as regras de submissão estão noutras secções não consultadas.
- Política de App Review muda com frequência.

## Pendencias

- Ler o tutorial de App Review e o FAQ.
- Confirmar o requisito de URL de política de privacidade e de callback de eliminação de dados.
