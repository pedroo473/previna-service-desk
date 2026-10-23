# Previna Service Desk — Etapa 1: arquitetura e modelo de dados

## Premissas adotadas

1. Produção em Linux com Docker Compose, Nginx na frente e Gunicorn servindo Flask.
2. Microsoft Entra ID via OIDC é o provedor preferido em produção; login local fica limitado a desenvolvimento e contingência explicitamente habilitada. Authlib fará o fluxo OIDC.
3. O serviço de e-mail será encapsulado: Microsoft Graph para Microsoft 365 quando houver app registration/autorização, ou SMTP relay interno quando essa for a política da empresa.
4. Calendário inicial: `America/Sao_Paulo`, segunda a sexta, 08:00–18:00; feriados e exceções locais são cadastráveis, não inferidos automaticamente.
5. Um tenant/empresa, um grupo de suporte inicial, aproximadamente 200 chamados/mês e 20 solicitantes.

## Decisões arquiteturais

- **Monólito modular Flask**: é suficiente para o volume informado, reduz o custo operacional e mantém portal, console e relatórios no mesmo domínio de negócio. Os módulos são separados por blueprint e serviço; não começar com microserviços.
- **Application factory + blueprints**: a configuração vem do ambiente; extensões são inicializadas com `init_app`; testes conseguem criar aplicações com configurações isoladas.
- **SQLAlchemy 2.x puro + Alembic**: os modelos usam `DeclarativeBase`, `Mapped` e `mapped_column`; a sessão é gerenciada por request/serviço. Isso evita acoplar a camada de domínio à extensão Flask-SQLAlchemy. Alembic é a única via para evolução de schema em ambientes implantados.
- **PostgreSQL**: transações, índices, constraints, `JSONB`, bloqueios e UPSERT para numeração concorrente. SQLite fica apenas no protótipo legado e em testes isolados, não como banco de produção.
- **Jinja2 + HTMX + Bootstrap 5.3 locais**: renderização no servidor, fragmentos HTML para filtros/status/comentários, sem SPA nem CDN. Bootstrap e HTMX ficam em `app/static/vendor/` e são versionados no projeto.
- **RBAC global + autorização por fila + autorização por objeto**: as roles indicam capacidades gerais; `queue_memberships` restringe filas. Em toda consulta de solicitante, o `requester_id` deve ser filtrado no servidor. Nunca confiar em IDs, campos ocultos ou botões escondidos na UI.
- **Audit trail append-only**: a aplicação não oferece edição ou exclusão de eventos de auditoria. Na Etapa 2, Alembic também criará trigger PostgreSQL que rejeita `UPDATE`/`DELETE` em `audit_events` e as permissões de banco serão separadas. O listener ORM do modelo é proteção adicional, não substitui o controle no banco.
- **Anexos fora do webroot**: banco guarda metadados, chave aleatória, hash SHA-256, MIME detectado e tamanho. O conteúdo fica em volume privado, com download por endpoint que revalida autorização do chamado.
- **Numeração `CH-AAAA-NNNNNN`**: `ticket_counters` mantém um contador por ano. Serviço usa UPSERT atômico PostgreSQL para reservar o próximo número dentro da transação; não utilizar `MAX(ticket_number)+1`, que apresenta condição de corrida.
- **Prioridade calculada** pela matriz impacto × urgência; solicitante não escolhe diretamente P1–P4. A matriz pode virar configuração administrativa numa evolução posterior.
- **Outbox transacional de e-mail**: a mudança do chamado e a inclusão do e-mail pendente são confirmadas na mesma transação. Isso evita perder notificação entre commit e envio; worker envia, marca resultado e faz retry sem duplicar por `idempotency_key`.
- **APScheduler em processo/contêiner separado**: para 200 chamados/mês, RQ + Redis adicionaria infraestrutura sem necessidade inicial. O web process não executa jobs; há somente um scheduler ativo, com proteção transacional/lease no banco e tarefas idempotentes. Se forem necessárias filas de alta concorrência ou processamento pesado, migrar para RQ será uma decisão explícita.
- **Segurança**: Flask-WTF `CSRFProtect` global, autoescape Jinja, Markdown sanitizado por allowlist, ORM/consultas parametrizadas, limite e inspeção de upload, rate-limit no login, sessões Secure/HttpOnly/SameSite em HTTPS, CSP com assets locais, `X-Content-Type-Options`, `frame-ancestors` e HSTS apenas quando HTTPS estiver garantido.

