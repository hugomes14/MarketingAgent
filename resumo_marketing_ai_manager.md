# Marketing AI Manager — Resumo do Projeto

## Visão geral

Desenvolver um **agente de inteligência artificial de marketing para uso interno da empresa**, integrado com **Facebook e Instagram**, capaz de apoiar o planeamento, a criação e a publicação de conteúdos, o atendimento a clientes e a análise de resultados.

A solução será **desenvolvida à medida**, privilegiando simplicidade, autonomia e baixo custo operacional. Será alojada inicialmente num **Raspberry Pi 5 com 8 GB de RAM**, usando **APIs da OpenAI e/ou Anthropic** para inferência na cloud, sem executar grandes modelos de linguagem localmente.

## Objetivos e funcionalidades

1. **Planeamento de marketing:** definir campanhas, temas, objetivos e calendário editorial com apoio de IA.
2. **Criação de conteúdos:** gerar textos, ideias, imagens e roteiros para publicações e Reels, respeitando a identidade da marca.
3. **Publicação e agendamento:** preparar e publicar conteúdos no Facebook e Instagram através das APIs oficiais da Meta.
4. **Atendimento automatizado:** receber mensagens e comentários, responder a questões frequentes e encaminhar situações complexas para uma pessoa.
5. **Gestão de contactos:** registar leads, interesses e histórico relevante das interações.
6. **Análise de desempenho:** recolher métricas, elaborar relatórios e recomendar melhorias para campanhas futuras.

## Decisões arquiteturais

- **Aplicação própria no MVP**, sem Postiz e Chatwoot, para reduzir consumo de recursos e adaptar o sistema ao negócio.
- **FastAPI (Python)** como backend, API interna, camada de agentes e integração com serviços externos.
- **PostgreSQL** para campanhas, publicações, contas, mensagens, leads, aprovações e auditoria.
- **Worker/scheduler** para executar tarefas em segundo plano, agendamentos e reenvios; **n8n é opcional** se os workflows visuais forem úteis.
- **Meta Graph API e APIs de mensagens** para integração oficial com Facebook e Instagram, de acordo com as permissões disponíveis.
- **OpenAI API e/ou Anthropic API** para planeamento, criação de conteúdos, classificação e respostas.
- **Interface web própria**, numa fase posterior ou em paralelo, para calendário, aprovações, mensagens e métricas; a forma final de interação ainda está por decidir.
- **Docker Compose** para execução e manutenção no Raspberry Pi.

### Fluxo simplificado

```text
Utilizador / Painel Web
         |
         v
 FastAPI — Marketing AI Manager
   |          |           |
   v          v           v
PostgreSQL  Worker     OpenAI / Anthropic
   |          |
   +----------+
         |
         v
  Meta Graph API / Webhooks
         |
         v
  Facebook e Instagram
```

## Infraestrutura inicial

- Raspberry Pi 5, **8 GB RAM**, sistema operativo **64-bit**.
- **SSD** (preferível a microSD para base de dados e funcionamento contínuo), refrigeração ativa e ligação Ethernet.
- Docker e Docker Compose.
- **HTTPS** para receção de webhooks, por exemplo com Cloudflare Tunnel e domínio configurado.
- Backups externos e gestão segura de credenciais.
- Inferência de IA remota, para preservar CPU e RAM do Raspberry Pi.

> **Limite importante:** executar Postiz, Chatwoot, n8n e todos os serviços associados em simultâneo num Pi de 8 GB não é a opção recomendada. A arquitetura própria e modular reduz a carga.

## Fases de implementação

### Fase 1 — Núcleo e integração
- Preparar Docker, FastAPI, PostgreSQL, tarefas agendadas e HTTPS.
- Configurar autenticação, permissões e webhooks da Meta.
- Validar uma publicação real e a receção de uma mensagem de teste.

### Fase 2 — Marketing e conteúdos
- Definir o perfil da marca, produtos, público-alvo e objetivos.
- Criar os fluxos de planeamento e geração de conteúdos.
- Implementar **aprovação humana** antes da publicação.
- Agendar e publicar no Facebook/Instagram.

### Fase 3 — Atendimento e leads
- Receber e organizar mensagens e comentários.
- Criar respostas com IA baseadas em informação validada da empresa.
- Guardar contactos e oportunidades comerciais.
- Encaminhar pedidos sensíveis ou fora das regras para atendimento humano.

### Fase 4 — Painel e análise
- Criar calendário editorial, inbox e área de aprovações.
- Recolher métricas de desempenho.
- Gerar relatórios e recomendações periódicas.

## Regras e cuidados essenciais

- Respeitar as **permissões, políticas e limites das APIs da Meta**, incluindo regras de resposta a mensagens e requisitos das contas profissionais.
- Verificar assinaturas de webhooks, impedir processamento duplicado de eventos e implementar reenvios controlados.
- Proteger tokens, dados pessoais e histórico dos clientes, em conformidade com o **RGPD**.
- Impor limites de autonomia: publicar após aprovação, não inventar preços ou condições comerciais, e permitir intervenção humana.
- Usar fontes atualizadas para produtos, preços e disponibilidade.
- Prever monitorização, backups e recuperação após falhas de Internet ou energia.

## Código e projetos de referência

Apesar de não serem usados como aplicações principais no MVP, podem servir como referência funcional:

- [Postiz](https://github.com/gitroomhq/postiz-app) — calendário, agendamento e publicação.
- [Chatwoot](https://github.com/chatwoot/chatwoot) — inbox e atendimento multicanal.
- [n8n](https://github.com/n8n-io/n8n) — automação e integração entre sistemas.
- [FastAPI](https://github.com/fastapi/fastapi) — framework para o backend.
- [APScheduler](https://github.com/agronholm/apscheduler) — agendamento de tarefas.
- [HTTPX](https://github.com/encode/httpx) — cliente HTTP para integrações.

## MVP recomendado

O primeiro marco de sucesso é conseguir, com o Raspberry Pi:

1. Pedir ao agente um plano semanal de conteúdos.
2. Gerar propostas de publicações adaptadas à marca.
3. Rever e aprovar os conteúdos.
4. Agendar/publicar em Facebook e Instagram.
5. Registar o estado de cada publicação e tratar falhas.

**Depois do MVP:** adicionar atendimento automatizado, leads, inbox própria e analytics avançados.

## Decisões ainda em aberto

- Setor e necessidades específicas da empresa.
- Se o controlo será feito primeiro por **painel web** ou por **chat/automação**.
- Volume esperado de publicações, mensagens e contactos.
- Se o Raspberry Pi será o servidor permanente ou inicialmente um ambiente de testes.

---

**Síntese:** criar uma plataforma própria, modular e leve para gerir marketing e atendimento com IA, alojada num Raspberry Pi 5 e integrada oficialmente com a Meta. Começar por conteúdos e publicação com aprovação humana; expandir posteriormente para atendimento, CRM e análise de resultados.
