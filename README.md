# Previna — Service Desk

Aplicação de suporte em Flask + SQLite. Esta entrega consolida a Fase 0: indicadores consistentes, filtros por período, notificações, acessibilidade básica e isolamento de dados demonstrativos.

## Funcionalidades

- Login com senha em hash; solicitantes veem somente os próprios chamados e a equipe de TI vê a fila.
- Abertura de chamados, comentários, atualização de status por atendente e registro de histórico em comentários.
- Indicadores mutuamente exclusivos: Total, Abertos, Em andamento, Aguardando usuário e Resolvidos/encerrados.
- Filtro único de período aplicado aos KPIs e às contagens; lista paginada no servidor (25 registros por página).
- Cards clicáveis e contagens por status.
- Sino com contador e dropdown das notificações recentes, página completa, toasts e aviso sonoro após interação do usuário com o navegador.
- Atalhos configuráveis da base de conhecimento, contato do plantão e horário de suporte.
- Proteção CSRF, consultas SQL parametrizadas, cookies HTTP-only, foco visível por teclado e autorização do lado do servidor.
- Limite de tentativas de login com contagem persistida na auditoria: bloqueio temporário por conta/IP.
- Novos timestamps são gravados em UTC explícito; timestamps legados sem fuso são interpretados como horário de São Paulo e não são regravados.
- Migração SQLite aditiva: tabelas novas e colunas demonstrativas não apagam os chamados ou usuários existentes.

## Instalação e início no Windows

Requisitos: Windows 10/11 ou Windows Server compatível e Python 3.11+.

1. Execute \`INICIAR_LOCAL.bat\` ou abra PowerShell na pasta e rode:
   \`\`\`powershell
   .\.venv\Scripts\python.exe app.py
   \`\`\`
2. Acesse \`http://127.0.0.1:5000\`.
3. Não existe senha padrão. Crie o primeiro usuário de TI:
   \`\`\`powershell
   .\.venv\Scripts\python.exe -m flask --app app:create_app create-user
   \`\`\`
   Use um nome de usuário, nome completo, perfil \`ti\` e uma senha com pelo menos 10 caracteres. Para criar um solicitante, execute o mesmo comando e selecione o perfil \`solicitante\`.

Para listar contas sem expor hashes:
\`\`\`powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app list-users
\`\`\`

## Testes automatizados

\`\`\`powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
\`\`\`

A suíte cobre permissões, login, comentários, filtros e notificações. Inclui a regra crítica: 1 chamado Em andamento + 1 Encerrado = Total 2, Abertos 0, Em andamento 1, Aguardando usuário 0 e Resolvidos/encerrados 1.

## Dados de demonstração — isolados por padrão

O sistema não cria dados fictícios automaticamente. Para demonstrar, use **um arquivo SQLite exclusivo**, diferente do banco real, e defina as variáveis somente nessa sessão PowerShell:

\`\`\`powershell
$env:PREVINA_DATABASE = "$PWD\instance\demonstracao.sqlite3"
$env:DEMO_MODE = "true"
$env:DEMO_SEED_PASSWORD = "substitua-por-uma-senha-forte"
.\.venv\Scripts\python.exe -m flask --app app:create_app seed-demo-data
\`\`\`

A senha de demonstração precisa ter pelo menos 10 caracteres. Não habilite \`DEMO_MODE=true\` com o banco de produção. No ambiente de demonstração, a limpeza dos registros marcados é feita com backup automático antes de apagar:

\`\`\`powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app clean-demo-data
\`\`\`

O comando de seed e o de limpeza falham quando \`DEMO_MODE\` não está habilitado. A limpeza remove apenas registros marcados como demonstrativos e cria um backup \`*.bak_AAAAMMDD_HHMMSS_ffffff\`. Preserve esse backup até confirmar que não é necessário restaurar.

## Configuração

- \`PREVINA_DATABASE\`: caminho completo do banco SQLite; use banco separado para demonstração.
- \`FLASK_SECRET_KEY\`: segredo persistente definido pelo administrador. Em desenvolvimento, se ausente, é gerado em \`instance/.secret_key\`; em produção é obrigatório defini-lo no ambiente.
- \`PREVINA_ENV=production\`: ativa configuração de produção; exige \`FLASK_SECRET_KEY\` e habilita cookies Secure por padrão. A aplicação deve ser servida por HTTPS.
- \`HOST\` e \`PORT\`: host/porta na inicialização pelo \`app.py\`; padrão \`127.0.0.1:5000\`.
- \`DEMO_MODE\`: habilita seed e limpeza demonstrativos somente com valor verdadeiro.
- \`DEMO_SEED_PASSWORD\`: senha inicial dos usuários demonstrativos.
- \`SUPPORT_KNOWLEDGE_BASE_URL\`: URL configurável da base de conhecimento.
- \`SUPPORT_ON_CALL_URL\`: URL configurável para contato do plantão.
- \`SUPPORT_HOURS_TEXT\`: texto com horário oficial do suporte.
- \`SESSION_COOKIE_SECURE=true\`: habilitar atrás de HTTPS.

## Segurança, limite de login e timestamps

O limite atual bloqueia uma conta após 5 falhas em 15 minutos e bloqueia um IP após 30 falhas na mesma janela. A contagem usa a tabela de auditoria existente, portanto não exige nova tabela. Em ambiente com proxy reverso, confirme que o endereço remoto recebido pela aplicação representa corretamente o cliente; não confie em cabeçalhos encaminhados arbitrariamente.

Os timestamps novos são gravados em UTC com offset explícito. Os valores históricos sem offset são interpretados como horário de São Paulo na apresentação e nas comparações temporais, sem conversão em lote do banco. A dependência `tzdata` fornece a base de fusos IANA em ambientes Windows sem banco de fusos do sistema.

## Limites operacionais

O padrão continua escutando em \`127.0.0.1\`. Não exponha diretamente à rede corporativa ou à internet sem servidor WSGI, HTTPS, regras de firewall, backups verificados e teste de implantação. O banco padrão permanece em \`instance/chamados.sqlite3\`. A migração desta fase é aditiva e não apaga usuários nem chamados existentes.

## Perfil, auditoria e notificações por e-mail

- Cada colaborador pode atualizar seu nome e e-mail em **Meu perfil** no menu lateral.
- A equipe de TI pode consultar **Auditoria**, que registra eventos como login, saída, abertura de chamado, comentários, mudança de status e atualização de perfil.
- Notificações internas funcionam sem SMTP. O envio por e-mail é opcional e só ocorre quando as configurações SMTP estão definidas.

Exemplo de configuração PowerShell (não grave senhas SMTP no Git):

```powershell
$env:PREVINA_BASE_URL = "http://127.0.0.1:5000"
$env:PREVINA_SMTP_HOST = "smtp.seu-provedor.com"
$env:PREVINA_SMTP_PORT = "587"
$env:PREVINA_SMTP_USERNAME = "usuario"
$env:PREVINA_SMTP_PASSWORD = "senha-fornecida-pelo-provedor"
$env:PREVINA_SMTP_FROM = "service-desk@empresa.com.br"
$env:PREVINA_SMTP_USE_TLS = "true"
$env:PREVINA_SMTP_USE_SSL = "false"
```

Substitua os valores pelos dados fornecidos pelo administrador de e-mail e reinicie o servidor. Não habilite SSL implícito e TLS simultaneamente.

Para cadastrar ou alterar o e-mail de um usuário pela CLI:

```powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app set-email
```

O status do envio fica salvo na notificação. Falhas SMTP não devem impedir o acesso à notificação dentro do sistema.