## Estrutura-alvo na mesma pasta do projeto existente

A estrutura será criada dentro da raiz atual `E:\Downloads\previna_chamados_mvp\previna_chamados`; não será necessário abrir um projeto ou trocar de diretório a cada etapa. O `app.py` monolítico atual só será substituído na Etapa 2, após backup do banco legado.

```text
previna_chamados/
├── app/
│   ├── __init__.py                 # create_app(config=None)
│   ├── config.py                   # configuração por ambiente
│   ├── extensions.py               # CSRF, LoginManager, limiter e engine/session
│   ├── models/
│   │   ├── __init__.py             # registra todos os modelos para Alembic
│   │   ├── base.py                 # Base, mixins, enums e tipos comuns
│   │   ├── identity.py             # usuário, role, permissão, equipe e fila
│   │   ├── itsm.py                 # catálogo, SLA/calendário, KB, macros e filtros
│   │   └── ticket.py               # chamado, comentários, SLA aplicado, auditoria
│   ├── blueprints/
│   │   ├── auth/                   # login OIDC, callback, logout e conta
│   │   ├── portal/                 # meus chamados, abertura, resposta e CSAT
│   │   ├── technician/             # filas, ticket detail, status, macros e tempo
│   │   ├── knowledge/              # pesquisa e artigos
│   │   ├── management/             # KPIs, CSV/XLSX e administração
│   │   └── notifications/          # inbox e leitura de notificações
│   ├── services/
│   │   ├── authorization.py        # RBAC, fila e autorização por objeto
│   │   ├── ticketing.py            # numeração, criação, reabertura e transições
│   │   ├── sla.py                  # calendário, metas, pausas e violações
│   │   ├── notifications.py        # notificações internas + outbox
│   │   ├── mail.py                 # adaptadores Graph/SMTP
│   │   ├── attachments.py          # validação, armazenamento e downloads seguros
│   │   └── reporting.py            # agregações e exportações
│   ├── templates/                  # Jinja2 por blueprint + base.html
│   └── static/
│       ├── css/app.css
│       ├── js/app.js
│       └── vendor/                 # Bootstrap, HTMX, ícones e fontes locais
├── migrations/                     # Alembic
├── tests/                          # pytest: RBAC, objeto, SLA e transições
├── instance/                       # configuração local somente desenvolvimento
├── storage/                        # volume privado de anexos (fora de static/)
├── worker.py                       # processo dedicado APScheduler
├── wsgi.py                         # exporta app para Gunicorn
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example                    # apenas nomes/valores fictícios
├── .dockerignore
├── nginx/
│   └── default.conf
└── README.md
```

## Módulos e entidades

Os modelos abaixo cobrem os domínios funcionais planejados. Regras de fluxo, cálculo do SLA, autorização e disparo de notificações pertencem aos serviços transacionais, não a hooks de interface.

### Identidade e autorização

- `User`: identificador interno estável, nome, e-mail normalizado, provedor/subject externo, hash de senha somente quando o provedor for local e status ativo.
- `Role`, `Permission`, `user_roles`, `role_permissions`: RBAC global para `requester`, `technician`, `supervisor`, `admin` e permissões específicas.
- `Team`, `TeamMembership`, `Queue`, `QueueMembership`: agrupamento e concessões por fila (`can_view`, `can_assign`, `can_manage`, `can_view_internal_notes`).

### Catálogo e SLA

- `Category`, `CategoryField`: árvore categoria/subcategoria e formulário dinâmico; valores respondidos são armazenados em `Ticket.custom_fields` após validação no servidor.
- `BusinessCalendar`, `BusinessHour`, `Holiday`: fuso, janelas por dia e feriados nacionais/estaduais/municipais/da empresa.
- `SLAPolicy`, `SLATarget`: perfil de SLA e metas por prioridade; as metas aplicadas são copiadas para `TicketSLA` para preservar o histórico se a política mudar.

### Chamados e operação

- `Ticket`, `TicketCounter`: identificador UUID interno e número humano por ano, prioridade P1–P4, impacto/urgência, fila, categoria, solicitante, técnico, dados dinâmicos, resolução e mesclagem.
- `TicketComment`: comentário público ou nota interna; conteúdo Markdown é sanitizado na renderização.
- `TicketAttachment`: metadados de arquivos armazenados privadamente.
- `TicketStatusHistory`: transições de status e motivo; sustenta a timeline e mede reaberturas/pausas.
- `TicketSLA`: metas, prazos, cumprimento/violação e estado de pausa por chamado.
- `TicketFollower`, `TimeEntry`, `SatisfactionSurvey`: seguidores, tempo trabalhado e avaliação por ciclo de resolução.
- `Notification`, `EmailOutbox`: inbox interna e entrega de e-mail resiliente/idempotente.
- `AuditEvent`: trilha append-only dos eventos relevantes e dos valores antes/depois.

### Conhecimento e produtividade

- `KnowledgeArticle`, `KnowledgeFeedback`: artigos publicados, visibilidade e feedback de utilidade.
- `Macro`: respostas prontas por fila ou compartilhadas.
- `SavedFilter`: filtros pessoais ou compartilhados, armazenados como JSON validado.

## Regras que serão codificadas nos serviços

### Matriz inicial de prioridade

| Impacto \ Urgência | Alta | Média | Baixa |
|---|---:|---:|---:|
| Alto | P1 | P2 | P3 |
| Médio | P2 | P3 | P4 |
| Baixo | P3 | P4 | P4 |

A matriz é uma premissa inicial para validar com a Previna antes da entrada em produção. O serviço valida os valores de impacto/urgência e calcula a prioridade; o frontend não tem autoridade para atribuir qualquer prioridade.

### Estados e transições

- `new`: criado, ainda não iniciado.
- `in_progress`: técnico atuando.
- `waiting_user`: aguardando solicitante; pausa apenas o relógio de SLA configurado para pausa.
- `waiting_third_party`: aguardando fornecedor/terceiro; por padrão não pausa SLA até aprovação da política.
- `resolved`: solução entregue; pode ser reaberto conforme regra.
- `closed`: encerramento definitivo após N dias configuráveis sem resposta do solicitante, sem apagar histórico.
- `canceled`: cancelamento autorizado e auditado.

Transições inválidas devem retornar erro de domínio e não alterar dados. Toda transição registra `TicketStatusHistory` e `AuditEvent` na mesma transação.

## Modelo de dados

O diagrama ER completo está em `docs/er_diagram.mmd`; renderizável por Mermaid. Os modelos Python correspondentes estão em `app/models/` e usam SQLAlchemy 2.x tipado (`Mapped`, `mapped_column`), com `JSONB` no PostgreSQL e variante `JSON` para testes de portabilidade. Referência oficial: documentação SQLAlchemy 2.x de mapeamento declarativo tipado.

## Etapa 2 — escopo previsto (ainda não executar automaticamente)

1. Adicionar a `application factory`, config por ambiente e blueprints ao projeto atual, na mesma pasta.
2. Instalar extensões; inicializar `CSRFProtect` globalmente e `LoginManager`; configurar Authlib OIDC.
3. Configurar SQLAlchemy/engine/session e Alembic; migration inicial dos 32 modelos.
4. Criar seed idempotente dos quatro roles, permissões, calendário padrão, política e metas P1–P4.
5. Adicionar trigger PostgreSQL que bloqueia `UPDATE`/`DELETE` em `audit_events`.
6. Manter backup do SQLite legado e definir/exportar os dados do protótipo antes de apontar a aplicação nova para PostgreSQL.
7. Não abrir acesso de rede até validar autenticação, TLS, CSRF, RBAC e autorização por objeto.
